"""Adversarial offline tests; live transport is always replaced by test doubles."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import phase1 as p


class Phase1Tests(unittest.TestCase):
    def setUp(self):
        self.truth = p.read(p.CORPUS / 'live/expected.json')
        self.catalog = p.read(p.CORPUS / 'catalog.json')

    def source(self, fixture):
        return (p.CORPUS / 'live/fixtures' / (fixture + '.txt')).read_text(encoding='utf-8')

    def response(self, fixture):
        truth = self.truth[fixture]
        def field(v):
            return {'value': v, 'quote': v if v is not None else ''}
        result = {k: field(v) for k, v in zip(p.scorer.HEADER, truth['header'])}
        line = {k: field(v) for k, v in zip(p.scorer.LINE, truth['line'])}
        line.update(candidates=[] if truth['sku'] is None else [{'sku': truth['sku'], 'score': .99, 'reason': 'OFFLINE TEST DOUBLE'}],
                    ambiguous=truth['needs_sku_review'], uncertainty='OFFLINE TEST DOUBLE')
        result.update(lines=[line], extraction_issues=[])
        return result

    def evaluate(self, fixture, response):
        return p.evaluate(json.dumps(response), self.source(fixture), self.truth[fixture], self.catalog)

    def test_frozen_integrity_and_schema(self):
        verified = p.integrity()
        self.assertEqual(verified['schema_sha256'], p.read(p.CORPUS / 'live/freeze.json')['schema_sha256'])
        self.assertEqual(len(verified['frozen_files']), 19)

    def test_all_ten_reference_test_doubles_pass(self):
        for fixture in self.truth:
            with self.subTest(fixture=fixture):
                score, evidence, errors = self.evaluate(fixture, self.response(fixture))
                self.assertEqual(errors, [])
                self.assertTrue(score['all_fields_correct'])
                self.assertTrue(evidence['all_returned_fields_grounded'])

    def test_terminal_punctuation_does_not_break_grounding(self):
        for value, quote in [('5.00', 'Price: 5.00.'), ('7', 'Quantity: 7.'), ('EUR', 'Currency: EUR.'), ('box', 'Unit: box.'), ('HX-201', 'PO HX-201.')]:
            self.assertEqual(p.grounded({'value': value, 'quote': quote}, quote, 'test'), value)

    def test_numeric_and_word_fragments_are_rejected(self):
        for value, quote in [('5', '5.00.'), ('00', '5.00.'), ('7', '17.'), ('box', 'boxes.'), ('EUR', 'NEUR.')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.grounded({'value': value, 'quote': quote}, quote, 'test')

    def test_nonliteral_quote_and_invalid_null_are_rejected(self):
        for field, source in [({'value':'EUR', 'quote':'EUR.'}, 'Currency EUR'),
                              ({'value':None, 'quote':'unknown'}, 'unknown'),
                              ({'value':' EUR', 'quote':' EUR'}, ' EUR')]:
            with self.assertRaises(ValueError):
                p.grounded(field, source, 'test')

    def test_description_comparison_remains_punctuation_strict(self):
        response = self.response('h01_paper')
        response['lines'][0]['description']['value'] += '.'
        score, _, errors = self.evaluate('h01_paper', response)
        self.assertFalse(score['field_checks']['line.description'])
        self.assertIn('SEMANTIC_WRONG', errors)

    def test_wrong_confident_flag_precedes_core_rejection(self):
        response = self.response('h07_not_medium')
        response['lines'][0]['candidates'][0]['sku'] = 'GLOVE-N-M'
        score, _, errors = self.evaluate('h07_not_medium', response)
        self.assertEqual(score['core_status'], 'CLEAN_DRAFT')
        self.assertIn('WRONG_CONFIDENT_SKU', errors)
        response['customer']['quote'] = 'invented source'
        score, _, errors = self.evaluate('h07_not_medium', response)
        self.assertTrue(score['wrong_confident'])
        self.assertIn('LOCAL_VALIDATION_FAILURE', errors)

    def test_invented_confident_sku_is_counted(self):
        response = self.response('h01_paper')
        response['lines'][0]['candidates'][0]['sku'] = 'INVENTED'
        score, _, errors = self.evaluate('h01_paper', response)
        self.assertTrue(score['wrong_confident'])
        self.assertIn('LOCAL_VALIDATION_FAILURE', errors)

    def test_review_cases_cannot_be_promoted_to_clean(self):
        for fixture in ('h05_no_size','h06_no_pack','h09_latex'):
            response = self.response(fixture)
            response['lines'][0].update(ambiguous=False, candidates=[{'sku':'GLOVE-N-M','score':.99,'reason':'unsafe guess'}])
            score, _, errors = self.evaluate(fixture, response)
            self.assertTrue(score['wrong_confident'])
            self.assertTrue(score['ambiguous_clean'])
            self.assertFalse(score['review_success'])
            self.assertIn('WRONG_CONFIDENT_SKU', errors)

    def test_schema_is_not_relaxed_for_provider(self):
        for mutate in (lambda r: r.update(extra=True),
                       lambda r: r['lines'][0]['candidates'][0].update(score=True),
                       lambda r: r['lines'][0]['candidates'][0].update(score=1.1),
                       lambda r: r.pop('currency')):
            response = self.response('h01_paper')
            mutate(response)
            score, evidence, errors = self.evaluate('h01_paper', response)
            self.assertEqual(errors, ['SCHEMA_INVALID'])
            self.assertIsNone(evidence)
            self.assertFalse(score['schema_valid'])

    def test_malformed_output_is_not_repaired(self):
        for text in ('```json\n{}\n```', 'not JSON', '{"score":NaN}'):
            score, _, errors = p.evaluate(text, self.source('h01_paper'), self.truth['h01_paper'], self.catalog)
            self.assertEqual(errors, ['MALFORMED_JSON'])
            self.assertEqual(score['field_correct'], 0)

    def test_raw_quote_audit_does_not_award_invalid_schema_credit(self):
        response=self.response('h01_paper')
        response['lines'][0]['candidates'][0]['scores']=response['lines'][0]['candidates'][0].pop('score')
        score,_,errors=self.evaluate('h01_paper',response)
        audit=p.raw_quote_audit(json.dumps(response),self.source('h01_paper'))
        self.assertEqual(errors,['SCHEMA_INVALID'])
        self.assertEqual(score['field_correct'],0)
        self.assertEqual(audit['verbatim_snippets'],8)
        response['customer']['quote']='ABSENT FROM SOURCE 123'
        self.assertFalse(p.raw_quote_audit(json.dumps(response),self.source('h01_paper'))['all_returned_snippets_verbatim'])

    def test_all_lines_and_evidence_are_checked(self):
        response = self.response('h01_paper')
        response['lines'].append(copy.deepcopy(response['lines'][0]))
        response['lines'][1]['description']['quote'] = 'NOT IN CANONICAL SOURCE 123'
        score, evidence, errors = self.evaluate('h01_paper', response)
        self.assertEqual(len(evidence['fields']), 12)
        self.assertFalse(evidence['all_returned_snippets_verbatim'])
        self.assertFalse(score['all_fields_correct'])
        self.assertIn('WRONG_CONFIDENT_SKU', errors)

    def test_exact_candidates_modes_and_input_isolation(self):
        env = {'GEMINI_API_KEY':'synthetic-google', 'QWEN_API_KEY':'synthetic-qwen',
               'QWEN_BASE_URL':'https://test.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'}
        text = p.request_text(self.source('h05_no_size'), self.catalog, (p.CORPUS/'prompt.txt').read_text())
        for candidate in p.CANDIDATES:
            req = p.build_request(candidate, text, env)
            body = json.loads(req.data)
            self.assertNotIn('tools', body)
            self.assertNotIn('expected_status', req.data.decode())
            self.assertNotIn('needs_sku_review', req.data.decode())
            if candidate['model'].startswith('gemini'):
                self.assertEqual(body['generationConfig']['responseFormat']['text']['schema'], p.SCHEMA)
                self.assertEqual(body['generationConfig']['responseFormat']['text']['mimeType'], 'APPLICATION_JSON')
                self.assertEqual(body['generationConfig']['thinkingConfig']['thinkingLevel'], candidate['thinking_level'])
            else:
                self.assertEqual(body['response_format'], {'type':'json_object'})
                self.assertIs(body['enable_thinking'], False)
                self.assertIn(json.dumps(p.SCHEMA), body['messages'][0]['content'])
        with self.assertRaises(ValueError):
            p.build_request({'model':'gemini-3.7-flash'}, text, env)

    def test_regions_redirects_and_failure_classes(self):
        with self.assertRaises(ValueError):
            p.qwen_endpoint('https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
        self.assertIsNone(p.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com'))
        for status, raw, expected in ((401,b'', 'AUTH_OR_ACCESS'),(403,b'', 'AUTH_OR_ACCESS'),(429,b'', 'QUOTA'),
                (400,b'AllocationQuota.FreeTierOnly','QUOTA'),(400,b'', 'PROVIDER_4XX'),(503,b'', 'PROVIDER_5XX')):
            self.assertEqual(p.provider_failure(status,raw),expected)

    def test_latency_small_sample_qualification(self):
        d = p.latency(list(range(1,11)))
        self.assertEqual(d['p50_ms'], 5.5)
        self.assertEqual(d['p95_ms'], 10)
        self.assertIn('not a reliable', d['p95_limitation'])
        self.assertIsNone(p.latency([]))

    def test_local_file_lock_retry_cannot_send_requests(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT, prefix='offline-write-') as name:
            target=Path(name)/'artifact.json'
            original=Path.replace
            attempts=[]
            def replace(path, destination):
                attempts.append(path)
                if len(attempts)==1:
                    raise PermissionError('OFFLINE simulated transient file lock')
                return original(path,destination)
            with patch.object(Path,'replace',replace),patch.object(p,'transport',side_effect=AssertionError('network forbidden')):
                p.write(target,{'safe':'ok'})
            self.assertEqual(len(attempts),2)
            self.assertEqual(p.read(target),{'safe':'ok'})

    def fake_transport(self, request):
        body=json.loads(request.data)
        google='googleapis.com' in request.full_url
        text=body['contents'][0]['parts'][0]['text'] if google else body['messages'][1]['content']
        fixture=next(name for name,truth in self.truth.items() if truth['header'][1] in text)
        final=json.dumps(self.response(fixture))
        if google:
            data={'modelVersion':'synthetic-model','candidates':[{'content':{'parts':[{'text':final}]},'finishReason':'STOP'}],
                  'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':200,'totalTokenCount':300}}
        else:
            data={'model':'qwen3.8-flash','choices':[{'message':{'content':final},'finish_reason':'stop'}],
                  'usage':{'prompt_tokens':100,'completion_tokens':200,'total_tokens':300}}
        return 200,json.dumps(data).encode()

    def test_full_offline_batch_one_attempt_per_fixture_and_rerun_guard(self):
        self.mock_batch(False)

    def test_auth_failure_stops_without_retries(self):
        self.mock_batch(True)

    def mock_batch(self, auth_failure):
        with tempfile.TemporaryDirectory(dir=p.ROOT, prefix='offline-test-') as name:
            root=Path(name)
            paths={k:root/(k+'.json' if k!='REPORT' else k+'.md') for k in ('METADATA','CALLS','SUMMARY','REPORT')}
            env={'GEMINI_API_KEY':'synthetic-google','QWEN_API_KEY':'synthetic-qwen',
                 'QWEN_BASE_URL':'https://test.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'}
            sent=[]
            def send(req):
                self.assertEqual(len(p.read(paths['CALLS'])['calls']),len(sent)+1)
                sent.append(req)
                return (403,b'') if auth_failure else self.fake_transport(req)
            with patch.multiple(p,**paths),patch.object(p,'environment',return_value=(env,'OFFLINE_TEST')),patch('builtins.print'):
                result=p.execute(send)
                self.assertEqual(result,2 if auth_failure else 0)
                self.assertEqual(len(sent),1 if auth_failure else 30)
                summary=p.read(paths['SUMMARY'])
                if auth_failure:
                    self.assertEqual(summary['completed_calls'],0)
                    self.assertEqual(summary['finalized_attempts'],1)
                    self.assertEqual(summary['candidates'][0]['schema_valid_rate'],{'numerator':0,'denominator':1,'rate':0.0})
                if not auth_failure:
                    self.assertTrue(all(c['all_hard_gates_pass'] for c in summary['candidates']))
                    self.assertTrue(all(c['attempted_calls']==10 for c in summary['candidates']))
                    self.assertEqual(len({(r['requested_model_id'],r['fixture_id'],r['run_index']) for r in p.read(paths['CALLS'])['calls']}),30)
                    self.assertEqual(summary['candidates'][0]['nonnull_evidence_coverage']['denominator'],76)
                with self.assertRaises(ValueError):
                    p.execute(lambda req:self.fail('must not retry'))
                output=paths['CALLS'].read_text()
                self.assertNotIn(env['GEMINI_API_KEY'],output)
                self.assertNotIn(env['QWEN_BASE_URL'],output)


if __name__ == '__main__':
    unittest.main()
