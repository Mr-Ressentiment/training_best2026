"""Finalist stability: six frozen fixtures, three additional runs, 36 sends max."""
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import time
import urllib.error

import phase1 as p

CANDIDATES = (p.CANDIDATES[0], p.CANDIDATES[1])
FIXTURES = ('h05_no_size', 'h06_no_pack', 'h07_not_medium', 'h08_not_large', 'h09_latex', 'h10_damaged')
LIMIT = 36
CALLS = p.ROOT / 'phase2_calls.json'
BASELINE = p.ROOT / 'phase2_phase1_selected_calls.json'
SUMMARY = p.ROOT / 'phase2_summary.json'
METADATA = p.ROOT / 'phase2_metadata.json'
REPORT = p.ROOT / 'phase2.md'
PROVIDER_ERRORS = ('AUTH_OR_ACCESS', 'QUOTA', 'TRANSPORT', 'PROVIDER_4XX', 'PROVIDER_5XX')


def now():
    return datetime.now(timezone.utc).isoformat()


def protected():
    outputs = (CALLS, BASELINE, SUMMARY, METADATA, REPORT)
    excluded = set(outputs) | {x.with_suffix(x.suffix + '.tmp') for x in outputs}
    return {str(x.relative_to(p.CORPUS)): p.sha(x) for x in p.CORPUS.rglob('*')
            if x.is_file() and x not in excluded and '__pycache__' not in x.parts}


def inputs(fixture):
    if fixture not in FIXTURES:
        raise ValueError('Fixture not authorized for Phase 2')
    path = p.CORPUS / 'live/fixtures' / (fixture + '.txt')
    source = path.read_text(encoding='utf-8')
    catalog = p.read(p.CORPUS / 'catalog.json')
    truth = p.read(p.CORPUS / 'live/expected.json')[fixture]
    text = p.request_text(source, catalog, (p.CORPUS / 'prompt.txt').read_text(encoding='utf-8'))
    return path, source, catalog, truth, text


def special_tracking(row, source, truth):
    """Supplemental literal observations; never alter frozen score or award credit."""
    try:
        obj = json.loads(row['raw_provider_output'], parse_constant=p.core.reject_constant)
    except (ValueError, TypeError):
        obj = None
    if not isinstance(obj, dict):
        return {'observations_available': False}
    lines = obj.get('lines')
    line = lines[0] if isinstance(lines, list) and lines and isinstance(lines[0], dict) else {}
    detail = {'observations_available': True, 'schema_valid': row['schema_valid'],
              'supplemental_only_no_scoring_credit': True}
    if truth['needs_sku_review']:
        candidates = line.get('candidates')
        ambiguous = line.get('ambiguous')
        detail['review_trap'] = {
            'model_ambiguous': ambiguous if isinstance(ambiguous, bool) else None,
            'candidate_skus': [c.get('sku') for c in candidates if isinstance(c, dict)] if isinstance(candidates, list) else None,
            'decision_tags': (['AMBIGUOUS'] if ambiguous is True else []) + (['UNRECOGNIZED'] if candidates == [] else []),
            'tags_definition': 'Observed ambiguous=true and/or empty candidates; frozen core uses HUMAN_REVIEW, not these tag names',
            'uncertainty': line.get('uncertainty') if isinstance(line.get('uncertainty'), str) else None,
            'frozen_core_status': row['score']['core_status'],
            'confident_proposal': row['score']['confident_proposal'],
            'successful_review': row['score']['review_success'],
            'clean_or_confident': row['score']['ambiguous_clean'] or row['score']['confident_proposal']}
    if row['fixture_id'] == 'h08_not_large':
        field = line.get('description')
        value = field.get('value') if isinstance(field, dict) else None
        expected = truth['line'][0]
        extra = value.replace(expected, '', 1) if isinstance(value, str) and expected in value and value != expected else None
        detail['h08_customer_description'] = {
            'schema_field': 'lines[0].description.value', 'extracted_value': value,
            'frozen_expected_text': expected, 'literal_exact_match': value == expected,
            'frozen_field_correct': row['score']['field_checks'].get('line.description', False),
            'contains_expected_with_extra_text': extra is not None,
            'additional_text': extra,
            'includes_additional_source_text': extra is not None and bool(extra.strip()) and extra.strip() in source,
            'full_extracted_value_verbatim_in_source': isinstance(value, str) and value in source}
    if row['fixture_id'] == 'h10_damaged':
        fields = []
        for name, value in zip(p.scorer.HEADER, truth['header']):
            if value is None:
                fields.append((name, obj.get(name)))
        for name, value in zip(p.scorer.LINE, truth['line']):
            if value is None:
                fields.append(('line.' + name, line.get(name)))
        observations = []
        for name, field in fields:
            present = isinstance(field, dict) and 'value' in field
            is_null = present and field['value'] is None
            quote = field.get('quote') if isinstance(field, dict) else None
            observations.append({'field': name, 'value_present': present,
                'extracted_value': field.get('value') if isinstance(field, dict) else None,
                'correct_null_value': is_null, 'quote': quote,
                'null_quote_empty': isinstance(quote, str) and quote == '',
                'null_provenance_valid': is_null and quote == ''})
        detail['h10_null_fields'] = {'fields': observations,
            'correct_null_values': sum(f['correct_null_value'] for f in observations),
            'expected_null_fields': len(observations),
            'valid_null_provenance': sum(f['null_provenance_valid'] for f in observations),
            'all_null_values_correct': all(f['correct_null_value'] for f in observations),
            'all_null_provenance_valid': all(f['null_provenance_valid'] for f in observations),
            'frozen_grounding_field_credit': row['score']['provenance_valid']}
    return detail


