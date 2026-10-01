"""Bounded synthetic preflight. Standard library only; no retries or redirects."""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
MODELS = ('gemini-3.7-flash', 'gemini-3.8-flash', 'qwen3.8-flash')
PROMPT = 'Return only this compact JSON object: {"status":"ok","item":"synthetic-widget"}'
EXPECTED = {'status': 'ok', 'item': 'synthetic-widget'}


def qwen_url(base):
    u = urlsplit(base)
    host = u.hostname or ''
    allowed = host == 'dashscope-intl.aliyuncs.com' or bool(re.fullmatch(
        r'[a-zA-Z0-9-]+\.ap-southeast-1\.maas\.aliyuncs\.com', host))
    if (u.scheme != 'https' or not allowed or u.port not in (None, 443)
            or u.username or u.password or u.query or u.fragment
            or u.path.rstrip('/') != '/compatible-mode/v1'):
        raise ValueError('Qwen endpoint must be an approved Singapore HTTPS compatible-mode base')
    return base.rstrip('/') + '/chat/completions'


def request_for(model, env):
    if model not in MODELS:
        raise ValueError('Exact candidate models only')
    headers = {'Content-Type': 'application/json'}
    if model.startswith('gemini'):
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        headers['x-goog-api-key'] = env['GEMINI_API_KEY']
        body = {'contents': [{'role': 'user', 'parts': [{'text': PROMPT}]}],
                'generationConfig': {'thinkingConfig': {'thinkingLevel': 'low'},
                                     'responseMimeType': 'application/json',
                                     'maxOutputTokens': 256}}
    else:
        url = qwen_url(env['QWEN_BASE_URL'])
        headers['Authorization'] = 'Bearer ' + env['QWEN_API_KEY']
        # Structured-output support for this exact endpoint/model is unverified.
        # Use the authorized compact-JSON prompt path, with local validation.
        body = {'model': model, 'messages': [{'role': 'user', 'content': PROMPT}],
                'enable_thinking': False, 'stream': False, 'max_tokens': 128}
    return urllib.request.Request(url, json.dumps(body).encode(), headers, method='POST')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(request):
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, response.read(1048576)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read(1048576)


def failure_class(status, raw):
    # Inspect errors in memory only; never persist error bodies or exception text.
    text = raw.decode('utf-8', errors='replace').lower()
    if status == 429 or any(s in text for s in ('quota', 'resource_exhausted', 'rate_limit', 'freeTierOnly'.lower())):
        return 'QUOTA'
    if status in (401, 403):
        return 'AUTH_OR_ACCESS'
    if status == 404 or any(s in text for s in ('model_not_found', 'modelnotfound', 'model not exist', 'model is not supported')):
        return 'MODEL_UNAVAILABLE'
    if status >= 500:
        return 'PROVIDER_5XX'
    if status >= 400:
        return 'PROVIDER_4XX'
    return 'TRANSPORT'


def parse_response(row, raw):
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        row['response_json_parsed'] = True
        google = row['requested_model_id'].startswith('gemini')
        actual = data.get('modelVersion' if google else 'model')
        # Persist only model-shaped IDs, not arbitrary provider text.
        if isinstance(actual, str) and re.fullmatch(r'[a-zA-Z0-9._:/-]{1,160}', actual):
            row['returned_model_id'] = actual
        usage = data.get('usageMetadata' if google else 'usage', {})
        def counts(obj):
            if not isinstance(obj, dict):
                return {}
            return {k: counts(v) if isinstance(v, dict) else v for k, v in obj.items()
                    if re.fullmatch(r'[a-zA-Z_]{1,80}', k)
                    and (type(v) is int or isinstance(v, dict))}
        row['token_usage'] = counts(usage) or None
        if google:
            parts = data['candidates'][0]['content']['parts']
            content = ''.join(p['text'] for p in parts if not p.get('thought') and 'text' in p)
        else:
            content = data['choices'][0]['message']['content']
        obj = json.loads(content)
        row['json_parsed'] = True
        row['expected_fields_present'] = isinstance(obj, dict) and all(k in obj for k in EXPECTED)
        row['expected_values_match'] = isinstance(obj, dict) and all(obj.get(k) == v for k, v in EXPECTED.items())
        if not row['expected_values_match']:
            raise ValueError()
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        row['failure_class'] = 'MALFORMED_JSON'
        row['failure_detail'] = 'Invalid response envelope, JSON syntax, or expected object values'


