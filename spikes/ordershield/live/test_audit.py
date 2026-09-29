"""Test the disclosed scoring erratum without invoking a model."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import audited_provider
from ordershield import grounded


class AuditTests(unittest.TestCase):
    def test_only_non_tool_warning_items_are_removed(self):
        original = {"tool_events": [
            {"event": "item.completed", "item_type": "error"},
            {"event": "item.completed", "item_type": "warning"},
            {"event": "item.completed", "item_type": "command_execution"},
            {"event": "item.completed", "item_type": "mcp_tool_call"},
            {"event": "item.completed", "item_type": "unknown_future_tool"}],
            "final_text": "exact output", "exit_code": 1}
        result = audited_provider(original)
        self.assertEqual(len(result["tool_events"]), 3)
        self.assertEqual(len(original["tool_events"]), 5)
        self.assertEqual(result["final_text"], original["final_text"])
        self.assertEqual(result["exit_code"], 1)

    def test_no_tool_events_stays_empty(self):
        self.assertEqual(audited_provider({"tool_events": []}), {"tool_events": []})

    def test_observed_core_word_period_boundary_limitation(self):
        source = "Selling unit: box."
        with self.assertRaisesRegex(ValueError, "not supported by quote"):
            grounded({"value": "box", "quote": source}, source, "sale_unit")
        # Literal span choice changes core acceptance despite identical meaning.
        self.assertEqual(grounded({"value": "box", "quote": "box"}, source, "sale_unit"), "box")


if __name__ == "__main__":
    unittest.main()
