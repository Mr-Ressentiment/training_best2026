#!/usr/bin/env python3
"""CLI script to run legacy vs symbolic reconciliation parity checks and generate report.

Usage:
    .venv/bin/python scripts/check_reconciliation_parity.py --seed 20261002 --random-orders 200 --report reports/reconciliation_parity.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.parity.harness import (
    CATEGORIES,
    compute_parity_summary,
    create_isolated_session,
    generate_markdown_report,
    generate_random_cases,
    run_fixture_cases,
)


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify parity between legacy reconciliation engine and symbolic rules."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20261002,
        help="Random seed for pseudo-random order generation (default: 20261002)",
    )
    parser.add_argument(
        "--random-orders",
        type=int,
        default=200,
        help="Number of pseudo-random orders to generate and evaluate (default: 200)",
    )
    parser.add_argument(
        "--report",
        type=str,
        default="reports/reconciliation_parity.md",
        help="Output path for the Markdown parity report (default: reports/reconciliation_parity.md)",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    parsed_args = parse_args(args)
    report_path = (REPO_ROOT / parsed_args.report).resolve()

    print(f"=== Reconciliation Engine vs Symbolic Rules Parity Check ===")
    print(f"Random Seed:    {parsed_args.seed}")
    print(f"Random Orders:  {parsed_args.random_orders}")
    print(f"Report Target:  {report_path}")
    print()

    db = create_isolated_session()
    try:
        print("1. Running fixture test cases...")
        fixture_results = run_fixture_cases(db, repo_root=REPO_ROOT)
        print(f"   Evaluated {len(fixture_results)} suitable fixtures.")

        print(f"2. Generating and evaluating {parsed_args.random_orders} stratified random orders...")
        random_results = generate_random_cases(
            db=db,
            count=parsed_args.random_orders,
            seed=parsed_args.seed,
        )
        print(f"   Evaluated {len(random_results)} random order cases.")

        all_results = fixture_results + random_results
        summary = compute_parity_summary(all_results, seed=parsed_args.seed)

        print()
        print("=== Parity Results by Category ===")
        print(f"{'Category':<30} {'Total':<8} {'Matches':<10} {'Mismatches':<12} {'Agreement'}")
        print("-" * 72)
        for cat in CATEGORIES:
            cm = summary.category_metrics[cat]
            print(
                f"{cat:<30} {cm.total:<8} {cm.matches:<10} {cm.mismatches:<12} {cm.agreement_pct:.2f}%"
            )
        print("-" * 72)
        print(
            f"Overall: {summary.matched_cases}/{summary.total_cases} cases match "
            f"({summary.overall_agreement_pct:.2f}%)"
        )
        print()

        generate_markdown_report(
            summary=summary,
            results=all_results,
            seed=parsed_args.seed,
            report_path=report_path,
        )
        print(f"Wrote parity report to: {report_path}")

        if summary.mismatched_cases > 0:
            print(f"FAILURE: Detected {summary.mismatched_cases} mismatches.")
            return 1

        print("SUCCESS: 100% parity achieved across all categories and cases.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
