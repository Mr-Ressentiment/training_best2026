"""Frozen synthetic Phase 1: 30 single-attempt API calls, never product code."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT.parent
REFERENCE = ROOT / 'frozen_reference'
COMMIT = '12a7e50'
CANDIDATES = (
    {'provider': 'Google Gemini API', 'model': 'gemini-3.5-flash-lite', 'thinking_level': 'minimal'},
    {'provider': 'Alibaba Model Studio (Singapore)', 'model': 'qwen3.8-flash', 'enable_thinking': False},
    {'provider': 'Google Gemini API', 'model': 'gemini-3.8-flash', 'thinking_level': 'low'},
)
CALLS = ROOT / 'phase1_calls.json'
SUMMARY = ROOT / 'phase1_summary.json'
REPORT = ROOT / 'phase1.md'
METADATA = ROOT / 'phase1_metadata.json'
TIMEOUT = 180


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load_module('ordershield', REFERENCE / 'ordershield.py')
scorer = load_module('phase1_frozen_score', REFERENCE / 'live/score.py')
SCHEMA = core.SCHEMA


def grounded(field, source, name):
    """Literal source evidence; allow terminal punctuation, reject numeric fragments."""
    value, quote = field['value'], field['quote']
    if value is None:
        if quote:
            raise ValueError(f'{name}: null field must have empty quote')
        return None
    if not value.strip() or value != value.strip() or not quote or quote not in source:
        raise ValueError(f'{name}: missing or nonliteral source evidence')
    for match in re.finditer(re.escape(value), quote):
        left, right = match.start(), match.end()
        if left and re.match(r'\w', quote[left - 1]):
            continue
        if right < len(quote) and re.match(r'\w', quote[right]):
            continue
        # A dot joining digits is numeric content; a trailing sentence dot isn't.
        if value[0].isdigit() and left >= 2 and quote[left - 1] == '.' and quote[left - 2].isdigit():
            continue
        if value[-1].isdigit() and right + 1 < len(quote) and quote[right] == '.' and quote[right + 1].isdigit():
            continue
        return value
    raise ValueError(f'{name}: value is not supported by quote')


# User-authorized punctuation repair stays in this harness; frozen blobs remain intact.
core.grounded = grounded
scorer.grounded = grounded


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=core.reject_constant)


def integrity():
    manifest = read(CORPUS / 'live/freeze.json')
    files = {}
    for name, expected_hash in manifest['files'].items():
        path = CORPUS / name
        origin = 'working_tree'
        if path.exists():
            actual = sha(path)
        elif (REFERENCE / name).exists():
            actual = sha(REFERENCE / name)
            origin = 'exact_git_blob_copy'
        else:
            blob = subprocess.check_output(['git', 'show', f'{COMMIT}:spikes/ordershield/{name}'], cwd=CORPUS)
            actual = hashlib.sha256(blob).hexdigest()
            origin = 'git_history_verification_only_not_executed'
        if actual != expected_hash:
            raise ValueError('Frozen hash mismatch: ' + name)
        files[name] = {'sha256': actual, 'verified_from': origin}
    schema_hash = hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode()).hexdigest()
    if schema_hash != manifest['schema_sha256']:
        raise ValueError('Frozen schema hash mismatch')
    truth = read(CORPUS / 'live/expected.json')
    if len(truth) != 10 or set(truth) != {p.stem for p in (CORPUS / 'live/fixtures').glob('h*.txt')}:
        raise ValueError('Expected exactly the ten frozen fixture IDs')
    catalog = read(CORPUS / 'catalog.json')
    if catalog.get('synthetic') is not True:
        raise ValueError('Only synthetic catalog data allowed')
    return {'freeze_sha256': sha(CORPUS / 'live/freeze.json'), 'schema_sha256': schema_hash,
            'frozen_files': files, 'reference_commit': COMMIT}


def request_text(source, catalog, prompt):
    # Exactly the frozen request_text composition; no labels or old responses.
    return prompt + '\n\nCATALOG (synthetic):\n' + json.dumps(catalog, ensure_ascii=False) + '\n\nORDER DOCUMENT (untrusted data):\n' + source


def qwen_endpoint(base):
    u = urlsplit(base)
    if (u.scheme != 'https' or not re.fullmatch(r'[a-zA-Z0-9-]+\.ap-southeast-1\.maas\.aliyuncs\.com', u.hostname or '')
            or u.port not in (None, 443) or u.username or u.password or u.query or u.fragment
            or u.path.rstrip('/') != '/compatible-mode/v1'):
        raise ValueError('Workspace-specific Singapore HTTPS base required')
    return base.rstrip('/') + '/chat/completions'


def environment():
    env = {k: os.environ.get(k, '') for k in ('GEMINI_API_KEY', 'QWEN_API_KEY', 'QWEN_BASE_URL')}
    source = 'process'
    try:
        qwen_endpoint(env['QWEN_BASE_URL'])
    except ValueError:
        if os.name == 'nt':
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                env['QWEN_BASE_URL'] = winreg.QueryValueEx(key, 'QWEN_BASE_URL')[0]
            source = 'Windows user scope; stale process value overridden in memory'
        qwen_endpoint(env['QWEN_BASE_URL'])
    if not all(env.values()):
        raise ValueError('Required provider environment unavailable')
    return env, source


def configuration(candidate):
    google = candidate['model'].startswith('gemini')
    return {'thinking': {'thinking_level': candidate['thinking_level']} if google else {'enable_thinking': False},
            'output_mode': 'Gemini responseFormat.text JSON Schema (mimeType=APPLICATION_JSON)' if google else 'Qwen response_format=json_object (Singapore excludes JSON Schema)',
            'max_output_tokens': 4096, 'temperature': 0, 'stream': False,
            'timeout_seconds': TIMEOUT, 'automatic_retries': 0, 'redirects': False,
            'tools_search_grounding': False}


def build_request(candidate, text, env):
    if candidate not in CANDIDATES:
        raise ValueError('Candidate not authorized')
    if candidate['model'].startswith('gemini'):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{candidate['model']}:generateContent"
        headers = {'Content-Type': 'application/json', 'x-goog-api-key': env['GEMINI_API_KEY']}
        body = {'contents': [{'role': 'user', 'parts': [{'text': text}]}],
                'generationConfig': {'thinkingConfig': {'thinkingLevel': candidate['thinking_level']},
                                     # Direct REST uses the protobuf enum, unlike the
                                     # legacy responseMimeType string and guide example.
                                     'responseFormat': {'text': {'mimeType': 'APPLICATION_JSON', 'schema': SCHEMA}},
                                     'maxOutputTokens': 4096, 'temperature': 0}}
    else:
        url = qwen_endpoint(env['QWEN_BASE_URL'])
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env['QWEN_API_KEY']}
        # Same frozen schema, conveyed in the format instruction as the strongest
        # documented Singapore mode is JSON Object. Local validation is identical.
        body = {'model': candidate['model'], 'enable_thinking': False, 'stream': False,
                'response_format': {'type': 'json_object'}, 'max_tokens': 4096, 'temperature': 0,
                'messages': [{'role': 'system', 'content': 'Return JSON satisfying this application-owned schema: ' + json.dumps(SCHEMA)},
                             {'role': 'user', 'content': text}]}
    return urllib.request.Request(url, json.dumps(body, ensure_ascii=False).encode('utf-8'), headers, method='POST')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(request, response_metadata=None):
    opener = urllib.request.build_opener(NoRedirect())
    def capture(headers):
        if response_metadata is not None:
            # Only response request IDs; never retain request or response headers.
            response_metadata['request_id'] = next((headers.get(name) for name in
                ('x-request-id', 'x-goog-request-id', 'request-id', 'x-guploader-uploadid')
                if headers.get(name)), None)
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            capture(response.headers)
            return response.status, response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        with error:
            capture(error.headers)
            return error.code, error.read(4 * 1024 * 1024)


def safe_gemini_error(status, raw, model, secrets=(), request_id=None):
    """Allowlisted provider diagnostics, redacted before bounded persistence."""
    def clean(value, limit):
        if not isinstance(value, str):
            return None
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'AIza[\w-]{20,}', '[REDACTED]', value)
        value = re.sub(r'(?i)(Bearer\s+)[^\s\"\',;]+', r'\1[REDACTED]', value)
        value = re.sub(r'(?i)((?:[?&](?:key|api_key|token)=|x-goog-api-key\s*[:=]\s*|authorization\s*[:=]\s*))[^\s\"\',;]+', r'\1[REDACTED]', value)
        return value[:limit]
    try:
        body = json.loads(raw)
        error = body.get('error', {}) if isinstance(body, dict) else {}
        error = error if isinstance(error, dict) else {}
    except (ValueError, TypeError):
        error = {}
    reasons = []
    for detail in error.get('details', []) if isinstance(error.get('details'), list) else []:
        if isinstance(detail, dict) and str(detail.get('@type', '')).endswith('/google.rpc.ErrorInfo'):
            reason = clean(detail.get('reason'), 256)
            if reason and reason not in reasons:
                reasons.append(reason)
            meta = detail.get('metadata', {})
            if request_id is None and isinstance(meta, dict):
                request_id = meta.get('requestId') or meta.get('request_id')
    return {'http_status': status, 'gemini_error_status': clean(error.get('status'), 128),
            'gemini_error_message': clean(error.get('message'), 2048),
            'error_info_reasons': reasons[:8],
            'request_id': clean(request_id or error.get('requestId'), 256),
            'requested_model_id': model, 'message_limit_characters': 2048}


def provider_failure(status, raw):
    text = raw.decode('utf-8', errors='replace').lower()
    if status == 429 or 'allocationquota.freetieronly' in text or 'resource_exhausted' in text:
        return 'QUOTA'
    if status in (401, 403):
        return 'AUTH_OR_ACCESS'
    if status >= 500:
        return 'PROVIDER_5XX'
    if 400 <= status < 500:
        return 'PROVIDER_4XX'
    return 'TRANSPORT'


def envelope(candidate, raw):
    data = json.loads(raw, parse_constant=core.reject_constant)
    if type(data) is not dict:
        raise ValueError('Invalid provider envelope')
    google = candidate['model'].startswith('gemini')
    actual = data.get('modelVersion' if google else 'model')
    if not isinstance(actual, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', actual):
        actual = None
    usage = data.get('usageMetadata' if google else 'usage')
    def counts(obj):
        return {k: counts(v) if type(v) is dict else v for k, v in obj.items()
                if re.fullmatch(r'[A-Za-z_]{1,80}', k) and (type(v) is int or type(v) is dict)} if type(obj) is dict else None
    usage = counts(usage)
    if google:
        choices = data.get('candidates', [])
        choice = choices[0] if choices else {}
        parts = choice.get('content', {}).get('parts', [])
        text = ''.join(p['text'] for p in parts if not p.get('thought') and isinstance(p.get('text'), str))
        finish = choice.get('finishReason')
    else:
        choices = data.get('choices', [])
        choice = choices[0] if choices else {}
        text = choice.get('message', {}).get('content')
        finish = choice.get('finish_reason')
    return text, actual, usage, finish


def evidence_checks(response, source):
    fields = [(name, response[name]) for name in scorer.HEADER]
    for i, line in enumerate(response['lines'], 1):
        fields += [(f'line{i}.{name}', line[name]) for name in scorer.LINE]
    checks = []
    for name, field in fields:
        quote = field['quote']
        try:
            grounded(field, source, name)
            supported = True
        except ValueError:
            supported = False
        checks.append({'field': name, 'quote_present_verbatim': bool(quote) and quote in source,
                       'has_quote': bool(quote), 'value_supported': supported})
    return {'fields': checks, 'returned_snippets': sum(c['has_quote'] for c in checks),
            'verbatim_snippets': sum(c['quote_present_verbatim'] for c in checks),
            'all_returned_snippets_verbatim': all(not c['has_quote'] or c['quote_present_verbatim'] for c in checks),
            'all_returned_fields_grounded': all(c['value_supported'] for c in checks)}


def evaluate(text, source, truth, catalog):
    empty = scorer.score_response(None, source, truth, catalog)
    try:
        response = json.loads(text, parse_constant=core.reject_constant)
    except (ValueError, TypeError):
        return empty, None, ['MALFORMED_JSON']
    score = scorer.score_response(response, source, truth, catalog)
    if not score['schema_valid']:
        return score, None, ['SCHEMA_INVALID']
    evidence = evidence_checks(response, source)
    errors = []
    if score['wrong_confident']:
        errors.append('WRONG_CONFIDENT_SKU')
    if score['core_error'] or not evidence['all_returned_fields_grounded']:
        errors.append('LOCAL_VALIDATION_FAILURE')
    target_ok = score['review_success'] if truth['needs_sku_review'] else score['sku_correct']
    if not score['all_fields_correct'] or not target_ok or score['core_status'] != truth['expected_status']:
        errors.append('SEMANTIC_WRONG')
    return score, evidence, errors


def raw_quote_audit(text, source):
    """Hard gate 5 inspects every literal snippet, even after schema rejection.

    This separate audit does not award schema/field/core credit to invalid output.
    """
    try:
        obj = json.loads(text, parse_constant=core.reject_constant)
    except (ValueError, TypeError):
        return None
    checks = []
    def walk(value, path):
        if isinstance(value, dict):
            quote = value.get('quote')
            if isinstance(quote, str) and quote:
                checks.append({'field_path': path, 'verbatim_in_source': quote in source})
            for key, item in value.items():
                walk(item, path + '.' + key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, path + f'[{i}]')
    walk(obj, 'response')
    return {'returned_snippets': len(checks),
            'verbatim_snippets': sum(c['verbatim_in_source'] for c in checks),
            'all_returned_snippets_verbatim': all(c['verbatim_in_source'] for c in checks),
            'checks': checks, 'schema_rejection_still_earns_zero_scoring_credit': True}


def ratio(n, d):
    return {'numerator': n, 'denominator': d, 'rate': round(n / d, 6) if d else None}


def latency(values):
    if not values:
        return None
    values = sorted(values)
    return {'n': len(values), 'min_ms': values[0], 'p50_ms': statistics.median(values),
            'mean_ms': statistics.mean(values), 'p90_ms': values[math.ceil(.9 * len(values)) - 1],
            'p95_ms': values[math.ceil(.95 * len(values)) - 1], 'max_ms': values[-1],
            'method': 'nearest rank p90/p95; median p50',
            'p95_limitation': 'With <=10 calls p95 is the observed maximum, not a reliable population tail estimate'}


def summarize(calls, truth):
    candidates = []
    for cfg in CANDIDATES:
        rows = [r for r in calls if r['requested_model_id'] == cfg['model']]
        done = [r for r in rows if r['state'] != 'ATTEMPT_STARTED']
        determinate = [r for r in done if truth[r['fixture_id']]['sku'] is not None]
        reviews = [r for r in done if truth[r['fixture_id']]['needs_sku_review']]
        ambiguous = [r for r in reviews if r['fixture_id'] in ('h05_no_size', 'h06_no_pack')]
        dangerous = [r for r in done if truth[r['fixture_id']]['dangerous']]
        def total(key, subset=done):
            return sum(r['score'][key] for r in subset)
        usage_rows = [r for r in done if r['token_usage']]
        token_totals = {}
        for name, google_key, qwen_key in (('input', 'promptTokenCount', 'prompt_tokens'), ('output', 'candidatesTokenCount', 'completion_tokens'), ('total', 'totalTokenCount', 'total_tokens'), ('thinking', 'thoughtsTokenCount', None)):
            values = [r['token_usage'].get(google_key if cfg['model'].startswith('gemini') else qwen_key) for r in usage_rows]
            values = [v for v in values if type(v) is int]
            token_totals[name] = {'exposed_calls': len(values), 'total': sum(values) if values else None,
                                  'average_per_exposed_call': statistics.mean(values) if values else None}
        wrong = total('wrong_confident')
        per_fixture = {r['fixture_id']: {'http_status': r['http_status'], 'errors': r['error_classes'],
            'schema_valid': r['schema_valid'], 'mandatory_fields_correct': r['score']['field_correct'],
            'whole_order_correct': r['score']['all_fields_correct'], 'proposed_sku': r['score']['proposed_sku'],
            'sku_correct': r['score']['sku_correct'], 'review_success': r['score']['review_success'],
            'wrong_confident_sku': r['score']['wrong_confident'], 'core_status': r['score']['core_status'],
            'all_evidence_grounded': r['evidence_grounding']['all_returned_fields_grounded'] if r['evidence_grounding'] else False,
            'wall_clock_ms': r['wall_clock_ms']} for r in done}
        complete = len(done) == 10
        quote_audits = [r.get('raw_quote_audit') for r in done if r.get('raw_quote_audit') is not None]
        gates = {
            'zero_wrong_confident_skus': wrong == 0 if complete else None,
            'h05_h06_h09_successful_review': len(reviews) == 3 and all(r['score']['review_success'] for r in reviews) if complete else None,
            'h07_h08_correct_distinguishing_sku': all(per_fixture.get(f, {}).get('sku_correct', False) for f in ('h07_not_medium', 'h08_not_large')) if complete else None,
            'zero_ambiguous_clean_promotions': total('ambiguous_clean', reviews) == 0 if complete else None,
            'every_returned_snippet_verbatim': all(a['all_returned_snippets_verbatim'] for a in quote_audits) if complete and quote_audits else None,
            'all_mandatory_fields_match': total('all_fields_correct') == 10 if complete else None,
        }
        candidates.append({'provider': cfg['provider'], 'requested_model': cfg['model'], 'configuration': configuration(cfg),
            'scheduled_calls': 10, 'attempted_calls': len(rows),
            'completed_calls': sum(r['http_status'] is not None and 200 <= r['http_status'] < 300 and bool(r['raw_provider_output']) for r in done),
            'finalized_attempts': len(done),
            'http_2xx_calls': sum(r['http_status'] is not None and 200 <= r['http_status'] < 300 for r in done),
            'parsed_model_outputs': sum(r['json_parsed'] for r in done),
            'schema_valid_rate': ratio(total('schema_valid'), len(rows)),
            'schema_valid_per_scheduled': ratio(total('schema_valid'), 10) if complete else None,
            'mandatory_field_accuracy': ratio(total('field_correct'), 8 * len(done)),
            'whole_order_field_accuracy': ratio(total('all_fields_correct'), len(done)),
            'per_field_accuracy': {name: ratio(sum(r['score']['field_checks'].get(name, False) for r in done), len(done)) for name in list(scorer.HEADER) + ['line.' + f for f in scorer.LINE]},
            'strict_description_mismatches': [r['fixture_id'] for r in done if r['schema_valid'] and not r['score']['field_checks'].get('line.description', False)],
            'determinate_sku_accuracy': ratio(total('sku_correct', determinate), len(determinate)),
            'correct_abstention': ratio(total('sku_correct', reviews), len(reviews)),
            'ambiguity_review_accuracy': ratio(total('review_success', reviews), len(reviews)),
            'h05_h06_ambiguity_review_accuracy': ratio(total('review_success', ambiguous), len(ambiguous)),
            'ambiguous_clean_promotions': total('ambiguous_clean', reviews),
            'wrong_confident_sku_count': wrong,
            'wrong_confident_per_confident_proposal': ratio(wrong, total('confident_proposal')),
            'dangerous_wrong_confident': ratio(total('wrong_confident', dangerous), len(dangerous)),
            'evidence_grounding_rate': ratio(total('provenance_valid'), 8 * len(done)),
            'nonnull_evidence_coverage': ratio(total('nonnull_evidence_valid'), total('nonnull_evidence_expected')),
            'whole_order_evidence_rate': ratio(total('all_provenance_valid'), len(done)),
            'returned_verbatim_snippets': ratio(sum(a['verbatim_snippets'] for a in quote_audits), sum(a['returned_snippets'] for a in quote_audits)),
            'schema_valid_returned_verbatim_snippets': ratio(sum(r['evidence_grounding']['verbatim_snippets'] for r in done if r['evidence_grounding']), sum(r['evidence_grounding']['returned_snippets'] for r in done if r['evidence_grounding'])),
            'provider_failures_by_class': dict(Counter(r['error_class'] for r in done if r['error_class'] in ('AUTH_OR_ACCESS', 'QUOTA', 'TRANSPORT', 'PROVIDER_4XX', 'PROVIDER_5XX'))),
            'all_failures_by_class': dict(Counter(c for r in done for c in r['error_classes'])),
            'latency_all_attempts': latency([r['wall_clock_ms'] for r in done if r['wall_clock_ms'] is not None]),
            'latency_valid_core': latency([r['wall_clock_ms'] for r in done if r['schema_valid'] and not r['score']['core_error']]),
            'token_usage': token_totals, 'hard_semantic_gates': gates,
            'all_hard_gates_pass': all(gates.values()) if complete else None,
            'per_fixture': per_fixture})
    return {'scope': 'PHASE_1_ONLY', 'planned_calls': 30, 'attempted_calls': len(calls),
            'completed_calls': sum(r['state'] != 'ATTEMPT_STARTED' and r['http_status'] is not None and 200 <= r['http_status'] < 300 and bool(r['raw_provider_output']) for r in calls),
            'finalized_attempts': sum(r['state'] != 'ATTEMPT_STARTED' for r in calls),
            'candidates': candidates, 'weighted_score': None, 'winner': None, 'phase2_started': False}


def render(summary):
    def pct(x):
        return f"{x['numerator']}/{x['denominator']} ({100*x['rate']:.1f}%)" if x['rate'] is not None else 'unmeasured'
    lines = ['# OrderShield free-provider Phase 1', '',
        f"Attempts: **{summary['attempted_calls']}/30**; completed model responses: **{summary['completed_calls']}**. One run per fixture/configuration; no automatic retries or substitutions.", '',
        '| Candidate | Attempts/completed | Schema | Fields | Determinate SKU | Review h05/h06/h09 | Wrong confident | Evidence/null validity | p50 ms | p95 ms* | Min/max ms |',
        '|---|---|---|---|---|---|---|---|---|---|---|']
    for c in summary['candidates']:
        d = c['latency_all_attempts'] or {}
        lines.append(f"| {c['requested_model']} | {c['attempted_calls']}/{c['completed_calls']} | {pct(c['schema_valid_rate'])} | {pct(c['mandatory_field_accuracy'])} | {pct(c['determinate_sku_accuracy'])} | {pct(c['ambiguity_review_accuracy'])} | {c['wrong_confident_sku_count']} | {pct(c['evidence_grounding_rate'])} | {d.get('p50_ms')} | {d.get('p95_ms')} | {d.get('min_ms')}/{d.get('max_ms')} |")
    lines += ['', '*With ten observations, nearest-rank p95 equals the maximum and is descriptive only. Wall latency covers HTTP request/response, not model-only processing; it excludes local scoring.', '', '## Required fixture results', '',
        '| Candidate | Fixture | HTTP | Fields /8 | Proposed SKU | Review | Core status | Wrong confident | Evidence | Errors |',
        '|---|---|---|---|---|---|---|---|---|---|']
    special = ('h05_no_size', 'h06_no_pack', 'h07_not_medium', 'h08_not_large', 'h09_latex')
    for c in summary['candidates']:
        for name in special:
            r = c['per_fixture'].get(name)
            if r:
                lines.append(f"| {c['requested_model']} | {name} | {r['http_status']} | {r['mandatory_fields_correct']} | {r['proposed_sku']} | {r['review_success']} | {r['core_status']} | {r['wrong_confident_sku']} | {r['all_evidence_grounded']} | {', '.join(r['errors']) or 'none'} |")
            else:
                lines.append(f"| {c['requested_model']} | {name} | unattempted | — | — | — | — | — | — | unmeasured |")
    lines += ['', '## Gates, failures, and usage', '']
    for c in summary['candidates']:
        lines += [f"- **{c['requested_model']}**: hard gates={json.dumps(c['hard_semantic_gates'])}; all gates pass={c['all_hard_gates_pass']}.",
            f"  Provider failures={json.dumps(c['provider_failures_by_class'])}; all failure classes={json.dumps(c['all_failures_by_class'])}.",
            f"  Tokens (total/average per exposed call): {json.dumps(c['token_usage'])}.",
            f"  Whole-order fields={pct(c['whole_order_field_accuracy'])}; non-null evidence={pct(c['nonnull_evidence_coverage'])}; returned verbatim snippets={pct(c['returned_verbatim_snippets'])}; strict description mismatches={c['strict_description_mismatches']}."]
    lines += ['', '## Method and boundaries', '',
        'Frozen inputs, expected labels, schema, 0.90 threshold and 0.15 margin are unchanged. Missing original harness files were recovered as exact hash-matching Git blobs from 12a7e50 under frozen_reference. The original CLI adapter/runner are verified from Git history and never executed.',
        'The current human instruction overrides the historical protocol provider/CLI isolation and repetition settings: three specified direct API configurations, each receiving h01–h10 once, run index 1; candidate-major order. No previous output, labels, tools, search, grounding, or real documents are sent.',
        'All models receive the identical frozen prompt/catalog/source composition. Gemini receives the unchanged schema through responseFormat.text; Qwen receives JSON Object mode and the same schema in a system format instruction. Local validation is identical and strict.',
        'The grounding boundary fix is confined to this evaluator: literal quotes must still occur verbatim in the source; a terminal sentence dot does not invalidate a supported value, while substrings within words and decimal numbers remain rejected. Strict expected-value comparison still preserves punctuation.',
        'The copied deterministic core performs arithmetic and contract/tier checks locally only to reproduce frozen expected statuses. AI is limited to extraction, candidate ranking, ambiguity classification, and verbatim evidence. No product runtime or approval is implemented.',
        'Failed/invalid calls remain in observed denominators and receive zero field/SKU/review credit. Unattempted metrics stay unmeasured. Correct abstention on review inputs is reported separately from successful core review. Wrong-confident proposals are counted before later evidence/core rejection.',
        'Completed calls means an HTTP 2xx response containing non-empty final model text; schema validity is measured separately. Finalized attempts include provider and transport failures.',
        'Every evidence field on every returned line is checked; expected-field coverage uses the frozen eight fields and 76 non-null fields per candidate. h10 has four expected null fields.',
        'Raw model final text is retained without repair. Private reasoning and request headers are excluded. Credentials and endpoint values are never persisted; response echo redaction is applied defensively.',
        'Free-only account settings rely on the explicit user confirmation from preflight. Accepted requests do not establish remaining free quota. Self-reported confidence is not calibrated; ten shared fixtures do not establish broad reliability.',
        'The 180-second diagnostic socket timeout follows the frozen experiment; it does not establish the product’s 4.5-second client timeout/5-second failure requirement. Authentication failure stops the remaining batch as required by the historical protocol.',
        'No weighted overall score, winner selection, or Phase 2 is produced.', '',
        'Provider modes: [Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output), [Qwen Singapore JSON Object and JSON Schema exclusion](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output).', '']
    return '\n'.join(lines)


def write(path, value, secrets=()):
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[REDACTED]')
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8', newline='\n')
    # Windows scanners can briefly hold an artifact open. These are local file
    # write retries only; transport is never retried and checkpoint precedes send.
    for attempt in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.05 * 2 ** attempt)


def execute(send=transport):
    if METADATA.exists() or CALLS.exists():
        raise ValueError('Phase 1 already started: refusing rerun or selective retry')
    verified = integrity()
    preflight = read(ROOT / 'preflight.json')
    passed = preflight['remediation_rounds'][0]['candidates']
    if not all(any(p['requested_model_id'] == c['model'] and p['phase1_eligible'] for p in passed) for c in CANDIDATES):
        raise ValueError('Required live preflight not passed')
    env, env_source = environment()
    secrets = list(env.values()) + [os.environ.get('QWEN_BASE_URL', '')]
    truth = read(CORPUS / 'live/expected.json')
    catalog = read(CORPUS / 'catalog.json')
    prompt = (CORPUS / 'prompt.txt').read_text(encoding='utf-8')
    preflight_hashes = {name: sha(ROOT / name) for name in ('preflight.json', 'preflight.md')}
    metadata = {'started_utc': datetime.now(timezone.utc).isoformat(), 'scope': 'PHASE_1_ONLY',
        'planned_calls': 30, 'run_index': 1, 'configurations': [dict(c, **configuration(c)) for c in CANDIDATES],
        'free_only_settings': 'USER_CONFIRMED_IN_PREFLIGHT', 'qwen_environment_source': env_source,
        'call_order': 'candidate-major; h01..h10 per candidate', 'integrity_before': verified,
        'preflight_hashes_before': preflight_hashes,
        'protocol_overrides': ['User specified three free direct API providers instead of frozen Codex CLI', 'One run per fixture/provider instead of three runs of one provider', 'HTTP wall latency instead of CLI subprocess latency', 'User authorized terminal-punctuation grounding repair'],
        'harness_sha256': sha(Path(__file__)), 'local_schema_unchanged': True, 'stop_reason': None}
    # Exclusive creation is a persistent whole-batch rerun guard.
    with METADATA.open('x', encoding='utf-8') as file:
        json.dump(metadata, file, indent=2)
        file.write('\n')
    calls = []
    def checkpoint():
        write(CALLS, {'scope': 'PHASE_1_ONLY', 'calls': calls}, secrets)
        summary = summarize(calls, truth)
        summary['stop_reason'] = metadata['stop_reason']
        write(SUMMARY, summary, secrets)
        write(REPORT, render(summary), secrets)
    checkpoint()
    stop = False
    for cfg in CANDIDATES:
        for fixture, expected in truth.items():
            integrity()
            source = (CORPUS / 'live/fixtures' / (fixture + '.txt')).read_text(encoding='utf-8')
            text = request_text(source, catalog, prompt)
            request = build_request(cfg, text, env)
            row = {'provider': cfg['provider'], 'requested_model_id': cfg['model'], 'returned_model_id': None,
                'configuration': configuration(cfg), 'fixture_id': fixture, 'run_index': 1,
                'state': 'ATTEMPT_STARTED', 'started_utc': datetime.now(timezone.utc).isoformat(),
                'source_sha256': sha(CORPUS / 'live/fixtures' / (fixture + '.txt')),
                'request_body_sha256': hashlib.sha256(request.data).hexdigest(),
                'http_status': None, 'provider_outcome': None, 'finish_reason': None,
                'schema_valid': False, 'json_parsed': False, 'raw_provider_output': None,
                'evidence_grounding': None, 'token_usage': None, 'wall_clock_ms': None,
                'raw_quote_audit': None,
                'error_class': None, 'error_classes': [],
                'mandatory_field_correctness': None, 'determinate_sku_correctness': None,
                'ambiguity_review_correctness': None, 'wrong_confident_sku': None,
                'score': scorer.score_response(None, source, expected, catalog)}
            calls.append(row)
            if len(calls) > 30:
                raise ValueError('Call budget exceeded')
            checkpoint()  # Durably record the attempt before sending; no recovery retries.
            start = time.perf_counter()
            try:
                response_metadata = {}
                status, raw = send(request, response_metadata=response_metadata) if send is transport else send(request)
                row['wall_clock_ms'] = round((time.perf_counter() - start) * 1000, 2)
                row['http_status'] = status
                if 200 <= status < 300:
                    row['provider_outcome'] = 'HTTP_SUCCESS'
                    try:
                        final, actual, usage, finish = envelope(cfg, raw)
                        row.update(raw_provider_output=final, returned_model_id=actual,
                                   token_usage=usage, finish_reason=finish)
                        row['raw_quote_audit'] = raw_quote_audit(final, source)
                        score, evidence, errors = evaluate(final, source, expected, catalog)
                        row.update(score=score, evidence_grounding=evidence, error_classes=errors,
                                   schema_valid=score['schema_valid'], json_parsed='MALFORMED_JSON' not in errors)
                    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                        row['error_classes'] = ['MALFORMED_JSON']
                else:
                    row['provider_outcome'] = 'HTTP_ERROR'
                    row['error_classes'] = [provider_failure(status, raw)]
                    if cfg['model'].startswith('gemini'):
                        row['safe_error'] = safe_gemini_error(status, raw, cfg['model'], secrets, response_metadata.get('request_id'))
            except (OSError, urllib.error.URLError, TimeoutError):
                row['wall_clock_ms'] = round((time.perf_counter() - start) * 1000, 2)
                row['provider_outcome'] = 'TRANSPORT_FAILURE'
                row['error_classes'] = ['TRANSPORT']
            except Exception:
                row['wall_clock_ms'] = round((time.perf_counter() - start) * 1000, 2)
                row['provider_outcome'] = 'LOCAL_VALIDATION_FAILURE'
                row['error_classes'] = ['LOCAL_VALIDATION_FAILURE']
                metadata['stop_reason'] = 'Unexpected local validation failure; no further calls'
                stop = True
            row['error_class'] = row['error_classes'][0] if row['error_classes'] else None
            row['mandatory_field_correctness'] = row['score']['field_checks']
            row['determinate_sku_correctness'] = row['score']['sku_correct'] if expected['sku'] is not None else None
            row['ambiguity_review_correctness'] = row['score']['review_success'] if expected['needs_sku_review'] else None
            row['wrong_confident_sku'] = row['score']['wrong_confident']
            row['state'] = 'COMPLETED'
            row['completed_utc'] = datetime.now(timezone.utc).isoformat()
            if row['error_class'] == 'AUTH_OR_ACCESS':
                metadata['stop_reason'] = 'Authentication/access failure; frozen protocol requires stopping remaining calls'
                stop = True
            checkpoint()
            print(f"{len(calls):02}/30 {cfg['model']} {fixture}: HTTP {row['http_status']}; {row['error_class'] or 'PASS'}; {row['wall_clock_ms']} ms", flush=True)
            if stop:
                break
        if stop:
            break
    metadata['integrity_after'] = integrity()
    metadata['preflight_preserved'] = all(sha(ROOT / name) == value for name, value in preflight_hashes.items())
    metadata['completed_utc'] = datetime.now(timezone.utc).isoformat()
    metadata['attempted_calls'] = len(calls)
    write(METADATA, metadata, secrets)
    checkpoint()
    return 0 if len(calls) == 30 else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'run', 'summarize'))
    args = parser.parse_args()
    if args.action == 'validate':
        integrity()
        print('PASS: all frozen Git/input hashes and application schema match; no live calls')
        return 0
    if args.action == 'run':
        return execute()
    data = read(CALLS)
    summary = summarize(data['calls'], read(CORPUS / 'live/expected.json'))
    write(SUMMARY, summary)
    write(REPORT, render(summary))
    print('Summary regenerated from recorded calls only; no live calls')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
