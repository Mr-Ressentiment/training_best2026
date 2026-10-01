"""Offline tests: injected transport only, no real credentials or network."""
import json
import unittest
from unittest.mock import patch

import preflight as p

ENV = {'GEMINI_API_KEY': 'synthetic-secret-google',
       'QWEN_API_KEY': 'synthetic-secret-qwen',
       'QWEN_BASE_URL': 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'}


def success(request):
    if 'googleapis.com' in request.full_url:
        return 200, json.dumps({'modelVersion': 'gemini-test-version',
            'candidates': [{'content': {'parts': [{'text': json.dumps(p.EXPECTED)}]}}],
            'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 12}}).encode()
    return 200, json.dumps({'model': 'qwen3.8-flash',
        'choices': [{'message': {'content': json.dumps(p.EXPECTED)}}],
        'usage': {'prompt_tokens': 20, 'completion_tokens': 12,
                  'completion_tokens_details': {'reasoning_tokens': 0}}}).encode()


class PreflightTests(unittest.TestCase):
    def test_exact_configs(self):
        for model in p.MODELS:
            req = p.request_for(model, ENV)
            body = json.loads(req.data)
            self.assertNotIn('tools', body)
            if model.startswith('gemini'):
                self.assertIn(model + ':generateContent', req.full_url)
                self.assertEqual(body['generationConfig']['thinkingConfig'], {'thinkingLevel': 'low'})
            else:
                self.assertEqual(body['model'], model)
                self.assertIs(body['enable_thinking'], False)
                self.assertNotIn('response_format', body)

    def test_endpoint_boundaries(self):
        for base in ('https://api.openai.com/v1', 'http://dashscope-intl.aliyuncs.com/compatible-mode/v1',
                     'https://dashscope.aliyuncs.com/compatible-mode/v1',
                     ENV['QWEN_BASE_URL'] + '?key=secret',
                     'https://user:secret@dashscope-intl.aliyuncs.com/compatible-mode/v1'):
            with self.subTest(base=base), self.assertRaises(ValueError):
                p.qwen_url(base)
        self.assertTrue(p.qwen_url('https://example.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1').endswith('/chat/completions'))

    def test_missing_environment_no_calls(self):
        with patch.object(p, 'transport', side_effect=AssertionError('network forbidden')):
            result = p.run({}, True, send=lambda req: self.fail('must not send'))
        self.assertEqual(result['live_calls_attempted'], 0)
        for row in result['candidates']:
            self.assertEqual(row['state'], 'NOT_CALLED')
            self.assertIsNone(row['wall_clock_ms'])
            self.assertIsNone(row['failure_class'])

    def test_free_only_gate(self):
        result = p.run(ENV, False, send=lambda req: self.fail('must not send'))
        self.assertEqual(result['live_calls_attempted'], 0)

    def test_three_calls_with_usage_and_no_secrets(self):
        calls = []
        checkpoints = []
        def send(req):
            calls.append(req)
            self.assertEqual(checkpoints[-1], len(calls))
            return success(req)
        result = p.run(ENV, True, send, lambda r: checkpoints.append(r['live_calls_attempted']))
        self.assertEqual(len(calls), 3)
        for row in result['candidates']:
            self.assertTrue(row['phase1_eligible'])
            self.assertTrue(row['json_parsed'])
            self.assertTrue(row['expected_fields_present'])
            self.assertIsNotNone(row['token_usage'])
            self.assertIsNotNone(row['returned_model_id'])
            self.assertGreaterEqual(row['wall_clock_ms'], 0)
        for key in ('GEMINI_API_KEY', 'QWEN_API_KEY'):
            self.assertNotIn(ENV[key], json.dumps(result))

    def test_http_failure_classes(self):
        for status, body, expected in ((401, b'', 'AUTH_OR_ACCESS'),
                (403, b'', 'AUTH_OR_ACCESS'), (404, b'', 'MODEL_UNAVAILABLE'),
                (400, b'model_not_found', 'MODEL_UNAVAILABLE'),
                (429, b'', 'QUOTA'), (400, b'AllocationQuota.FreeTierOnly', 'QUOTA'),
                (400, b'', 'PROVIDER_4XX'), (503, b'', 'PROVIDER_5XX'),
                (302, b'', 'TRANSPORT')):
            self.assertEqual(p.failure_class(status, body), expected)

    def test_no_retry_on_failures(self):
        calls = []
        def failed(req):
            calls.append(req)
            raise TimeoutError('synthetic-secret-google')
        result = p.run(ENV, True, failed)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(r['failure_class'] == 'TRANSPORT' for r in result['candidates']))
        self.assertNotIn('synthetic-secret-google', json.dumps(result))

    def test_malformed_envelope_and_object(self):
        for raw in (b'not json', b'[]', b'{}',
                    b'{"choices":[{"message":{"content":"[]"}}]}',
                    b'{"choices":[{"message":{"content":"{\\"status\\":\\"wrong\\"}"}}]}'):
            result = p.run(ENV, True, lambda req: (200, raw))
            self.assertTrue(all(r['failure_class'] == 'MALFORMED_JSON' for r in result['candidates']))
            self.assertFalse(any(r['phase1_eligible'] for r in result['candidates']))

    def test_redirects_disabled(self):
        self.assertIsNone(p.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))


if __name__ == '__main__':
    unittest.main()
