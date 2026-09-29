"""Offline sanitization and call-budget tests; no provider transport is invoked."""
import io
import hashlib
import json
from email.message import Message
import unittest
import urllib.error
from unittest.mock import patch

import gemini_diagnostic as d
import phase1 as p


class DiagnosticTests(unittest.TestCase):
    def test_allowlist_and_redaction_before_truncation(self):
        secret = 'OFFLINE_SYNTHETIC_SECRET_12345'
        raw = json.dumps({'error': {'status': 'INVALID_ARGUMENT',
            'message': 'Bad request ' + secret + ' Bearer synthetic-token ?key=synthetic-key ' + 'x' * 3000,
            'details': [{'@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': 'FIELD_UNSUPPORTED',
                         'metadata': {'requestId': 'r-123', 'private': secret}}],
            'private': secret}, 'request_headers': {'Authorization': secret}}).encode()
        result = p.safe_gemini_error(400, raw, 'gemini-3.5-flash-lite', (secret,))
        self.assertEqual(result['gemini_error_status'], 'INVALID_ARGUMENT')
        self.assertEqual(result['error_info_reasons'], ['FIELD_UNSUPPORTED'])
        self.assertEqual(result['request_id'], 'r-123')
        self.assertEqual(len(result['gemini_error_message']), 2048)
        for value in (secret, 'synthetic-token', 'synthetic-key', 'request_headers', 'private'):
            self.assertNotIn(value, json.dumps(result))

    def test_non_json_or_illtyped_errors_do_not_leak_raw_body(self):
        for raw in (b'private HTML', b'[]', b'{"error":null}', b'{"error":{"message":{},"details":{}}}'):
            result = p.safe_gemini_error(400, raw, 'gemini-3.5-flash-lite')
            self.assertIsNone(result['gemini_error_message'])
            self.assertEqual(result['error_info_reasons'], [])

    def test_error_transport_returns_only_selected_request_id(self):
        headers = Message()
        headers['x-request-id'] = 'response-id'
        headers['Authorization'] = 'PRIVATE-HEADER'
        error = urllib.error.HTTPError('https://example.test', 400, 'bad', headers,
                                       io.BytesIO(b'{"error":{"message":"unsupported"}}'))
        meta = {}
        with patch.object(p.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            status, raw = p.transport(object(), response_metadata=meta)
            opener.return_value.open.assert_called_once()
        self.assertEqual(status, 400)
        self.assertEqual(meta, {'request_id': 'response-id'})
        self.assertNotIn('PRIVATE-HEADER', json.dumps(meta))

    def test_full_schema_subset_audit(self):
        audit = d.schema_audit()
        self.assertEqual(audit['unsupported_keywords_found'], [])
        self.assertEqual(audit['nullable_union_nodes'], 8)
        self.assertTrue(audit['all_objects_closed_and_fully_required'])
        self.assertEqual(audit['full_schema'], p.SCHEMA)

    def test_call_guards_reserve_no_extra_live_attempts(self):
        before = d.protected()
        for variant, calls, authorized in [('original', [{}], False), ('corrected', [], True),
                                          ('corrected', [{}], False), ('corrected', [{}, {}], True)]:
            with self.subTest(variant=variant, calls=len(calls)):
                data = {'offline_completed_utc': 'OFFLINE', 'protected_sha256_before': before,
                        'calls': calls, 'bounded_fix_authorized_by_evidence': authorized}
                with patch.object(p, 'read', return_value=data), patch.object(p, 'transport') as send:
                    with self.assertRaises(ValueError):
                        d.run_one(variant)
                    send.assert_not_called()

    def test_only_mime_enum_changes_in_both_shared_gemini_payloads(self):
        source = (p.CORPUS / 'live/fixtures/h01_paper.txt').read_text(encoding='utf-8')
        text = p.request_text(source, p.read(p.CORPUS / 'catalog.json'),
                              (p.CORPUS / 'prompt.txt').read_text(encoding='utf-8'))
        previous = p.read(p.CALLS)['calls']
        for candidate in (p.CANDIDATES[0], p.CANDIDATES[2]):
            request = p.build_request(candidate, text, {'GEMINI_API_KEY': 'OFFLINE_SENTINEL'})
            body = json.loads(request.data)
            self.assertEqual(body['generationConfig']['responseFormat']['text']['mimeType'], 'APPLICATION_JSON')
            self.assertEqual(body['generationConfig']['responseFormat']['text']['schema'], p.SCHEMA)
            body['generationConfig']['responseFormat']['text']['mimeType'] = 'application/json'
            original = next(c for c in previous if c['requested_model_id'] == candidate['model'] and c['fixture_id'] == 'h01_paper')
            self.assertEqual(hashlib.sha256(json.dumps(body, ensure_ascii=False).encode('utf-8')).hexdigest(), original['request_body_sha256'])

    def test_budget_exhausted_after_recorded_live_diagnostic(self):
        data = p.read(d.JSON)
        self.assertEqual(len(data['calls']), 2)
        with patch.object(p, 'transport') as send:
            for variant in ('original', 'corrected'):
                with self.assertRaises(ValueError):
                    d.run_one(variant)
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
