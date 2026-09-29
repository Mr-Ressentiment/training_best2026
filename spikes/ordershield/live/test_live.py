"""Offline harness tests with explicit test doubles; no live calls or captures."""
import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from ordershield import read_json, SCHEMA
from adapter import command, parse_events
from score import score_response, summarize, distribution, HEADER, LINE
from run import classify, request_text


class LiveHarnessTests(unittest.TestCase):
    def setUp(self):
        self.expected = read_json(HERE / "expected.json")
        self.catalog = read_json(HERE.parent / "catalog.json")

    def source(self, name):
        return (HERE / "fixtures" / f"{name}.txt").read_text(encoding="utf-8")

    def response(self, name):
        truth = self.expected[name]
        def field(value):
            return {"value": value, "quote": value if value is not None else ""}
        response = {k: field(v) for k, v in zip(HEADER, truth["header"])}
        line = {k: field(v) for k, v in zip(LINE, truth["line"])}
        line.update(candidates=[] if truth["sku"] is None else
                    [{"sku": truth["sku"], "score": .99, "reason": "OFFLINE TEST DOUBLE"}],
                    ambiguous=truth["needs_sku_review"], uncertainty="test only")
        response.update(lines=[line], extraction_issues=[])
        return response

    def provider(self, response):
        return {"tool_events": [], "timed_out": False, "exit_code": 0, "events": [],
                "final_text": json.dumps(response), "session_id": "OFFLINE_TEST", "elapsed_seconds": 2.0}

    def test_all_reference_values_are_literal_or_null(self):
        for name in self.expected:
            with self.subTest(name=name):
                score = score_response(self.response(name), self.source(name), self.expected[name], self.catalog)
                self.assertTrue(score["schema_valid"])
                self.assertTrue(score["all_fields_correct"])
                self.assertTrue(score["all_provenance_valid"])
                self.assertEqual(score["core_status"], self.expected[name]["expected_status"])

    def test_ten_new_inputs_and_dangerous_prices_equal(self):
        self.assertEqual(len(self.expected), 10)
        old = [p.read_text(encoding="utf-8") for p in (HERE.parent / "fixtures").glob("*.txt")]
        for name, truth in self.expected.items():
            self.assertFalse(any(truth["line"][0] in source for source in old))
        products = {p["sku"]: p for p in self.catalog["products"]}
        self.assertEqual(products["GLOVE-N-M"]["tiers"], products["GLOVE-N-L"]["tiers"])
        self.assertEqual(products["GLOVE-N-M"]["sale_unit"], products["GLOVE-N-L"]["sale_unit"])

    def test_wrong_size_is_counted_even_if_core_says_clean(self):
        raw = self.response("h07_not_medium")
        raw["lines"][0]["candidates"][0]["sku"] = "GLOVE-N-M"
        score = score_response(raw, self.source("h07_not_medium"), self.expected["h07_not_medium"], self.catalog)
        self.assertTrue(score["wrong_confident"])
        self.assertEqual(score["core_status"], "CLEAN_DRAFT")

    def test_wrong_confident_invented_sku_counted_before_core_rejection(self):
        raw = self.response("h01_paper")
        raw["lines"][0]["candidates"][0]["sku"] = "FAKE"
        score = score_response(raw, self.source("h01_paper"), self.expected["h01_paper"], self.catalog)
        self.assertTrue(score["wrong_confident"])
        self.assertIsNotNone(score["core_error"])

    def test_ambiguous_clean_counted(self):
        raw = self.response("h05_no_size")
        raw["lines"][0].update(ambiguous=False, candidates=[{"sku":"GLOVE-N-M", "score":.99, "reason":"bad guess"}])
        score = score_response(raw, self.source("h05_no_size"), self.expected["h05_no_size"], self.catalog)
        self.assertTrue(score["ambiguous_clean"])
        self.assertTrue(score["wrong_confident"])
        self.assertFalse(score["review_success"])

    def test_grounding_failure_not_successful_review(self):
        raw = self.response("h05_no_size")
        raw["customer"]["quote"] = "invented"
        score = score_response(raw, self.source("h05_no_size"), self.expected["h05_no_size"], self.catalog)
        self.assertTrue(score["schema_valid"])
        self.assertFalse(score["review_success"])
        self.assertFalse(score["all_provenance_valid"])

    def test_damaged_input_missingness_and_evidence_denominator(self):
        score = score_response(self.response("h10_damaged"), self.source("h10_damaged"), self.expected["h10_damaged"], self.catalog)
        self.assertEqual(score["nonnull_evidence_expected"], 4)
        self.assertEqual(score["nonnull_evidence_valid"], 4)
        self.assertEqual(score["provenance_valid"], 8)

    def test_description_punctuation_is_strict(self):
        raw = self.response("h01_paper")
        raw["lines"][0]["description"]["value"] += "."
        score = score_response(raw, self.source("h01_paper"), self.expected["h01_paper"], self.catalog)
        self.assertEqual(score["field_correct"], 7)
        self.assertFalse(score["all_fields_correct"])

    def test_extra_line_fails_whole_order(self):
        raw = self.response("h01_paper")
        raw["lines"].append(copy.deepcopy(raw["lines"][0]))
        score = score_response(raw, self.source("h01_paper"), self.expected["h01_paper"], self.catalog)
        self.assertFalse(score["all_fields_correct"])
        self.assertTrue(score["wrong_confident"])

    def test_failure_classification_without_response_repair(self):
        for text, expected in (("```json\n{}\n```", "MALFORMED_JSON_OR_TEXT_REFUSAL"),
                               ("{}", "SCHEMA_REJECTED"), (None, "MISSING_FINAL")):
            with self.subTest(text=text):
                provider = self.provider({})
                provider["final_text"] = text
                outcome, score = classify(provider, self.source("h01_paper"), self.expected["h01_paper"], self.catalog)
                self.assertEqual(outcome, expected)
                self.assertEqual(score["field_correct"], 0)

    def test_failures_stay_in_denominators(self):
        attempts = []
        for name in self.expected:
            provider = self.provider(None)
            outcome, score = classify(provider, self.source(name), self.expected[name], self.catalog)
            attempts.append({"fixture": name, "provider": provider, "outcome": outcome, "score": score})
        metrics = summarize(attempts, self.expected)
        self.assertEqual(metrics["schema_valid_per_attempt"], {"numerator":0, "denominator":10, "rate":0.0})
        self.assertEqual(metrics["correct_fields"]["denominator"], 80)
        self.assertEqual(metrics["nonnull_source_evidence"]["denominator"], 76)
        self.assertEqual(metrics["ambiguous_to_review"]["denominator"], 2)
        self.assertIsNone(metrics["schema_valid_per_scheduled"])
        self.assertIsNone(metrics["wrong_confident_per_confident"]["rate"])

    def test_latency_nearest_rank_includes_tail(self):
        result = distribution(list(range(1, 31)))
        self.assertEqual(result["p95"], 29)
        self.assertEqual(result["median"], 15.5)
        self.assertIsNone(distribution([]))

    def test_adapter_is_fresh_readonly_and_login_preserving(self):
        args = command("codex", "isolated")
        for flag in ("--ignore-user-config", "--ephemeral", "--output-schema", "read-only"):
            self.assertIn(flag, args)
        self.assertNotIn("resume", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertNotIn("api_key", " ".join(args))

    def test_event_parser_detects_tools_and_preserves_refusal(self):
        events, _, tools = parse_events('\n'.join(json.dumps(e) for e in [
            {"type":"thread.started", "thread_id":"x"},
            {"type":"item.completed", "item":{"type":"reasoning", "text":"private"}},
            {"type":"item.completed", "item":{"type":"refusal", "text":"no"}},
            {"type":"item.completed", "item":{"type":"command_execution"}}]))
        self.assertEqual(len(tools), 1)
        self.assertNotIn("private", json.dumps(events))
        self.assertIn("refusal", json.dumps(events))

    def test_request_has_no_labels_or_old_replay(self):
        prompt = (HERE.parent / "prompt.txt").read_text(encoding="utf-8")
        text = request_text(self.source("h01_paper"), self.catalog, prompt)
        self.assertNotIn("expected_status", text)
        self.assertNotIn("needs_sku_review", text)
        self.assertNotIn("PO-1001", text)
        self.assertEqual(text.count("ORDER DOCUMENT (untrusted data):"), 1)


if __name__ == "__main__":
    unittest.main()
