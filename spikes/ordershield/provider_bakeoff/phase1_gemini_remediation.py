"""Gemini-only remediation: reuse verified Lite h01, at most 19 new attempts."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error

import phase1 as p

CALLS = p.ROOT / 'phase1_gemini_remediation_calls.json'
SUMMARY = p.ROOT / 'phase1_gemini_remediation_summary.json'
METADATA = p.ROOT / 'phase1_gemini_remediation_metadata.json'
REPORT = p.ROOT / 'phase1_gemini_remediation.md'
CANDIDATES = (p.CANDIDATES[0], p.CANDIDATES[2])
LIMIT = 19
DIAGNOSTIC = p.ROOT / 'gemini_diagnostic.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def protected():
    outputs = (CALLS, SUMMARY, METADATA, REPORT)
    excluded = set(outputs) | {x.with_suffix(x.suffix + '.tmp') for x in outputs}
    return {str(x.relative_to(p.CORPUS)): p.sha(x) for x in p.CORPUS.rglob('*')
            if x.is_file() and x not in excluded and '__pycache__' not in x.parts}


def inputs(fixture):
    source_path = p.CORPUS / 'live/fixtures' / (fixture + '.txt')
    source = source_path.read_text(encoding='utf-8')
    catalog = p.read(p.CORPUS / 'catalog.json')
    truth = p.read(p.CORPUS / 'live/expected.json')[fixture]
    text = p.request_text(source, catalog, (p.CORPUS / 'prompt.txt').read_text(encoding='utf-8'))
    return source_path, source, catalog, truth, text


def apply_score(row, source, truth, catalog):
    final = row['raw_provider_output']
    score, evidence, errors = p.evaluate(final, source, truth, catalog)
    row.update(score=score, evidence_grounding=evidence, error_classes=errors,
               schema_valid=score['schema_valid'], json_parsed='MALFORMED_JSON' not in errors,
               raw_quote_audit=p.raw_quote_audit(final, source))
    finalize_fields(row, truth)


def finalize_fields(row, truth):
    row['error_class'] = row['error_classes'][0] if row['error_classes'] else None
    row['mandatory_field_correctness'] = row['score']['field_checks']
    row['determinate_sku_correctness'] = row['score']['sku_correct'] if truth['sku'] is not None else None
    row['ambiguity_review_correctness'] = row['score']['review_success'] if truth['needs_sku_review'] else None
    row['wrong_confident_sku'] = row['score']['wrong_confident']


def verify_reuse():
    verified = p.integrity()
    data = p.read(DIAGNOSTIC)
    eligible = [r for r in data['calls'] if r['requested_model_id'] == CANDIDATES[0]['model']
                and r['fixture_id'] == 'h01_paper' and r['path_variant'] == 'corrected']
    if len(eligible) != 1:
        raise ValueError('Exactly one corrected h01 diagnostic must exist')
    old = eligible[0]
    source_path, source, catalog, truth, text = inputs('h01_paper')
    request = p.build_request(CANDIDATES[0], text, {'GEMINI_API_KEY': 'OFFLINE_SENTINEL'})
    body = json.loads(request.data)
    checks = {
        'exact_frozen_fixture_and_inputs': verified == data['integrity_after'],
        'exact_corrected_phase1_body': hashlib.sha256(request.data).hexdigest() == old['request_body_sha256'],
        'unchanged_harness_and_scorer': old['harness_sha256'] == p.sha(p.ROOT / 'phase1.py'),
        'unchanged_schema': body['generationConfig']['responseFormat']['text']['schema'] == data['schema_audit']['full_schema'] == p.SCHEMA,
        'intended_minimal_configuration': old['configuration'] == p.configuration(CANDIDATES[0]) and body['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'minimal'},
        'completed_http_success_and_schema': old['state'] == 'COMPLETED' and old['http_status'] == 200 and old['schema_valid'] and old['json_parsed'],
    }
    score, evidence, errors = p.evaluate(old['raw_provider_output'], source, truth, catalog)
    checks['identical_recomputed_score_and_grounding'] = score == old['score'] and evidence == old['evidence_grounding'] and errors == old['error_classes']
    if not all(checks.values()):
        raise ValueError('h01 reuse conditions not satisfied; no model call made')
    row = copy.deepcopy(old)
    row.update(provider=CANDIDATES[0]['provider'], run_index=1, origin='REUSED_CORRECTED_DIAGNOSTIC',
               new_live_attempt=False, provider_outcome='HTTP_SUCCESS', source_sha256=p.sha(source_path),
               reused_from={'artifact': DIAGNOSTIC.name, 'artifact_sha256': p.sha(DIAGNOSTIC),
                            'diagnostic_call_index': old['call_index'], 'original_started_utc': old['started_utc']})
    apply_score(row, source, truth, catalog)
    return row, checks


def verify_qwen_comparability():
    previous = [r for r in p.read(p.CALLS)['calls'] if r['requested_model_id'] == 'qwen3.8-flash']
    if len(previous) != 10:
        raise ValueError('Expected ten recorded Qwen outputs for offline comparison')
    for row in previous:
        _, source, catalog, truth, _ = inputs(row['fixture_id'])
        score, evidence, errors = p.evaluate(row['raw_provider_output'], source, truth, catalog)
        if (score != row['score'] or evidence != row['evidence_grounding']
                or errors != row['error_classes'] or p.raw_quote_audit(row['raw_provider_output'], source) != row['raw_quote_audit']):
            raise ValueError('Recorded Qwen scoring does not reproduce exactly')
    return {'recorded_calls_rescored_offline': 10, 'identical_scores_grounding_and_errors': True,
            'source_artifact': p.CALLS.name, 'source_sha256': p.sha(p.CALLS), 'new_qwen_calls': 0}


def summarize(rows, metadata):
    truth = p.read(p.CORPUS / 'live/expected.json')
    result = p.summarize(rows, truth)
    result['candidates'] = [c for c in result['candidates'] if c['requested_model'] in {x['model'] for x in CANDIDATES}]
    result.update(scope='GEMINI_PHASE_1_REMEDIATION', planned_calls=20, max_new_live_calls=LIMIT,
                  evaluated_calls=len(rows), new_live_attempts=sum(r['new_live_attempt'] for r in rows),
                  reused_calls=sum(not r['new_live_attempt'] for r in rows),
                  attempted_calls_definition='Evaluated single attempts, including one prior corrected h01 diagnostic; new_live_attempts is the new network-call count',
                  stop_reason=metadata['stop_reason'], evidence_interpretation={
                      'ORIGINAL_GEMINI_PHASE_1': 'Invalid harness request; not model-quality evidence. Preserved unchanged.',
                      'GEMINI_REMEDIATION': 'Corrected enum request path. Returned outputs are model-quality evidence; provider/transport failures remain operational failures with zero scoring credit.',
                      'QWEN_PHASE_1': 'Existing evidence unchanged; zero new Qwen calls; same schema, expected labels, scorer and grounding semantics verified offline.'})
    for c in result['candidates']:
        relevant = [r for r in rows if r['requested_model_id'] == c['requested_model']]
        done = [r for r in relevant if r['state'] == 'COMPLETED']
        c['new_live_attempts'] = sum(r['new_live_attempt'] for r in relevant)
        c['reused_calls'] = sum(not r['new_live_attempt'] for r in relevant)
        complete = len(done) == 10
        c['hard_semantic_gates']['all_evaluated_outputs_schema_valid'] = all(r['schema_valid'] for r in done) if complete else None
        c['all_hard_gates_pass'] = all(c['hard_semantic_gates'].values()) if complete else None
        c['evidence_model_quality'] = 'CORRECTED_GEMINI_REMEDIATION'
    return result


def render(summary, metadata):
    def rate(x):
        return f"{x['numerator']}/{x['denominator']} ({100*x['rate']:.1f}%)" if x['rate'] is not None else 'unmeasured'
    lines = ['# OrderShield Gemini Phase-1 remediation', '',
        f"New live attempts: **{summary['new_live_attempts']}/{LIMIT}**. Evaluated results: **{summary['evaluated_calls']}/20**, including **{summary['reused_calls']}** verified corrected Flash-Lite h01 diagnostic. No Qwen calls, retries, substitutions, winner, weighted score, or Phase 2.", '',
        '**ORIGINAL GEMINI PHASE 1 = invalid harness request; not model-quality evidence.** Those immutable results rejected the MIME string before inference.', '',
        '**GEMINI REMEDIATION = corrected request path and valid model-quality evidence for returned outputs.** Provider/transport failures remain operational failures. The shared MIME enum is APPLICATION_JSON; all frozen schema and scorer semantics remain unchanged.', '',
        '| Candidate | Completed / attempted | New / reused | Schema | Mandatory fields | Determinate SKU | Review | Wrong confident | Evidence/null validity | p50 ms | p95/max ms* | Min/max ms |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for c in summary['candidates']:
        d = c['latency_all_attempts'] or {}
        lines.append(f"| {c['requested_model']} | {c['completed_calls']}/{c['attempted_calls']} | {c['new_live_attempts']}/{c['reused_calls']} | {rate(c['schema_valid_rate'])} | {rate(c['mandatory_field_accuracy'])} | {rate(c['determinate_sku_accuracy'])} | {rate(c['ambiguity_review_accuracy'])} | {c['wrong_confident_sku_count']} | {rate(c['evidence_grounding_rate'])} | {d.get('p50_ms', '—')} | {d.get('p95_ms', '—')} | {d.get('min_ms', '—')}/{d.get('max_ms', '—')} |")
    lines += ['', '*With ten observations nearest-rank p95 equals the observed maximum; it is not a reliable population tail estimate. HTTP wall latency includes the reused diagnostic’s original measurement.', '',
              '## h05–h09', '', '| Candidate | Fixture | HTTP | Schema | Fields / 8 | Proposed SKU | Review correct | Wrong confident | Grounding | Error |', '|---|---|---|---|---|---|---|---|---|---|']
    for c in summary['candidates']:
        for fixture in ('h05_no_size', 'h06_no_pack', 'h07_not_medium', 'h08_not_large', 'h09_latex'):
            r = c['per_fixture'].get(fixture)
            if not r:
                lines.append(f"| {c['requested_model']} | {fixture} | unattempted | — | — | — | — | — | — | — |")
            else:
                lines.append(f"| {c['requested_model']} | {fixture} | {r['http_status']} | {r['schema_valid']} | {r['mandatory_fields_correct']} | {r['proposed_sku']} | {r['review_success']} | {r['wrong_confident_sku']} | {r['all_evidence_grounded']} | {', '.join(r['errors']) or 'PASS'} |")
    lines += ['', '## Gates and usage', '']
    for c in summary['candidates']:
        lines += [f"- **{c['requested_model']}**: all seven hard gates pass={c['all_hard_gates_pass']}; gates={json.dumps(c['hard_semantic_gates'])}.",
                  '  Provider failures: ' + json.dumps(c['provider_failures_by_class']) + '; all error classes: ' + json.dumps(c['all_failures_by_class']) + '.',
                  '  Token totals and averages per exposed call: ' + json.dumps(c['token_usage']) + '.',
                  '  Returned verbatim snippets: ' + rate(c['returned_verbatim_snippets']) + '; non-null grounded coverage: ' + rate(c['nonnull_evidence_coverage']) + '.',
                  '  Strict description mismatches: ' + json.dumps(c['strict_description_mismatches']) + '.']
    lines += ['', '## Method and preservation', '',
        'h01 reuse verified exact frozen inputs, corrected request-body hash, minimal configuration, unchanged harness/schema, and identical recomputed score and grounding. Provenance records the diagnostic artifact hash and call index 2; original latency and token usage are retained.',
        'Candidate-major order: Flash-Lite h02–h10, then 3.8 h01 and h02–h10 only after h01 HTTP 200 plus JSON/schema validity. Each new attempt is reserved durably before send. Existing remediation artifacts prevent restart or selective retry. Model-quality errors do not change requests or scoring.',
        'Qwen’s ten stored responses reproduce exactly the same scores, errors, and grounding offline. No Qwen model is called. Frozen 0.90 threshold / 0.15 margin, field comparison, and punctuation-safe literal grounding are unchanged. AI receives only frozen synthetic prompt/catalog/fixture; arithmetic and decisions are local.',
        'Failed/invalid outputs receive zero scoring credit. Unattempted results are unmeasured and prevent a complete hard-gate assessment. Empty quotes on correctly missing null fields are valid missingness, not positive evidence. Every returned literal quote is also audited even after schema rejection.',
        'Free-only account settings rely on the prior explicit user confirmation. Accepted calls do not establish remaining quota. Ten fixtures and uncalibrated confidence scores do not establish broad reliability.',
        f"Stop reason: {metadata['stop_reason']}. Protected prior evidence/corpus/harness hashes unchanged: {metadata.get('protected_evidence_preserved', True)}.", '']
    if metadata.get('checks'):
        lines += ['## Verification', '', *['- ' + x for x in metadata['checks']], '']
    return '\n'.join(lines)


def checkpoint(rows, metadata, secrets):
    if protected() != metadata['protected_sha256_before']:
        raise ValueError('Protected evidence or evaluator changed; stopping before further calls')
    metadata['protected_evidence_preserved'] = True
    metadata['new_live_attempts'] = sum(r['new_live_attempt'] for r in rows)
    metadata['evaluated_calls'] = len(rows)
    p.write(CALLS, {'scope': 'GEMINI_PHASE_1_REMEDIATION', 'calls': rows}, secrets)
    summary = summarize(rows, metadata)
    p.write(SUMMARY, summary, secrets)
    p.write(METADATA, metadata, secrets)
    p.write(REPORT, render(summary, metadata), secrets)


def new_call(cfg, fixture, send, reserve, env, secrets):
    if cfg not in CANDIDATES:
        raise ValueError('Only the two authorized Gemini configurations are allowed')
    path, source, catalog, truth, text = inputs(fixture)
    request = p.build_request(cfg, text, env)
    row = {'provider': cfg['provider'], 'requested_model_id': cfg['model'], 'returned_model_id': None,
           'configuration': p.configuration(cfg), 'fixture_id': fixture, 'run_index': 1,
           'origin': 'NEW_GEMINI_REMEDIATION_ATTEMPT', 'new_live_attempt': True,
           'state': 'ATTEMPT_STARTED', 'started_utc': now(), 'source_sha256': p.sha(path),
           'request_body_sha256': hashlib.sha256(request.data).hexdigest(),
           'http_status': None, 'provider_outcome': None, 'finish_reason': None,
           'schema_valid': False, 'json_parsed': False, 'raw_provider_output': None,
           'evidence_grounding': None, 'raw_quote_audit': None, 'token_usage': None,
           'wall_clock_ms': None, 'error_class': None, 'error_classes': [],
           'score': p.scorer.score_response(None, source, truth, catalog)}
    reserve(row)
    started = time.perf_counter()
    response_metadata = {}
    try:
        status, raw = send(request, response_metadata=response_metadata)
        row['wall_clock_ms'] = round((time.perf_counter() - started) * 1000, 2)
        row['http_status'] = status
        if 200 <= status < 300:
            row['provider_outcome'] = 'HTTP_SUCCESS'
            try:
                final, actual, usage, finish = p.envelope(cfg, raw)
                row.update(raw_provider_output=final, returned_model_id=actual, token_usage=usage, finish_reason=finish)
                apply_score(row, source, truth, catalog)
            except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                row['error_classes'] = ['MALFORMED_JSON']
        else:
            row['provider_outcome'] = 'HTTP_ERROR'
            row['error_classes'] = [p.provider_failure(status, raw)]
            row['safe_error'] = p.safe_gemini_error(status, raw, cfg['model'], secrets, response_metadata.get('request_id'))
    except (OSError, urllib.error.URLError, TimeoutError):
        row['wall_clock_ms'] = round((time.perf_counter() - started) * 1000, 2)
        row['provider_outcome'] = 'TRANSPORT_FAILURE'
        row['error_classes'] = ['TRANSPORT']
    except Exception:
        row['wall_clock_ms'] = round((time.perf_counter() - started) * 1000, 2)
        row['provider_outcome'] = 'LOCAL_VALIDATION_FAILURE'
        row['error_classes'] = ['LOCAL_VALIDATION_FAILURE']
    finalize_fields(row, truth)
    row.update(state='COMPLETED', completed_utc=now())
    return row


def execute(send=None):
    if any(x.exists() for x in (CALLS, SUMMARY, METADATA, REPORT)):
        raise ValueError('Remediation already has evidence; refusing restart or selective retry')
    reuse, checks = verify_reuse()
    comparable = verify_qwen_comparability()
    key = os.environ['GEMINI_API_KEY']
    if not key:
        raise ValueError('Verified credential unavailable for this execution')
    env = {'GEMINI_API_KEY': key}
    secrets = tuple(v for v in (key, os.environ.get('QWEN_API_KEY'), os.environ.get('QWEN_BASE_URL')) if v)
    metadata = {'scope': 'GEMINI_PHASE_1_REMEDIATION', 'started_utc': now(), 'max_new_live_calls': LIMIT,
                'planned_evaluated_calls': 20, 'run_index': 1, 'automatic_retries': 0,
                'candidates': [dict(c, **p.configuration(c)) for c in CANDIDATES],
                'h01_reuse_verification': checks, 'qwen_offline_comparability': comparable,
                'integrity_before': p.integrity(), 'protected_sha256_before': protected(),
                'free_only_settings': 'Previously explicitly confirmed by user; unchanged Google endpoint/account',
                'call_order': 'Reuse Lite h01; Lite h02..h10; 3.8 h01 gated; 3.8 h02..h10',
                'stop_reason': None, 'phase2_started': False, 'remediation_script_sha256': p.sha(Path(__file__))}
    with METADATA.open('x', encoding='utf-8') as file:
        json.dump(metadata, file, indent=2)
    rows = [reuse]
    checkpoint(rows, metadata, secrets)
    sender = send or p.transport
    fixtures = sorted(p.read(p.CORPUS / 'live/expected.json'))
    def reserve(row):
        if sum(r['new_live_attempt'] for r in rows) >= LIMIT:
            raise ValueError('New live call budget exhausted')
        if any(r['requested_model_id'] == row['requested_model_id'] and r['fixture_id'] == row['fixture_id'] for r in rows):
            raise ValueError('Duplicate candidate/fixture; refusing retry')
        rows.append(row)
        checkpoint(rows, metadata, secrets)
    stop = False
    for cfg in CANDIDATES:
        for fixture in fixtures:
            if cfg == CANDIDATES[0] and fixture == 'h01_paper':
                continue
            p.integrity()
            row = new_call(cfg, fixture, sender, reserve, env, secrets)
            if row['error_class'] == 'AUTH_OR_ACCESS':
                metadata['stop_reason'] = 'Authentication/access failure; frozen protocol stops remaining calls'
                stop = True
            if row['provider_outcome'] == 'LOCAL_VALIDATION_FAILURE':
                metadata['stop_reason'] = 'Unexpected harness failure; no further calls'
                stop = True
            if cfg == CANDIDATES[1] and fixture == 'h01_paper':
                metadata['gemini_3_8_h01_gate'] = {'http_status': row['http_status'], 'json_parsed': row['json_parsed'], 'schema_valid': row['schema_valid']}
                if row['http_status'] != 200 or not row['json_parsed'] or not row['schema_valid']:
                    metadata['stop_reason'] = 'Gemini 3.8 h01 did not satisfy HTTP 200 + JSON/schema gate; remaining nine calls not made'
                    stop = True
            checkpoint(rows, metadata, secrets)
            print(f"{sum(r['new_live_attempt'] for r in rows):02}/{LIMIT} new: {cfg['model']} {fixture}; HTTP {row['http_status']}; {row['error_class'] or 'PASS'}; {row['wall_clock_ms']} ms", flush=True)
            if stop:
                break
        if stop:
            break
    metadata['stop_reason'] = metadata['stop_reason'] or 'Gemini remediation complete; 19-call new-attempt budget consumed; STOP'
    metadata['completed_utc'] = now()
    metadata['integrity_after'] = p.integrity()
    checkpoint(rows, metadata, secrets)
    return 0 if len(rows) == 20 else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'run'))
    action = parser.parse_args().action
    if action == 'validate':
        verify_reuse()
        verify_qwen_comparability()
        print('PASS: exact h01 reuse conditions, frozen hashes and Qwen scoring comparability; zero live calls')
        return 0
    return execute()


if __name__ == '__main__':
    raise SystemExit(main())