def run(env, free_confirmed=False, send=transport, checkpoint=lambda report: None):
    report = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'PREFLIGHT_ONLY', 'live_calls_attempted': 0,
              'free_only_account_settings': 'USER_CONFIRMED' if free_confirmed else 'UNCONFIRMED',
              'candidates': []}
    for model in MODELS:
        google = model.startswith('gemini')
        required = ['GEMINI_API_KEY'] if google else ['QWEN_API_KEY', 'QWEN_BASE_URL']
        missing = [key for key in required if not env.get(key)]
        row = {'provider': 'Google Gemini API' if google else 'Alibaba Model Studio (Singapore)',
               'requested_model_id': model, 'returned_model_id': None,
               'thinking': {'thinking_level': 'low'} if google else {'enable_thinking': False},
               'output_mode': 'application/json' if google else 'compact JSON prompt; local validation',
               'state': 'NOT_CALLED', 'http_status': None, 'wall_clock_ms': None,
               'response_json_parsed': None, 'json_parsed': None,
               'expected_fields_present': None, 'expected_values_match': None,
               'token_usage': None, 'failure_class': None, 'blockers': [],
               'access_status': 'UNVERIFIED', 'quota_status': 'UNVERIFIED',
               'phase1_eligible': False}
        report['candidates'].append(row)
        if missing:
            row['blockers'].append('Missing environment variables: ' + ', '.join(missing))
        if not free_confirmed:
            row['blockers'].append('Account free-only settings unconfirmed')
        if row['blockers']:
            continue
        try:
            request = request_for(model, env)
        except (ValueError, KeyError):
            row['blockers'].append('Invalid or non-Singapore endpoint configuration')
            continue
        row['state'] = 'ATTEMPT_STARTED'
        report['live_calls_attempted'] += 1
        checkpoint(report)  # Durable attempt marker BEFORE sending, even on interruption.
        start = time.perf_counter()
        try:
            status, raw = send(request)
            row['http_status'] = status
            if 200 <= status < 300:
                row['access_status'] = 'ACCEPTED'
                row['quota_status'] = 'REQUEST_ACCEPTED; remaining quota unknown'
                parse_response(row, raw)
            else:
                row['failure_class'] = failure_class(status, raw)
                row['access_status'] = 'DENIED' if row['failure_class'] == 'AUTH_OR_ACCESS' else 'UNVERIFIED'
                if row['failure_class'] == 'QUOTA':
                    row['quota_status'] = 'QUOTA_OR_RATE_LIMIT_REJECTED'
        except (OSError, urllib.error.URLError, TimeoutError):
            row['failure_class'] = 'TRANSPORT'
        finally:
            row['wall_clock_ms'] = round((time.perf_counter() - start) * 1000, 2)
        row['state'] = 'FAILED' if row['failure_class'] else 'PASSED'
        row['phase1_eligible'] = row['state'] == 'PASSED'
        checkpoint(report)
    return report


def save(report):
    (ROOT / 'preflight.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# OrderShield provider preflight', '',
             f"Live model calls attempted: **{report['live_calls_attempted']} / 3 maximum**.",
             f"Free-only account settings: {report['free_only_account_settings']}.", '',
             '| Provider | Requested model | Result | HTTP | Latency ms | Phase 1 eligible |',
             '|---|---|---|---|---|---|']
    for r in report['candidates']:
        lines.append(f"| {r['provider']} | {r['requested_model_id']} | {r['state']} | {r['http_status']} | {r['wall_clock_ms']} | {r['phase1_eligible']} |")
    lines += ['', '## Evidence and limitations', '']
    for r in report['candidates']:
        lines.append(f"- {r['requested_model_id']}: access={r['access_status']}; quota={r['quota_status']}; failure={r['failure_class']}; blockers={'; '.join(r['blockers']) or 'none'}.")
    lines += ['', 'Null measurements mean no observation, never zero latency or zero tokens.',
              'NOT_CALLED is a local prerequisite block, not a provider failure or proof of unavailable models.',
              'No h01-h10 fixture, real document, product implementation, or Phase 1 evaluation is used.',
              'Qwen uses enable_thinking=false and compact JSON prompting because exact Singapore structured-output support is unverified.',
              'Gemini uses thinkingConfig.thinkingLevel=low, JSON MIME type, and no tools/search/grounding.',
              'One accepted tiny request does not establish remaining evaluation quota, quality, or production latency compliance.',
              'The 30-second preflight socket timeout is diagnostic only; it does not redefine the frozen product timeout.',
              'MALFORMED_JSON also covers valid JSON that fails the required object validation.',
              'No raw provider bodies, prompts with real data, keys, environment values, or exception messages are persisted.',
              '', '## Run', '',
              '`python -B spikes/ordershield/provider_bakeoff/preflight.py` records blocked prerequisites without sending requests.',
              'After supplying the three environment variables to the execution process and confirming both account settings:',
              '`python -B spikes/ordershield/provider_bakeoff/preflight.py --confirm-free-only`',
              'The flag attests Gemini Free tier and active Alibaba Free Quota Only for this exact model; it does not configure billing.',
              'A persisted live attempt prevents all later runs, including automatic retries after interruption.',
              '`python -B -m unittest discover -s spikes/ordershield/provider_bakeoff -p "test_*.py" -v` runs offline tests.',
              '', '## Provider references', '',
              '- https://ai.google.dev/gemini-api/docs/generate-content/thinking',
              '- https://ai.google.dev/gemini-api/docs/pricing',
              '- https://www.alibabacloud.com/help/en/model-studio/deep-thinking',
              '- https://www.alibabacloud.com/help/en/model-studio/new-free-quota', '']
    (ROOT / 'preflight.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-free-only', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'preflight.json'
    if path.exists() and json.loads(path.read_text(encoding='utf-8')).get('live_calls_attempted', 0):
        parser.exit(2, 'Refusing rerun: a live attempt is already recorded.\n')
    report = run(os.environ, args.confirm_free_only, checkpoint=save)
    save(report)
    print(f"Live calls attempted: {report['live_calls_attempted']}; reports saved beside this script.")


if __name__ == '__main__':
    main()