def finish_fields(row, source, truth):
    score = row['score']
    row.update(error_class=row['error_classes'][0] if row['error_classes'] else None,
               mandatory_field_correctness=score['field_checks'],
               determinate_sku_correctness=score['sku_correct'] if truth['sku'] is not None else None,
               ambiguity_review_correctness=score['review_success'] if truth['needs_sku_review'] else None,
               wrong_confident_sku=score['wrong_confident'],
               special_tracking=special_tracking(row, source, truth))


def baselines():
    p.integrity()
    rows = []
    provenance = []
    env = {'GEMINI_API_KEY': 'OFFLINE_SENTINEL', 'QWEN_API_KEY': 'OFFLINE_SENTINEL',
           'QWEN_BASE_URL': 'https://offline.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'}
    paths = (p.ROOT / 'phase1_gemini_remediation_calls.json', p.CALLS)
    for cfg, path in zip(CANDIDATES, paths):
        selected = [r for r in p.read(path)['calls'] if r['requested_model_id'] == cfg['model'] and r['fixture_id'] in FIXTURES]
        if len(selected) != 6 or {r['fixture_id'] for r in selected} != set(FIXTURES):
            raise ValueError('Expected exactly one Phase-1 baseline for each selected fixture')
        for original in selected:
            _, source, catalog, truth, text = inputs(original['fixture_id'])
            score, evidence, errors = p.evaluate(original['raw_provider_output'], source, truth, catalog)
            request = p.build_request(cfg, text, env)
            if (score != original['score'] or evidence != original['evidence_grounding'] or errors != original['error_classes']
                    or p.raw_quote_audit(original['raw_provider_output'], source) != original['raw_quote_audit']
                    or hashlib.sha256(request.data).hexdigest() != original['request_body_sha256']
                    or original['configuration'] != p.configuration(cfg)):
                raise ValueError('Phase-1 scores or exact request body do not reproduce')
            row = copy.deepcopy(original)
            row.update(phase='PHASE_1_SELECTED_BASELINE', source_artifact=path.name,
                       source_artifact_sha256=p.sha(path), additional_run_index=None)
            finish_fields(row, source, truth)
            rows.append(row)
        provenance.append({'model': cfg['model'], 'artifact': path.name, 'sha256': p.sha(path),
                           'selected_calls': 6, 'scores_errors_grounding_and_request_hashes_identical': True})
    return rows, provenance


