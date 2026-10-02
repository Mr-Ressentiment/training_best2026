"""Pytest test suite verifying legacy reconciliation engine vs symbolic rules parity.

Tests:
- 100% parity across all suitable repository fixtures (clean_acme, discrepancy_apex, ambiguous_apex).
- 100% parity across exactly 200 stratified pseudo-random order drafts with fixed seed 20261002.
- Determinism of the pseudo-random generator with identical seeds.
- Verification that excluded fixtures (po_unextractable.pdf) fail document extraction as documented.
- Validation that Markdown report generation produces complete and accurate summary artifacts.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.services.document_parser import UnextractableTextError, parse_document
from tests.parity.harness import (
    CATEGORIES,
    compute_parity_summary,
    generate_markdown_report,
    generate_random_cases,
    run_fixture_cases,
)


@pytest.fixture(autouse=True)
def _seed_db(db_session: Session) -> None:
    """Ensure baseline products, contracts, and tiers are present in the test session."""
    seed_baseline(db_session)
    db_session.commit()


def test_reconciliation_parity_on_fixtures(db_session: Session) -> None:
    """Validate 100% parity between legacy engine and symbolic rules on existing fixtures."""
    results = run_fixture_cases(db_session)
    assert len(results) == 3, f"Expected 3 suitable fixtures, got {len(results)}"

    for case in results:
        assert case.all_matched is True, (
            f"Fixture '{case.case_id}' has mismatch:\n"
            f"Oracle:   {case.oracle_flags}\n"
            f"Symbolic: {case.symbolic_flags}\n"
            f"Matches:  {case.matches}"
        )

    # Detailed expectations for known fixtures
    clean_acme = next(r for r in results if r.case_id == "fixture-clean-acme")
    assert not any(clean_acme.oracle_flags.values())
    assert not any(clean_acme.symbolic_flags.values())

    discrepancy_apex = next(r for r in results if r.case_id == "fixture-discrepancy-apex")
    assert discrepancy_apex.oracle_flags["PriceMismatch"] is True
    assert discrepancy_apex.symbolic_flags["PriceMismatch"] is True
    assert discrepancy_apex.oracle_flags["QuantityOrPackagingBreach"] is False
    assert discrepancy_apex.symbolic_flags["QuantityOrPackagingBreach"] is False
    assert "CatalogMatchingMismatch" in discrepancy_apex.unmodeled_oracle_flags

    ambiguous_apex = next(r for r in results if r.case_id == "fixture-ambiguous-apex")
    assert not any(ambiguous_apex.oracle_flags.values())
    assert not any(ambiguous_apex.symbolic_flags.values())
    assert "CatalogMatchingMismatch" in ambiguous_apex.unmodeled_oracle_flags


def test_reconciliation_parity_on_200_random_orders(db_session: Session) -> None:
    """Validate 100% parity on exactly 200 stratified pseudo-random order drafts."""
    results = generate_random_cases(db=db_session, count=200, seed=20261002)
    assert len(results) == 200, f"Expected exactly 200 random cases, got {len(results)}"

    summary = compute_parity_summary(results, seed=20261002)
    assert summary.total_cases == 200
    assert summary.mismatched_cases == 0, f"Detected {summary.mismatched_cases} mismatches"
    assert summary.matched_cases == 200
    assert summary.overall_agreement_pct == 100.0

    # Verify each category achieved 100% agreement
    for cat in CATEGORIES:
        cm = summary.category_metrics[cat]
        assert cm.mismatches == 0, f"Category '{cat}' had {cm.mismatches} mismatches"
        assert cm.matches == 200
        assert cm.agreement_pct == 100.0
        # Ensure positive examples were generated and detected for each category
        assert cm.true_positives > 0, f"Category '{cat}' had 0 true positives generated"

    # Verify expected stratification counts
    assert summary.strata_counts["clean"] == 25
    assert summary.strata_counts["price_mismatch"] == 25
    assert summary.strata_counts["moq_shortfall"] == 25
    assert summary.strata_counts["pkg_breach"] == 25
    assert summary.strata_counts["arith_line"] == 25
    assert summary.strata_counts["arith_order"] == 25
    assert summary.strata_counts["mixed"] == 25
    assert summary.strata_counts["unresolved_ambiguous"] == 15
    assert summary.strata_counts["removed_lines"] == 10


def test_reconciliation_parity_generator_determinism(db_session: Session) -> None:
    """Verify that identical random seeds generate bit-for-bit identical case evaluations."""
    run1 = generate_random_cases(db=db_session, count=20, seed=20261002)
    run2 = generate_random_cases(db=db_session, count=20, seed=20261002)

    assert len(run1) == len(run2) == 20
    for r1, r2 in zip(run1, run2):
        assert r1.case_id == r2.case_id
        assert r1.strata == r2.strata
        assert r1.oracle_flags == r2.oracle_flags
        assert r1.symbolic_flags == r2.symbolic_flags
        assert r1.matches == r2.matches


def test_unextractable_fixture_is_excluded(fixtures_dir: Path) -> None:
    """Verify that po_unextractable.pdf fails text extraction as documented in manifest."""
    pdf_path = fixtures_dir / "po_unextractable.pdf"
    assert pdf_path.exists(), f"Fixture file not found: {pdf_path}"

    with pytest.raises(UnextractableTextError) as exc_info:
        parse_document(
            pdf_path.read_bytes(),
            filename=pdf_path.name,
            content_type="application/pdf",
        )
    assert "no extractable" in str(exc_info.value).lower()


def test_report_generation(tmp_path: Path, db_session: Session) -> None:
    """Verify that Markdown report generation produces a complete and valid report document."""
    report_file = tmp_path / "test_report.md"
    results = run_fixture_cases(db_session)
    summary = compute_parity_summary(results, seed=20261002)

    content = generate_markdown_report(summary, results, seed=20261002, report_path=report_file)

    assert report_file.exists()
    assert "# Reconciliation Parity Report" in content
    assert "100% Deterministic Parity Confirmed" in content
    assert "fixture-clean-acme" in content
    assert "fixture-discrepancy-apex" in content
    assert "po_unextractable.pdf" in content
    assert "PriceMismatch" in content
    assert "QuantityOrPackagingBreach" in content
    assert "ArithmeticMismatch.line" in content
    assert "ArithmeticMismatch.order" in content
    assert "CatalogMatchingMismatch" in content
