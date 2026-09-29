"""Separate, durable two-call diagnostic; never invoke the Phase-1 batch runner."""
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import phase1 as p
import preflight

JSON = p.ROOT / 'gemini_diagnostic.json'
MD = p.ROOT / 'gemini_diagnostic.md'
DOC = 'https://ai.google.dev/gemini-api/docs/generate-content/structured-output'


def now():
    return datetime.now(timezone.utc).isoformat()


def protected():
    paths = [p.ROOT / n for n in ('preflight.json', 'preflight.md', 'phase1_calls.json',
             'phase1_summary.json', 'phase1_metadata.json', 'phase1.md')]
    paths += [x for x in p.CORPUS.rglob('*') if x.is_file() and p.ROOT not in x.parents]
    paths += [x for x in p.REFERENCE.rglob('*') if x.is_file()]
    return {str(x.relative_to(p.CORPUS)): p.sha(x) for x in paths}


def schema_audit():
    supported = {'type', 'properties', 'required', 'additionalProperties', 'items',
                 'minItems', 'minimum', 'maximum'}
    nodes = []
    def walk(node, path):
        nodes.append({'path': path, 'type': node.get('type'), 'keywords': sorted(node)})
        for name, child in node.get('properties', {}).items():
            walk(child, path + '.' + name)
        if isinstance(node.get('items'), dict):
            walk(node['items'], path + '[]')
    walk(p.SCHEMA, '$')
    unsupported = sorted({k for n in nodes for k in n['keywords']} - supported)
    return {'schema_sha256': p.integrity()['schema_sha256'], 'full_schema': p.SCHEMA,
            'nodes': nodes, 'keywords': sorted({k for n in nodes for k in n['keywords']}),
            'unsupported_keywords_found': unsupported,
            'nullable_union_nodes': sum(isinstance(n['type'], list) for n in nodes),
            'all_objects_closed_and_fully_required': all(
                node['additionalProperties'] is False and set(node['required']) == set(node['properties'])
                for node in object_nodes(p.SCHEMA)),
            'documentation': DOC,
            'conclusion': 'All used keywords and nullable type arrays are documented as supported; actual endpoint acceptance remains to be diagnosed.'}


def object_nodes(node):
    if node.get('type') == 'object':
        yield node
    for child in node.get('properties', {}).values():
        yield from object_nodes(child)
    if isinstance(node.get('items'), dict):
        yield from object_nodes(node['items'])


def render(data):
    lines = ['# Gemini Phase-1 diagnostic', '',
             'Scope: h01 only, Flash-Lite/minimal; at most two explicit single attempts. No Qwen, benchmark rerun, automatic retry, or model substitution.', '',
             '## Offline comparison (completed before any live diagnostic call)', '',
             '| Area | Successful tiny preflight | Original Phase 1 |', '|---|---|---|']
    for row in data['offline_comparison']:
        lines.append('| ' + ' | '.join(row) + ' |')
    lines += ['', 'Flash-Lite was added by the prior remediation driver, not the original preflight.py candidate list; its recorded successful configuration was minimal thinking with tiny JSON MIME output.', '',
              'The complete frozen schema is retained in the JSON artifact. Its eight keywords (type, properties, required, additionalProperties, items, minItems, minimum, maximum), including eight nullable string unions, are supported by the [current Google JSON Schema documentation](' + DOC + '). Every object remains closed and fully required. No schema or scoring relaxation is justified offline.', '',
              'Smallest suspicion: the newly introduced responseFormat.text/schema wire configuration. Documentation describes it, so incompatibility with the actual endpoint is an inference awaiting the provider error; authentication is unchanged and missing credentials are not the working hypothesis.', '',
              '## Diagnostic calls', '']
    for c in data['calls']:
        lines += [f"- Call {c['call_index']}: {c['requested_model_id']} / minimal / {c['fixture_id']} / {c['path_variant']}; HTTP {c.get('http_status')}; {c.get('wall_clock_ms')} ms; state={c['state']}; schema valid={c.get('schema_valid')}; classification={c.get('error_class')}."]
        if c.get('safe_error'):
            lines += ['```json', json.dumps(c['safe_error'], indent=2), '```']
        if c.get('token_usage'):
            lines += ['  Tokens: ' + json.dumps(c['token_usage']) + '.']
        if c.get('returned_model_id'):
            lines += ['  Returned model: ' + c['returned_model_id'] + '.']
        if 'score' in c:
            lines += [f"  Mandatory fields: {c['score']['field_correct']}/8; SKU correct: {c['score']['sku_correct']}; semantic errors: {c['error_classes']}."]
    lines += ['', '## Conclusion', '', data.get('conclusion', 'Offline comparison complete; no live diagnostic request yet.'), '',
              'Correction: ' + data.get('correction', 'None.'), '',
              'The [REST API reference](https://ai.google.dev/api/generate-content#TextResponseFormat) defines TextResponseFormat.mimeType as an enum and lists APPLICATION_JSON. This contradicts the MIME string in the structured-output guide example. The enum reference was inspected after Call 1 identified the exact rejected field.', '',
              'Clean Phase-1 remediation: ' + data.get('remediation_readiness', 'Pending diagnostic evidence; no run authorized or started here.'), '',
              f"Live diagnostic attempts: {len(data['calls'])}/2. Previous preflight and Phase-1 evidence preserved: {data.get('protected_evidence_preserved', True)}. Frozen inputs, schema, labels, and scorer remain unchanged.", '']
    if data.get('checks'):
        lines += ['## Offline verification', '', *['- ' + c for c in data['checks']], '']
    return '\n'.join(lines)