def schedule():
    return [(cfg, fixture, run) for run in (2, 3, 4) for cfg in CANDIDATES for fixture in FIXTURES]


def metrics(rows, scheduled):
    done = [r for r in rows if r['state'] == 'COMPLETED']
    truth = p.read(p.CORPUS / 'live/expected.json')
    determinate = [r for r in done if truth[r['fixture_id']]['sku'] is not None]
    reviews = [r for r in done if truth[r['fixture_id']]['needs_sku_review']]
    def total(key, subset=done):
        return sum(r['score'][key] for r in subset)
    audits = [r['raw_quote_audit'] for r in done if r.get('raw_quote_audit') is not None]
    tokens = {}
    google = bool(rows) and rows[0]['requested_model_id'].startswith('gemini')
    for name, key in (('input', 'promptTokenCount' if google else 'prompt_tokens'),
                      ('output', 'candidatesTokenCount' if google else 'completion_tokens'),
                      ('total', 'totalTokenCount' if google else 'total_tokens'),
                      ('thinking', 'thoughtsTokenCount' if google else 'reasoning_tokens')):
        values = [r['token_usage'][key] for r in done if isinstance(r.get('token_usage'), dict) and type(r['token_usage'].get(key)) is int]
        tokens[name] = {'exposed_calls': len(values), 'total': sum(values) if values else None,
                        'average_per_exposed_call': statistics.mean(values) if values else None}
    return {'scheduled_calls': scheduled, 'attempted_calls': len(rows), 'finalized_attempts': len(done),
        'unattempted_calls': scheduled - len(rows),
        'completed_calls': sum(r['http_status'] is not None and 200 <= r['http_status'] < 300 and bool(r['raw_provider_output']) for r in done),
        'schema_valid_rate': p.ratio(total('schema_valid'), len(rows)),
        'exact_mandatory_field_rate': p.ratio(total('field_correct'), 8 * len(done)),
        'whole_order_field_rate': p.ratio(total('all_fields_correct'), len(done)),
        'determinate_sku_rate': p.ratio(total('sku_correct', determinate), len(determinate)),
        'review_trap_rate': p.ratio(total('review_success', reviews), len(reviews)),
        'wrong_confident_sku_count': total('wrong_confident'),
        'ambiguous_to_clean_count': total('ambiguous_clean', reviews),
        'review_clean_or_confident_count': sum(r['score']['ambiguous_clean'] or r['score']['confident_proposal'] for r in reviews),
        'grounding_null_validity': p.ratio(total('provenance_valid'), 8 * len(done)),
        'nonnull_grounding': p.ratio(total('nonnull_evidence_valid'), total('nonnull_evidence_expected')),
        'returned_verbatim_snippets': p.ratio(sum(a['verbatim_snippets'] for a in audits), sum(a['returned_snippets'] for a in audits)),
        'latency_ms': p.latency([r['wall_clock_ms'] for r in done if r['wall_clock_ms'] is not None]),
        'provider_failures_by_class': dict(Counter(r['error_class'] for r in done if r['error_class'] in PROVIDER_ERRORS)),
        'all_error_classes': dict(Counter(e for r in done for e in r['error_classes'])),
        'token_usage': tokens,
        'outcomes': dict(Counter(r['score']['core_status'] or r['error_class'] or 'NO_CORE_STATUS' for r in done)),
        'decision_tags': dict(Counter(tag for r in done for tag in r.get('special_tracking', {}).get('review_trap', {}).get('decision_tags', []))),
        'observations': [{'phase': r['phase'], 'run_index': r['run_index'], 'fixture_id': r['fixture_id'],
            'schema_valid': r['schema_valid'], 'core_status': r['score']['core_status'],
            'proposed_sku': r['score']['proposed_sku'], 'errors': r['error_classes'],
            'special_tracking': r.get('special_tracking')} for r in done]}


