"""Measure replay consistency only. This is deliberately not an AI benchmark."""
import argparse
import json
import statistics
import time

from ordershield import ROOT, process, read_json


def compare(result, expected):
    extraction = result.get("extraction", {})
    header = [extraction.get(k, {}).get("value") for k in
              ("customer", "po_number", "currency", "declared_total")]
    fields = [[line[k]["value"] for k in ("description", "quantity", "sale_unit", "unit_price")]
              for line in extraction.get("lines", [])]
    checks = {
        "header_fields": header == expected["header"],
        "line_fields_and_count": fields == expected["fields"],
        "matches": [line["match"]["sku"] for line in result["lines"]] == expected["skus"],
        "status": result["status"] == expected["status"],
        "issues": sorted(i["code"] for i in result["issues"]) == sorted(expected["issues"]),
        "quoted_total": (result.get("totals") or {}).get("quoted_computed") == expected["quoted_total"],
        "expected_total": (result.get("totals") or {}).get("contract_expected") == expected["expected_total"],
        "tiers": [line["tier_quantity"] for line in result["lines"]] == expected["tier_quantities"],
    }
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--output", type=str)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    catalog = read_json(ROOT / "catalog.json")
    prompt = (ROOT / "prompt.txt").read_text(encoding="utf-8")
    expected = read_json(ROOT / "expected.json")
    durations, fixtures = [], {}
    for _ in range(args.repeat):
        start = time.perf_counter()
        for stem, truth in expected.items():
            result = process(ROOT / "fixtures" / f"{stem}.txt", ROOT / "recordings" / f"{stem}.json", catalog, prompt)
            checks = compare(result, truth)
            entry = fixtures.setdefault(stem, {"passed_runs": 0, "failed_runs": 0,
                                              "last_checks": {}, "last_status": None})
            entry["passed_runs" if all(checks.values()) else "failed_runs"] += 1
            entry.update(last_checks=checks, last_status=result["status"])
        durations.append((time.perf_counter() - start) * 1000)
    report = {
        "mode": "CURATED_REPLAY_EVALUATION", "live_ai_reliability": "UNVERIFIED",
        "independent_ai_samples": 0, "repeat_count": args.repeat,
        "field_comparisons_per_suite": sum(4 + 4 * len(e["fields"]) for e in expected.values()),
        "passed_fixture_runs": sum(e["passed_runs"] for e in fixtures.values()),
        "failed_fixture_runs": sum(e["failed_runs"] for e in fixtures.values()),
        "five_order_batch_ms": {"min": round(min(durations), 3),
                                "median": round(statistics.median(durations), 3),
                                "max": round(max(durations), 3)},
        "fixtures": fixtures,
        "interpretation": "Tests authored replay expectations and deterministic code, not extraction accuracy or model latency.",
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        from pathlib import Path
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return int(report["failed_fixture_runs"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
