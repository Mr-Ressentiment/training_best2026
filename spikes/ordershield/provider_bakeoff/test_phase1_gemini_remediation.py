"""Offline doubles verify call limits, reuse, strict scoring and the 3.8 gate."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import phase1 as p
import phase1_gemini_remediation as m


class RemediationTests(unittest.TestCase):
    def setUp(self):
        self.truth = p.read(p.CORPUS / 'live/expected.json')

    def response(self, fixture):
        truth = self.truth[fixture]
        def field(value):
            return {'value': value, 'quote': value if value is not None else ''}
        obj = {name: field(value) for name, value in zip(p.scorer.HEADER, truth['header'])}
        line = {name: field(value) for name, value in zip(p.scorer.LINE, truth['line'])}
        line.update(candidates=[] if truth['sku'] is None else [{'sku': truth['sku'], 'score': .99, 'reason': 'OFFLINE TEST DOUBLE'}],
                    ambiguous=truth['needs_sku_review'], uncertainty='OFFLINE TEST DOUBLE')
        obj.update(lines=[line], extraction_issues=[])
        return obj

    def send(self, seen, gate_failure=None, auth_failure=False):
        def fake(request, response_metadata=None):
            model = request.full_url.split('/models/')[1].split(':')[0]
            self.assertIn(model, (c['model'] for c in m.CANDIDATES))
            body = json.loads(request.data)
            text = body['contents'][0]['parts'][0]['text']
            fixture = next(f for f, truth in self.truth.items() if truth['header'][1] in text)
            seen.append((model, fixture))
            self.assertLessEqual(len(seen), 19)
            self.assertEqual(body['generationConfig']['responseFormat']['text']['schema'], p.SCHEMA)
            self.assertEqual(body['generationConfig']['responseFormat']['text']['mimeType'], 'APPLICATION_JSON')
            if auth_failure:
                return 403, b'{"error":{"status":"PERMISSION_DENIED","message":"OFFLINE TEST DOUBLE"}}'
            if model == 'gemini-3.8-flash' and fixture == 'h01_paper' and gate_failure == '400':
                response_metadata['request_id'] = 'offline-id'
                return 400, b'{"error":{"status":"INVALID_ARGUMENT","message":"OFFLINE request incompatibility"}}'
            result = self.response(fixture)
            if model == 'gemini-3.8-flash' and fixture == 'h01_paper' and gate_failure == 'schema':
                result['extra'] = True
            if model == 'gemini-3.8-flash' and fixture == 'h01_paper' and gate_failure == 'semantic':
                result['lines'][0]['candidates'][0]['sku'] = 'GLOVE-N-L'
            return 200, json.dumps({'modelVersion': model,
                'candidates': [{'content': {'parts': [{'text': json.dumps(result)}]}, 'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1, 'totalTokenCount': 2}}).encode()
        return fake

    def run_batch(self, gate_failure=None, auth_failure=False):
        seen = []
        with tempfile.TemporaryDirectory(dir=p.ROOT, prefix='offline-remediation-') as folder:
            root = Path(folder)
            targets = {'CALLS': root/'calls.json', 'SUMMARY': root/'summary.json',
                       'METADATA': root/'metadata.json', 'REPORT': root/'report.md'}
            with patch.multiple(m, **targets), patch.dict(os.environ, {'GEMINI_API_KEY': 'OFFLINE_SENTINEL'}), patch.object(p, 'transport') as live:
                code = m.execute(self.send(seen, gate_failure, auth_failure))
                live.assert_not_called()
                calls = p.read(m.CALLS)['calls']
                summary = p.read(m.SUMMARY)
                metadata = p.read(m.METADATA)
                with self.assertRaises(ValueError):
                    m.execute(self.send(seen))
                live.assert_not_called()
        return code, seen, calls, summary, metadata

    def test_h01_exact_reuse_and_qwen_comparability(self):
        row, checks = m.verify_reuse()
        self.assertTrue(all(checks.values()))
        self.assertFalse(row['new_live_attempt'])
        self.assertEqual(row['fixture_id'], 'h01_paper')
        self.assertEqual(row['reused_from']['diagnostic_call_index'], 2)
        self.assertEqual(row['wall_clock_ms'], 2006.6)
        self.assertEqual(m.verify_qwen_comparability()['new_qwen_calls'], 0)

    def test_reuse_rejects_configuration_or_fixture_or_output_drift(self):
        original = p.read(m.DIAGNOSTIC)
        original_read = p.read
        for mutate in (lambda d: d['calls'][1]['configuration']['thinking'].update(thinking_level='low'),
                       lambda d: d['calls'][1].update(fixture_id='h02_large'),
                       lambda d: d['calls'][1].update(request_body_sha256='invalid'),
                       lambda d: d['calls'][1].update(raw_provider_output='{}')):
            data = copy.deepcopy(original)
            mutate(data)
            def read(path):
                return data if path == m.DIAGNOSTIC else original_read(path)
            with patch.object(p, 'read', side_effect=read), patch.object(p, 'transport') as live:
                with self.assertRaises(ValueError):
                    m.verify_reuse()
                live.assert_not_called()

    def test_full_plan_exactly_19_new_and_one_reuse(self):
        code, seen, rows, summary, metadata = self.run_batch()
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 19)
        self.assertEqual(len(set(seen)), 19)
        self.assertNotIn(('gemini-3.5-flash-lite', 'h01_paper'), seen)
        self.assertEqual(len(rows), 20)
        self.assertEqual(summary['new_live_attempts'], 19)
        self.assertEqual(summary['reused_calls'], 1)
        self.assertEqual(summary['completed_calls'], 20)
        self.assertTrue(metadata['protected_evidence_preserved'])
        self.assertEqual(len(summary['candidates']), 2)
        self.assertTrue(all(c['all_hard_gates_pass'] for c in summary['candidates']))

    def test_model_specific_400_stops_after_h01(self):
        code, seen, rows, summary, metadata = self.run_batch(gate_failure='400')
        self.assertEqual(code, 2)
        self.assertEqual(len(seen), 10)
        self.assertEqual(rows[-1]['safe_error']['gemini_error_status'], 'INVALID_ARGUMENT')
        self.assertEqual(rows[-1]['safe_error']['request_id'], 'offline-id')
        gemini = summary['candidates'][1]
        self.assertEqual(gemini['attempted_calls'], 1)
        self.assertIsNone(gemini['all_hard_gates_pass'])
        self.assertNotIn('h02_large', gemini['per_fixture'])
        self.assertIn('remaining nine calls not made', metadata['stop_reason'])

    def test_http_200_schema_rejection_does_not_open_gate(self):
        code, seen, rows, summary, _ = self.run_batch(gate_failure='schema')
        self.assertEqual(code, 2)
        self.assertEqual(len(seen), 10)
        self.assertEqual(rows[-1]['error_class'], 'SCHEMA_INVALID')
        self.assertFalse(rows[-1]['schema_valid'])
        self.assertEqual(rows[-1]['score']['field_correct'], 0)

    def test_semantic_error_does_not_change_request_or_block_schema_gate(self):
        code, seen, rows, summary, _ = self.run_batch(gate_failure='semantic')
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 19)
        self.assertTrue(rows[10]['schema_valid'])
        self.assertIn('WRONG_CONFIDENT_SKU', rows[10]['error_classes'])
        self.assertFalse(summary['candidates'][1]['all_hard_gates_pass'])

    def test_access_failure_stops_remaining_calls(self):
        code, seen, rows, summary, _ = self.run_batch(auth_failure=True)
        self.assertEqual(code, 2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(rows[-1]['error_class'], 'AUTH_OR_ACCESS')
        self.assertEqual(summary['new_live_attempts'], 1)


if __name__ == '__main__':
    unittest.main()