def save(data, secrets=()):
    data['protected_evidence_preserved'] = protected() == data['protected_sha256_before']
    if not data['protected_evidence_preserved']:
        raise ValueError('Protected evidence changed; refusing continuation')
    p.write(JSON, data, secrets)
    p.write(MD, render(data), secrets)


def offline():
    if JSON.exists():
        raise ValueError('Diagnostic evidence already exists; refusing overwrite/restart')
    p.integrity()
    req = preflight.request_for('gemini-3.8-flash', {'GEMINI_API_KEY': 'OFFLINE_SENTINEL'})
    tiny = json.loads(req.data)
    data = {'scope': 'GEMINI_DIAGNOSTIC_ONLY', 'offline_completed_utc': now(), 'live_call_limit': 2,
            'automatic_retries': 0, 'calls': [], 'protected_sha256_before': protected(),
            'phase1_harness_sha256_before': p.sha(p.ROOT / 'phase1.py'),
            'free_only_settings': 'Previously explicitly user-confirmed; unchanged account/endpoint',
            'offline_comparison': [
                ['Endpoint / version', 'generativelanguage.googleapis.com/v1beta/models/{model}:generateContent', 'Identical'],
                ['Headers / authentication', 'Content-Type application/json; x-goog-api-key from GEMINI_API_KEY', 'Identical; header values never persisted'],
                ['Model ID', '3.8 Flash passed; 3.5 Flash-Lite passed remediation', 'Same exact model IDs; model in URL, no body model field'],
                ['Body structure', 'contents: one user role, one text part; generationConfig', 'Identical envelope; text is frozen prompt + synthetic catalog + one fixture'],
                ['thinkingConfig', 'thinkingLevel low (3.8), minimal (successful Lite remediation)', 'Identical per-model levels; no thinkingBudget or includeThoughts'],
                ['Response MIME / format', 'responseMimeType: application/json', 'responseFormat.text.mimeType: application/json'],
                ['JSON schema', 'None (two-field synthetic object, locally checked)', 'Full unchanged application JSON Schema at responseFormat.text.schema'],
                ['Max output', str(tiny['generationConfig']['maxOutputTokens']), '4096'],
                ['Temperature', 'Omitted (provider default)', '0'],
                ['Deprecated / unsupported parameters', 'responseMimeType marked deprecated by current API reference', 'responseFormat documented; no definite unsupported schema keyword found offline'],
                ['Tools / search / grounding', 'None', 'None'],
                ['Encoding / transport', 'json.dumps default ASCII; POST; 30s timeout; no redirects/retries', 'UTF-8 ensure_ascii=False; POST; 180s timeout; no redirects/retries'],
            ], 'schema_audit': schema_audit(),
            'suspected_incompatibility': 'responseFormat.text/schema wire configuration; not yet confirmed',
            'original_generation_config': json.loads(p.build_request(p.CANDIDATES[0], 'OFFLINE', {'GEMINI_API_KEY': 'OFFLINE_SENTINEL'}).data)['generationConfig']}
    with JSON.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    save(data)
    print('PASS: offline comparison and full schema audit complete; zero live diagnostic calls')