def summarize(baseline, new, metadata):
    candidates = []
    for cfg in CANDIDATES:
        first = [r for r in baseline if r['requested_model_id'] == cfg['model']]
        second = [r for r in new if r['requested_model_id'] == cfg['model']]
        views = {'phase1_selected': (first, 6, 1), 'phase2': (second, 18, 3), 'combined': (first + second, 24, 4)}
        result = {'provider': cfg['provider'], 'requested_model': cfg['model'], 'configuration': p.configuration(cfg)}
        for name, (rows, planned, runs) in views.items():
            result[name] = metrics(rows, planned)
            result[name]['per_fixture'] = {f: metrics([r for r in rows if r['fixture_id'] == f], runs) for f in FIXTURES}
        candidates.append(result)
    return {'scope': 'PHASE_2_FINALIST_STABILITY', 'max_new_live_calls': LIMIT,
        'new_live_attempts': len(new), 'selected_phase1_calls': len(baseline), 'fixtures': list(FIXTURES),
        'candidates': candidates, 'stop_reason': metadata['stop_reason'],
        'interpretation': 'Only corrected Gemini remediation and original successful Qwen Phase 1 are included; original invalid Gemini requests are excluded. All original raw evidence remains immutable.',
        'scoring': 'Identical Phase-1 schema/scorer/grounding. Mandatory comparison collapses whitespace only as frozen; literal h08 and raw h10 observations are supplemental, never substituted for scores.',
        'weighted_score_computed': False, 'winner_declared': False}


