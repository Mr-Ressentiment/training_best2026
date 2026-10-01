"""Offline stability-run safety tests; network is replaced by explicit doubles."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import phase1 as p
import phase2 as m


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.baseline, _ = m.baselines()
        self.truth = p.read(p.CORPUS / 'live/expected.json')

    def response(self, fixture):
        truth = self.truth[fixture]
        def field(v):
            return {'value': v, 'quote': v if v is not None else ''}
        obj = {k: field(v) for k, v in zip(p.scorer.HEADER, truth['header'])}
        line = {k: field(v) for k, v in zip(p.scorer.LINE, truth['line'])}
        line.update(candidates=[] if truth['sku'] is None else [{'sku': truth['sku'], 'score': .99, 'reason': 'OFFLINE TEST DOUBLE'}],
                    ambiguous=truth['needs_sku_review'], uncertainty='OFFLINE TEST DOUBLE')
        obj.update(lines=[line], extraction_issues=[])
        return obj

    def row(self, model, fixture, response, run=2):
        template = copy.deepcopy(next(r for r in self.baseline if r['requested_model_id'] == model and r['fixture_id'] == fixture))
        _, source, catalog, truth, _ = m.inputs(fixture)
        final = json.dumps(response)
        score, evidence, errors = p.evaluate(final, source, truth, catalog)
        template.update(phase='PHASE_2', run_index=run, raw_provider_output=final,
                        score=score, evidence_grounding=evidence, error_classes=errors,
                        schema_valid=score['schema_valid'], json_parsed='MALFORMED_JSON' not in errors,
                        raw_quote_audit=p.raw_quote_audit(final, source))
        m.finish_fields(template, source, truth)
        return template

    def test_exact_baselines_and_36_unique_authorized_attempts(self):
        self.assertEqual(len(self.baseline), 12)
        planned = m.schedule()
        self.assertEqual(len(planned), 36)
        self.assertEqual(len({(c['model'], f, r) for c, f, r in planned}), 36)
        self.assertEqual({r for _, _, r in planned}, {2, 3, 4})
        self.assertEqual({f for _, f, _ in planned}, set(m.FIXTURES))
        self.assertEqual({c['model'] for c, _, _ in planned}, {'gemini-3.5-flash-lite', 'qwen3.8-flash'})
        with patch.object(p, 'transport') as send:
            with self.assertRaises(ValueError):
                m.call(p.CANDIDATES[2], 'h05_no_size', 2, send, lambda r: None, {}, (), {})
            with self.assertRaises(ValueError):
                m.inputs('h01_paper')
            send.assert_not_called()

    def test_h08_additional_source_text_is_separate_and_not_normalized(self):
        response = self.response('h08_not_large')
        _, source, _, _, _ = m.inputs('h08_not_large')
        value = self.truth['h08_not_large']['line'][0] + '\nLarge gloves were ordered last month; that size must not be repeated here.'
        self.assertIn(value, source)
        response['lines'][0]['description'] = {'value': value, 'quote': value}
        row = self.row('gemini-3.5-flash-lite', 'h08_not_large', response)
        detail = row['special_tracking']['h08_customer_description']
        self.assertFalse(detail['literal_exact_match'])
        self.assertFalse(detail['frozen_field_correct'])
        self.assertTrue(detail['includes_additional_source_text'])
        self.assertEqual(row['score']['field_correct'], 7)
        self.assertTrue(row['score']['sku_correct'])

    def test_h10_null_values_separate_from_nonempty_null_quote(self):
        response = self.response('h10_damaged')
        response['lines'][0]['quantity']['quote'] = '[unreadable]'
        row = self.row('gemini-3.5-flash-lite', 'h10_damaged', response)
        detail = row['special_tracking']['h10_null_fields']
        self.assertTrue(detail['all_null_values_correct'])
        self.assertEqual(detail['correct_null_values'], 4)
        self.assertEqual(detail['valid_null_provenance'], 3)
        self.assertFalse(detail['all_null_provenance_valid'])
        self.assertEqual(row['score']['field_correct'], 8)
        self.assertIn('LOCAL_VALIDATION_FAILURE', row['error_classes'])
        response.pop('customer')
        invalid = self.row('gemini-3.5-flash-lite', 'h10_damaged', response)
        self.assertEqual(invalid['special_tracking']['h10_null_fields']['correct_null_values'], 3)
        self.assertFalse(invalid['schema_valid'])
        self.assertEqual(invalid['score']['field_correct'], 0)

    def test_review_confident_promotion_and_tags_are_recorded(self):
        response = self.response('h05_no_size')
        row = self.row('qwen3.8-flash', 'h05_no_size', response)
        self.assertEqual(row['special_tracking']['review_trap']['decision_tags'], ['AMBIGUOUS', 'UNRECOGNIZED'])
        response['lines'][0].update(ambiguous=False, candidates=[{'sku': 'GLOVE-N-M', 'score': .99, 'reason': 'unsafe'}])
        unsafe = self.row('qwen3.8-flash', 'h05_no_size', response)
        self.assertTrue(unsafe['score']['wrong_confident'])
        self.assertTrue(unsafe['special_tracking']['review_trap']['clean_or_confident'])
        report = m.metrics([unsafe], 1)
        self.assertEqual(report['wrong_confident_sku_count'], 1)
        self.assertEqual(report['review_clean_or_confident_count'], 1)
        self.assertEqual(report['ambiguous_to_clean_count'], 1)

    def test_aggregate_retains_all_repeated_fixture_rows_and_phase_views(self):
        new = [self.row(c['model'], f, self.response(f), run) for c, f, run in m.schedule()]
        report = m.summarize(self.baseline, new, {'stop_reason': 'OFFLINE'})
        for candidate in report['candidates']:
            self.assertEqual(candidate['phase1_selected']['attempted_calls'], 6)
            self.assertEqual(candidate['phase2']['attempted_calls'], 18)
            self.assertEqual(candidate['combined']['attempted_calls'], 24)
            self.assertEqual(candidate['phase2']['exact_mandatory_field_rate']['denominator'], 144)
            self.assertEqual(candidate['combined']['exact_mandatory_field_rate']['denominator'], 192)
            self.assertEqual(candidate['phase2']['determinate_sku_rate']['denominator'], 9)
            self.assertEqual(candidate['phase2']['review_trap_rate']['denominator'], 9)
            self.assertTrue(all(x['attempted_calls'] == 4 for x in candidate['combined']['per_fixture'].values()))
        self.assertFalse(report['weighted_score_computed'])
        self.assertFalse(report['winner_declared'])

    def run_batch(self, auth_failure=False):
        seen = []
        def fake(request, response_metadata=None):
            body = json.loads(request.data)
            google = '/models/' in request.full_url
            model = request.full_url.split('/models/')[1].split(':')[0] if google else body['model']
            text = body['contents'][0]['parts'][0]['text'] if google else body['messages'][1]['content']
            fixture = next(f for f in m.FIXTURES if self.truth[f]['header'][1] in text)
            seen.append((model, fixture))
            self.assertLessEqual(len(seen), 36)
            self.assertIn(model, {c['model'] for c in m.CANDIDATES})
            if not google:
                self.assertIs(body['enable_thinking'], False)
                self.assertEqual(body['response_format'], {'type': 'json_object'})
            if auth_failure:
                return 403, b'{"error":{"status":"PERMISSION_DENIED","message":"OFFLINE"}}'
            final = json.dumps(self.response(fixture))
            envelope = {'modelVersion': model, 'candidates': [{'content': {'parts': [{'text': final}]}}],
                        'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1, 'totalTokenCount': 2}} if google else {
                        'model': model, 'choices': [{'message': {'content': final}}],
                        'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            return 200, json.dumps(envelope).encode()
        with tempfile.TemporaryDirectory(dir=p.ROOT, prefix='offline-phase2-') as folder:
            root = Path(folder)
            targets = {'CALLS': root/'calls.json', 'BASELINE': root/'baseline.json',
                       'SUMMARY': root/'summary.json', 'METADATA': root/'metadata.json', 'REPORT': root/'report.md'}
            env = {'GEMINI_API_KEY': 'OFFLINE_SENTINEL', 'QWEN_API_KEY': 'OFFLINE_SENTINEL',
                   'QWEN_BASE_URL': 'https://offline.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'}
            with patch.multiple(m, **targets), patch.object(p, 'environment', return_value=(env, 'OFFLINE')), patch.object(p, 'transport') as live, redirect_stdout(io.StringIO()):
                code = m.execute(fake)
                live.assert_not_called()
                rows = p.read(m.CALLS)['calls']
                metadata = p.read(m.METADATA)
                summary = p.read(m.SUMMARY)
                self.assertEqual(summary, m.summarize(p.read(m.BASELINE)['calls'], rows, metadata))
                with self.assertRaises(ValueError):
                    m.execute(fake)
                live.assert_not_called()
        return code, seen, rows, summary

    def test_full_36_call_plan_no_retries_no_other_fixtures(self):
        code, seen, rows, summary = self.run_batch()
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 36)
        self.assertEqual(len({(r['requested_model_id'], r['fixture_id'], r['run_index']) for r in rows}), 36)
        for cfg in m.CANDIDATES:
            for fixture in m.FIXTURES:
                self.assertEqual(seen.count((cfg['model'], fixture)), 3)
        self.assertEqual(summary['new_live_attempts'], 36)

    def test_access_error_stops_without_retry(self):
        code, seen, rows, summary = self.run_batch(auth_failure=True)
        self.assertEqual(code, 2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(rows[0]['error_class'], 'AUTH_OR_ACCESS')
        self.assertEqual(summary['candidates'][0]['phase2']['schema_valid_rate']['rate'], 0)
        self.assertIsNone(summary['candidates'][1]['phase2']['schema_valid_rate']['rate'])

    def test_qwen_errors_redacted_and_capped(self):
        raw = json.dumps({'error': {'code': 'Forbidden', 'message': 'OFFLINE_SECRET ' + 'x' * 3000},
                          'headers': {'Authorization': 'OFFLINE_SECRET'}}).encode()
        clean = m.safe_error(m.CANDIDATES[1], 403, raw, ('OFFLINE_SECRET',), 'offline-id')
        self.assertEqual(clean['provider_error_code'], 'Forbidden')
        self.assertEqual(len(clean['provider_error_message']), 2048)
        self.assertNotIn('OFFLINE_SECRET', json.dumps(clean))
        self.assertNotIn('Authorization', json.dumps(clean))


if __name__ == '__main__':
    unittest.main()