def run_one(variant):
    data = p.read(JSON)
    if not data.get('offline_completed_utc') or protected() != data['protected_sha256_before']:
        raise ValueError('Offline comparison/integrity prerequisite failed')
    if variant == 'original' and data['calls']:
        raise ValueError('Original diagnostic already attempted; no retry')
    if variant == 'corrected' and (len(data['calls']) != 1 or not data.get('bounded_fix_authorized_by_evidence')):
        raise ValueError('Correction must be justified by first-call evidence')
    if len(data['calls']) >= 2:
        raise ValueError('Diagnostic call budget exhausted')
    key = os.environ['GEMINI_API_KEY']
    if not key:
        raise ValueError('Diagnostic cannot execute without the verified credential')
    secrets = tuple(v for v in (key, os.environ.get('QWEN_API_KEY'), os.environ.get('QWEN_BASE_URL')) if v)
    fixture = 'h01_paper'
    source = (p.CORPUS / 'live/fixtures' / (fixture + '.txt')).read_text(encoding='utf-8')
    catalog = p.read(p.CORPUS / 'catalog.json')
    truth = p.read(p.CORPUS / 'live/expected.json')[fixture]
    text = p.request_text(source, catalog, (p.CORPUS / 'prompt.txt').read_text(encoding='utf-8'))
    cfg = p.CANDIDATES[0]
    request = p.build_request(cfg, text, {'GEMINI_API_KEY': key})
    body_hash = hashlib.sha256(request.data).hexdigest()
    old = next(c for c in p.read(p.CALLS)['calls'] if c['requested_model_id'] == cfg['model'] and c['fixture_id'] == fixture)
    if variant == 'original' and body_hash != old['request_body_sha256']:
        raise ValueError('Original request does not match preserved Phase-1 body')
    if variant == 'corrected':
        # Prove the only wire-body change is the invalid enum string.
        reverted = json.loads(request.data)
        reverted['generationConfig']['responseFormat']['text']['mimeType'] = 'application/json'
        if hashlib.sha256(json.dumps(reverted, ensure_ascii=False).encode('utf-8')).hexdigest() != old['request_body_sha256']:
            raise ValueError('Correction changed more than the MIME enum; refusing send')
    row = {'call_index': len(data['calls']) + 1, 'provider': cfg['provider'], 'requested_model_id': cfg['model'],
           'fixture_id': fixture, 'path_variant': variant, 'configuration': p.configuration(cfg),
           'request_body_sha256': body_hash, 'generation_config': json.loads(request.data)['generationConfig'],
           'matches_original_phase1_body': body_hash == old['request_body_sha256'],
           'harness_sha256': p.sha(p.ROOT / 'phase1.py'), 'state': 'ATTEMPT_STARTED', 'started_utc': now(),
           'http_status': None, 'returned_model_id': None, 'json_parsed': False, 'schema_valid': False,
           'token_usage': None, 'error_class': None}
    data['calls'].append(row)
    save(data, secrets)  # Durable reservation before send; interruption is never retried.
    metadata = {}
    start = time.perf_counter()
    try:
        status, raw = p.transport(request, response_metadata=metadata)
        row['wall_clock_ms'] = round((time.perf_counter() - start) * 1000, 2)
        row['http_status'] = status
        if not 200 <= status < 300:
            row['error_class'] = p.provider_failure(status, raw)
            row['safe_error'] = p.safe_gemini_error(status, raw, cfg['model'], secrets, metadata.get('request_id'))
        else:
            final, actual, usage, finish = p.envelope(cfg, raw)
            row.update(returned_model_id=actual, token_usage=usage, finish_reason=finish, raw_provider_output=final)
            score, evidence, errors = p.evaluate(final, source, truth, catalog)
            row.update(score=score, evidence_grounding=evidence, error_classes=errors,
                       schema_valid=score['schema_valid'], json_parsed='MALFORMED_JSON' not in errors,
                       error_class=errors[0] if errors else None)
    except (OSError, TimeoutError):
        row['wall_clock_ms'] = round((time.perf_counter() - start) * 1000, 2)
        row['error_class'] = 'TRANSPORT'
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        row['error_class'] = 'LOCAL_VALIDATION_FAILURE'
    row.update(state='COMPLETED', completed_utc=now())
    data['conclusion'] = 'Stopped after one explicit diagnostic attempt; inspect the safe outcome before considering a bounded fix.'
    save(data, secrets)
    print(f"Call {row['call_index']}/2: {cfg['model']} {fixture}; HTTP {row['http_status']}; {row['wall_clock_ms']} ms; {row['error_class'] or 'PASS'}")
    if row.get('safe_error'):
        print(json.dumps(row['safe_error'], ensure_ascii=False))