def render(summary, metadata):
    def fraction(x):
        return f"{x['numerator']}/{x['denominator']}" if x['rate'] is not None else 'unmeasured'
    lines = ['# OrderShield finalist Phase-2 stability', '',
        f"New live attempts: **{summary['new_live_attempts']}/36**. Baseline: 12 selected Phase-1 attempts; combined plan: 48. Run indices 2–4 are new, index 1 is preserved baseline. No retries, substitutions, h01–h04, excluded providers, weighted score, or winner.", '',
        '| Model | Evidence | Completed/attempted | Schema | Exact fields | Determinate SKU | Review traps | Wrong confident | Review→clean | Grounding/null | p50/p95 ms | Min/max ms | Provider failures |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for candidate in summary['candidates']:
        for view in ('phase1_selected', 'phase2', 'combined'):
            r = candidate[view]
            d = r['latency_ms'] or {}
            lines.append(f"| {candidate['requested_model']} | {view} | {r['completed_calls']}/{r['attempted_calls']} | {fraction(r['schema_valid_rate'])} | {fraction(r['exact_mandatory_field_rate'])} | {fraction(r['determinate_sku_rate'])} | {fraction(r['review_trap_rate'])} | {r['wrong_confident_sku_count']} | {r['ambiguous_to_clean_count']} | {fraction(r['grounding_null_validity'])} | {d.get('p50_ms', '—')}/{d.get('p95_ms', '—')} | {d.get('min_ms', '—')}/{d.get('max_ms', '—')} | {json.dumps(r['provider_failures_by_class'])} |")
    lines += ['', '## Per-fixture stability', '',
              '| Model | Fixture | Phase | Completed/attempted | Schema | Exact fields | SKU or review success | Wrong confident | Clean/confident review trap | Grounding/null | Outcomes |',
              '|---|---|---|---|---|---|---|---|---|---|---|']
    for candidate in summary['candidates']:
        for fixture in FIXTURES:
            for view in ('phase1_selected', 'phase2', 'combined'):
                r = candidate[view]['per_fixture'][fixture]
                target = r['review_trap_rate'] if fixture in ('h05_no_size', 'h06_no_pack', 'h09_latex') else r['determinate_sku_rate']
                lines.append(f"| {candidate['requested_model']} | {fixture} | {view} | {r['completed_calls']}/{r['attempted_calls']} | {fraction(r['schema_valid_rate'])} | {fraction(r['exact_mandatory_field_rate'])} | {fraction(target)} | {r['wrong_confident_sku_count']} | {r['review_clean_or_confident_count']} | {fraction(r['grounding_null_validity'])} | {json.dumps(r['outcomes'])} |")
    lines += ['', '## Description and null provenance observations', '',
              '| Model | Phase | h08 literal exact / observed | h08 frozen-correct / observed | h08 additional source text / observed | h10 correct raw nulls / expected | h10 valid raw null provenance / expected |',
              '|---|---|---|---|---|---|---|']
    for candidate in summary['candidates']:
        for view in ('phase1_selected', 'phase2', 'combined'):
            r = candidate[view]
            descriptions = [x['special_tracking']['h08_customer_description'] for x in r['observations'] if 'h08_customer_description' in (x.get('special_tracking') or {})]
            nulls = [x['special_tracking']['h10_null_fields'] for x in r['observations'] if 'h10_null_fields' in (x.get('special_tracking') or {})]
            n = len(descriptions)
            total = sum(x['expected_null_fields'] for x in nulls)
            lines.append(f"| {candidate['requested_model']} | {view} | {sum(x['literal_exact_match'] for x in descriptions)}/{n} | {sum(x['frozen_field_correct'] for x in descriptions)}/{n} | {sum(x['includes_additional_source_text'] for x in descriptions)}/{n} | {sum(x['correct_null_values'] for x in nulls)}/{total} | {sum(x['valid_null_provenance'] for x in nulls)}/{total} |")
    lines += ['', 'These are raw supplemental observations, also retained on schema-invalid JSON; invalid outputs still earn zero frozen scoring credit. Omitted value differs from explicit null. The schema field for customer description is lines[0].description.value. Core review status remains HUMAN_REVIEW; AMBIGUOUS/UNRECOGNIZED tags describe ambiguous=true and empty candidates, respectively.', '', '## Tokens and decisions', '']
    for candidate in summary['candidates']:
        for view in ('phase1_selected', 'phase2', 'combined'):
            r = candidate[view]
            lines += [f"- {candidate['requested_model']} / {view}: tokens={json.dumps(r['token_usage'])}; review tags={json.dumps(r['decision_tags'])}; all error classes={json.dumps(r['all_error_classes'])}; verbatim snippets={fraction(r['returned_verbatim_snippets'])}."]
    lines += ['', '## Method and preservation', '',
        'Each request-body hash must equal its Phase-1 baseline for that exact model and fixture. Round-major order: each additional run sends Gemini h05–h10, then Qwen h05–h10. Same exact prompt, catalog, schema, output mode, temperature, output cap, thinking settings, scorer, thresholds and grounding semantics. No earlier model output or labels enter inference.',
        'Every network attempt is durably reserved before sending. Existing Phase-2 artifacts prohibit restart and selective retry. Provider/transport failures remain in attempted-call denominators with zero scoring credit; unattempted outcomes are unmeasured. Authentication/access or unexpected harness failure stops the remainder as in the frozen protocol.',
        'HTTP wall latency includes transport; baseline measurements retain their original timestamps. p50 is median; p95 is nearest rank. With 18 Phase-2 attempts it equals the observed maximum; with 24 combined attempts it is the second-highest. Repeated fixtures are correlated and do not establish general reliability. Confidence scores remain uncalibrated.',
        'Free-only settings rely on prior explicit confirmation; the approved Singapore endpoint and environment source are validated without persisting values. No paid fallback or alternate region. Accepted requests do not establish remaining free quota.',
        f"Stop reason: {metadata['stop_reason']}. Protected previous evidence, frozen corpus, schema, evaluator and grounding unchanged: {metadata.get('protected_evidence_preserved', True)}.", '']
    if metadata.get('checks'):
        lines += ['## Verification', '', *['- ' + c for c in metadata['checks']], '']
    return '\n'.join(lines)


def safe_error(cfg, status, raw, secrets, request_id):
    if cfg['model'].startswith('gemini'):
        return p.safe_gemini_error(status, raw, cfg['model'], secrets, request_id)
    try:
        data = json.loads(raw)
        error = data.get('error', {}) if isinstance(data, dict) else {}
        error = error if isinstance(error, dict) else {}
    except (ValueError, TypeError):
        error = {}
    minimal = json.dumps({'error': {'status': error.get('code') or error.get('type'), 'message': error.get('message')}}).encode()
    cleaned = p.safe_gemini_error(status, minimal, cfg['model'], secrets, request_id)
    return {'http_status': status, 'provider_error_code': cleaned['gemini_error_status'],
            'provider_error_message': cleaned['gemini_error_message'], 'request_id': cleaned['request_id'],
            'requested_model_id': cfg['model'], 'message_limit_characters': 2048}


def call(cfg, fixture, run, send, reserve, env, secrets, baseline):
    if cfg not in CANDIDATES or fixture not in FIXTURES or run not in (2, 3, 4):
        raise ValueError('Unauthorized candidate/fixture/run')
    path, source, catalog, truth, text = inputs(fixture)
    request = p.build_request(cfg, text, env)
    digest = hashlib.sha256(request.data).hexdigest()
    if digest != baseline['request_body_sha256']:
        raise ValueError('Request path changed from Phase 1; refusing send')
    row = {'phase': 'PHASE_2', 'provider': cfg['provider'], 'requested_model_id': cfg['model'],
        'returned_model_id': None, 'configuration': p.configuration(cfg), 'fixture_id': fixture,
        'run_index': run, 'additional_run_index': run - 1, 'state': 'ATTEMPT_STARTED', 'started_utc': now(),
        'source_sha256': p.sha(path), 'request_body_sha256': digest, 'matches_phase1_request_body': True,
        'http_status': None, 'provider_outcome': None, 'schema_valid': False, 'json_parsed': False,
        'raw_provider_output': None, 'evidence_grounding': None, 'raw_quote_audit': None,
        'wall_clock_ms': None, 'token_usage': None, 'finish_reason': None,
        'error_classes': [], 'error_class': None, 'score': p.scorer.score_response(None, source, truth, catalog)}
    reserve(row)
    start = time.perf_counter()
    response_metadata = {}
    try:
        status, raw = send(request, response_metadata=response_metadata)
        row['wall_clock_ms'] = round((time.perf_counter() - start) * 1000, 2)
        row['http_status'] = status
        if 200 <= status < 300:
            row['provider_outcome'] = 'HTTP_SUCCESS'
            try:
                final, actual, usage, finish = p.envelope(cfg, raw)
                row.update(raw_provider_output=final, returned_model_id=actual, token_usage=usage, finish_reason=finish)
                score, evidence, errors = p.evaluate(final, source, truth, catalog)
                row.update(score=score, evidence_grounding=evidence, error_classes=errors,
                           schema_valid=score['schema_valid'], json_parsed='MALFORMED_JSON' not in errors,
                           raw_quote_audit=p.raw_quote_audit(final, source))
            except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                row['error_classes'] = ['MALFORMED_JSON']
        else:
            row['provider_outcome'] = 'HTTP_ERROR'
            row['error_classes'] = [p.provider_failure(status, raw)]
            row['safe_error'] = safe_error(cfg, status, raw, secrets, response_metadata.get('request_id'))
    except (OSError, urllib.error.URLError, TimeoutError):
        row['provider_outcome'] = 'TRANSPORT_FAILURE'
        row['error_classes'] = ['TRANSPORT']
    except Exception:
        row['provider_outcome'] = 'HARNESS_FAILURE'
        row['error_classes'] = ['LOCAL_VALIDATION_FAILURE']
    if row['wall_clock_ms'] is None:
        row['wall_clock_ms'] = round((time.perf_counter() - start) * 1000, 2)
    finish_fields(row, source, truth)
    row.update(state='COMPLETED', completed_utc=now())
    return row


def checkpoint(baseline, rows, metadata, secrets):
    if protected() != metadata['protected_sha256_before']:
        raise ValueError('Protected corpus/evidence/evaluator changed; stop')
    metadata.update(protected_evidence_preserved=True, new_live_attempts=len(rows))
    p.write(CALLS, {'scope': 'PHASE_2_ONLY', 'calls': rows}, secrets)
    summary = summarize(baseline, rows, metadata)
    p.write(SUMMARY, summary, secrets)
    p.write(METADATA, metadata, secrets)
    p.write(REPORT, render(summary, metadata), secrets)


def execute(send=None):
    if any(x.exists() for x in (CALLS, BASELINE, SUMMARY, METADATA, REPORT)):
        raise ValueError('Phase 2 evidence already exists; no restart or selective retry')
    baseline, provenance = baselines()
    env, env_source = p.environment()
    secrets = tuple(env.values())
    metadata = {'scope': 'PHASE_2_FINALIST_STABILITY', 'started_utc': now(), 'max_new_live_calls': LIMIT,
        'configurations': [dict(c, **p.configuration(c)) for c in CANDIDATES], 'fixtures': FIXTURES,
        'run_indices': [2, 3, 4], 'additional_runs_per_fixture': 3, 'automatic_retries': 0,
        'environment_selection': env_source, 'free_only_settings': 'Previously explicitly user-confirmed; same account and approved endpoint',
        'baseline_provenance': provenance, 'integrity_before': p.integrity(),
        'protected_sha256_before': protected(), 'harness_sha256': p.sha(Path(__file__)),
        'schedule': [{'model': c['model'], 'fixture': f, 'run_index': r} for c, f, r in schedule()],
        'stop_reason': None}
    with METADATA.open('x', encoding='utf-8') as file:
        json.dump(metadata, file, indent=2)
    p.write(BASELINE, {'scope': 'PHASE_1_SELECTED_ONLY', 'calls': baseline}, secrets)
    rows = []
    checkpoint(baseline, rows, metadata, secrets)
    def reserve(row):
        identity = (row['requested_model_id'], row['fixture_id'], row['run_index'])
        if len(rows) >= LIMIT or any((r['requested_model_id'], r['fixture_id'], r['run_index']) == identity for r in rows):
            raise ValueError('Call limit or duplicate attempt; no send')
        rows.append(row)
        checkpoint(baseline, rows, metadata, secrets)
    by_id = {(r['requested_model_id'], r['fixture_id']): r for r in baseline}
    for cfg, fixture, run in schedule():
        p.integrity()
        row = call(cfg, fixture, run, send or p.transport, reserve, env, secrets, by_id[(cfg['model'], fixture)])
        if row['error_class'] == 'AUTH_OR_ACCESS' or row['provider_outcome'] == 'HARNESS_FAILURE':
            metadata['stop_reason'] = 'Authentication/access or unexpected harness failure; remaining calls stopped'
        checkpoint(baseline, rows, metadata, secrets)
        print(f"{len(rows):02}/36 {cfg['model']} {fixture} run {run}: HTTP {row['http_status']}; {row['error_class'] or 'PASS'}; {row['wall_clock_ms']} ms", flush=True)
        if metadata['stop_reason']:
            break
    metadata['stop_reason'] = metadata['stop_reason'] or 'All 36 new single attempts finalized; Phase 2 STOP'
    metadata['completed_utc'] = now()
    metadata['integrity_after'] = p.integrity()
    checkpoint(baseline, rows, metadata, secrets)
    return 0 if len(rows) == LIMIT else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'run'))
    action = parser.parse_args().action
    if action == 'validate':
        rows, _ = baselines()
        assert len(rows) == 12 and len(schedule()) == 36
        print('PASS: 12 Phase-1 baselines, exact scoring and request hashes, 36 authorized scheduled calls; zero live calls')
        return 0
    return execute()


if __name__ == '__main__':
    raise SystemExit(main())
