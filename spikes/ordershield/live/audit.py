"""Explicit post-freeze scoring erratum; never changes inputs or raw evidence.

The frozen adapter mistook CLI startup error/warning items for tools. Exclude
only those two non-tool item types, retaining any other suspected tool events.
Re-run the otherwise unchanged frozen classifier/scorer against original text.
"""
import copy
import json
from pathlib import Path

from run import ROOT, check_freeze, classify, read_json, save, sha
from score import summarize, distribution, HEADER, LINE
from ordershield import grounded


def audited_provider(provider):
    corrected = copy.deepcopy(provider)
    corrected["tool_events"] = [e for e in provider["tool_events"]
                                if e["item_type"] not in {"error", "warning"}]
    return corrected


def main():
    check_freeze()
    expected = read_json(ROOT / "expected.json")
    catalog = read_json(ROOT.parent / "catalog.json")
    corrected, records, hashes = [], [], {}
    for path in sorted((ROOT / "results/calls").glob("*.json")):
        original = read_json(path)
        hashes[path.name] = sha(path)
        source = (ROOT / "fixtures" / f"{original['fixture']}.txt").read_text(encoding="utf-8")
        provider = audited_provider(original["provider"])
        outcome, score = classify(provider, source, expected[original["fixture"]], catalog)
        updated = {**original, "provider": provider, "outcome": outcome, "score": score}
        corrected.append(updated)
        records.append({"fixture": original["fixture"], "repeat": original["repeat"],
                        "raw_evidence": "calls/" + path.name, "raw_sha256": hashes[path.name],
                        "original_outcome": original["outcome"], "audited_outcome": outcome,
                        "excluded_nontool_events": [e for e in original["provider"]["tool_events"]
                                                    if e["item_type"] in {"error", "warning"}],
                        "score": score})
    summary = summarize(corrected, expected)
    generated = [a for a in corrected if a["provider"]["exit_code"] == 0 and a["provider"]["final_text"]]
    grounding_failures = []
    literal_quotes = 0
    returned_nonnull = 0
    for a in generated:
        if not a["score"]["schema_valid"]:
            continue
        source = (ROOT / "fixtures" / f"{a['fixture']}.txt").read_text(encoding="utf-8")
        raw = json.loads(a["provider"]["final_text"])
        fields = [(k, raw[k]) for k in HEADER]
        fields += [(f"line.{i + 1}.{k}", line[k]) for i, line in enumerate(raw["lines"]) for k in LINE]
        for name, field in fields:
            if field["value"] is not None:
                returned_nonnull += 1
                literal_quotes += int(bool(field["quote"]) and field["quote"] in source)
            try:
                grounded(field, source, name)
            except ValueError as exc:
                grounding_failures.append({"fixture": a["fixture"], "repeat": a["repeat"],
                                           "field": name, **field, "error": str(exc)})
    summary.update(audit="Non-tool CLI error/warning items removed from tool-event classification only",
                   original_summary="summary.json (frozen harness bug; not the valid model-quality estimate)",
                   audit_script_sha256=sha(Path(__file__)), raw_call_sha256=hashes,
                   unique_session_ids=len({a["provider"]["session_id"] for a in corrected}),
                   remaining_tool_event_count=sum(len(a["provider"]["tool_events"]) for a in corrected),
                   total_cli_wall_seconds=sum(a["provider"]["elapsed_seconds"] for a in corrected),
                   freeze_still_valid=bool(check_freeze()))
    summary["supplementary_observations_not_new_acceptance_criteria"] = {
        "completed_model_outputs": len(generated),
        "quota_rejected_calls": sum("hit your usage limit" in json.dumps(a["provider"]).lower() for a in corrected),
        "returned_nonnull_fields": returned_nonnull,
        "returned_nonnull_literal_quotes": literal_quotes,
        "all_grounding_failures": grounding_failures,
        "latency_seconds_completed_model_outputs": distribution([a["provider"]["elapsed_seconds"] for a in generated]),
        "latency_seconds_provider_errors": distribution([a["provider"]["elapsed_seconds"] for a in corrected if a["outcome"] == "PROVIDER_ERROR"]),
        "generations_completed_for_every_fixture_three_times": all(sum(a["fixture"] == name for a in generated) == 3 for name in expected),
        "note": "Frozen summary.complete means 30 attempts recorded, not 30 completed generations.",
    }
    save(ROOT / "results/audited_scores.json", records)
    save(ROOT / "results/audited_summary.json", summary)
    print(f"Audited {len(records)} original calls; schema valid {summary['schema_valid_per_attempt']}; "
          f"wrong confident {summary['wrong_confident_per_attempt']}")


if __name__ == "__main__":
    main()