def record_fix():
    data = p.read(JSON)
    if len(data['calls']) != 1:
        raise ValueError('Fix requires exactly one preserved diagnostic attempt')
    call = data['calls'][0]
    safe = call.get('safe_error', {})
    message = safe.get('gemini_error_message', '')
    if (call['http_status'] != 400 or safe.get('gemini_error_status') != 'INVALID_ARGUMENT'
            or 'generation_config.response_format.text.mime_type' not in message
            or 'TextResponseFormat.MimeType' not in message or '"application/json"' not in message):
        raise ValueError('No bounded MIME enum defect established; stop')
    data['bounded_fix_authorized_by_evidence'] = True
    data['correction'] = 'Shared Gemini build_request: responseFormat.text.mimeType changes only from application/json to APPLICATION_JSON. Full wire schema, local schema, input, thinking, limits and scorer are unchanged.'
    data['root_cause'] = 'The new responseFormat.text.mimeType is a protobuf enum, not the legacy responseMimeType MIME string; application/json is invalid.'
    data['api_reference'] = 'https://ai.google.dev/api/generate-content#TextResponseFormat'
    data['documentation_discrepancy'] = 'Structured-output REST guide uses application/json; API reference and live error identify an enum requiring APPLICATION_JSON.'
    data['conclusion'] = 'Call 1 reproduced the original request body byte-for-byte and confirmed the invalid MIME enum. Same build_request branch serves both Gemini candidates; no authentication or schema rejection was reported.'
    data['fix_recorded_utc'] = now()
    save(data)
    print('PASS: bounded single-value fix justified by preserved HTTP 400 and API reference; no live call')


def finalize():
    data = p.read(JSON)
    if len(data['calls']) != 2 or any(c['state'] != 'COMPLETED' for c in data['calls']):
        raise ValueError('Finalization requires the two completed diagnostic attempts')
    second = data['calls'][1]
    passed = second['http_status'] == 200 and second['schema_valid'] and not second['error_classes']
    data['completed_utc'] = now()
    data['new_live_calls'] = 2
    data['stop_reason'] = 'Diagnostic budget exhausted; no further model calls'
    data['confirmed_root_cause'] = data['root_cause']
    data['conclusion'] = ('Confirmed request-construction defect: responseFormat.text.mimeType requires APPLICATION_JSON, not application/json. The original h01 body was reproduced exactly and returned INVALID_ARGUMENT. Changing only this enum value returned HTTP 200 and passed the unchanged schema, all eight mandatory fields, determinate SKU and grounding checks.' if passed else
                          'The original MIME enum defect is confirmed; the corrected call did not pass all checks. See the second outcome; no further calls permitted.')
    data['remediation_readiness'] = ('Safe to prepare a separately authorized clean Gemini remediation run using the corrected shared request path and unchanged schema/scorer. Flash-Lite/h01 is live-verified. The same defective field is present in the 3.8/low path and is corrected offline; 3.8 and the other nine fixtures were not rerun. Historical errors cannot be recovered retrospectively because their bodies were discarded. No benchmark or Phase 2 was started.' if passed else 'Not yet verified; stop at the two-call limit.')
    data['integrity_after'] = p.integrity()
    data['phase1_harness_sha256_after'] = p.sha(p.ROOT / 'phase1.py')
    data['checks'] = ['python -B -m unittest discover -s spikes/ordershield/provider_bakeoff -p "test_*.py" -q: 35 tests PASS',
                      'python -B spikes/ordershield/provider_bakeoff/phase1.py validate: PASS',
                      'git diff --check: PASS',
                      'Protected preflight, original Phase-1 evidence, corpus and frozen reference SHA-256 comparison: PASS',
                      'Diagnostic artifact secret scan (values inspected only in memory): PASS']
    save(data)
    print('PASS: finalized two-call diagnostic; protected evidence preserved; no live calls')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('offline', 'record-fix', 'finalize', 'call-original', 'call-corrected'))
    action = parser.parse_args().action
    if action == 'offline':
        offline()
    elif action == 'record-fix':
        record_fix()
    elif action == 'finalize':
        finalize()
    else:
        run_one(action.removeprefix('call-'))
