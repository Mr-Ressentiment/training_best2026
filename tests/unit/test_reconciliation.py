"""Unit-level acceptance contract for T026 / P2 deterministic discrepancy engine.

Defines the RED acceptance boundary prior to T028 implementation for:
- PriceMismatch (AC1)
- QuantityOrPackagingBreach (AC2, AC3, AC4)
- ArithmeticMismatch line-level (AC5)
- ArithmeticMismatch order-level (AC6)
- CatalogMatchingMismatch (AC7, AC8, AC9)
- Clean draft / false-positive prevention (AC10)
- Readiness blocker invariant: unresolved discrepancy -> Needs Review (AC11)
- Multiple concurrent discrepancy categories (AC12)
- Pure deterministic execution without AI or network (AC13)
- Operator-selected SKU resolution positive acceptance
- Discrepancy re-evaluation and idempotency (no duplicate unresolved flags)

NOTE on Order-Level ArithmeticMismatch:
Following Human Gate HG-P2-01 ACCEPTED Option A, the canonical model represents
an optional source-backed stated order total (extracted_order_total_cents).
AC6 defines the genuine RED acceptance contract for order-level ArithmeticMismatch
awaiting T028 implementation.
"""

from __future__ import annotations

from decimal import Decimal
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.models.entities import (
    ContractPriceTier,
    DiscrepancyFlag,
    DraftLineItem,
    FieldProvenance,
    OrderDraft,
    PurchaseOrderDocument,
)
from app.services.reconciliation import (
    LineMutationValidationError,
    SourceGroundingMismatchError,
    TerminalDraftMutationError,
    correct_line_field,
    evaluate_clean_draft,
    remove_line,
)


# -----------------------------------------------------------------------------
# Test-local helpers
# -----------------------------------------------------------------------------

def _create_test_draft(
    db: Session,
    *,
    customer_id: str = "CUST-ACME",
    customer_name: str = "Acme Industrial Supplies",
    po_number: str = "PO-10023",
    extracted_order_total_cents: int | None = None,
    line_items: list[DraftLineItem] | None = None,
    raw_text: str = "PURCHASE ORDER\nAcme Industrial Supplies\nPO-10023\n",
) -> OrderDraft:
    """Create a persisted OrderDraft and PurchaseOrderDocument fixture."""
    doc = PurchaseOrderDocument(
        filename="test_po.txt",
        content_type="text/plain",
        raw_text=raw_text,
        status="Ingested",
    )
    db.add(doc)
    db.flush()

    if line_items is None:
        line_items = [
            DraftLineItem(
                line_number=1,
                customer_description="18in stretch film heavy duty",
                extracted_quantity=10,
                extracted_unit_price_cents=2500,
                extracted_line_total_cents=25000,
                matched_sku="SKU-WRAP-18",
                sku_confidence="High",
                sku_resolution_source="AI_HIGH_CONFIDENCE",
                status="Active",
            )
        ]

    draft = OrderDraft(
        document_id=doc.id,
        customer_id=customer_id,
        customer_name_extracted=customer_name,
        po_number_extracted=po_number,
        extracted_order_total_cents=extracted_order_total_cents,
        status="Ingested",
        is_replay_mode=False,
        line_items=line_items,
    )
    db.add(draft)
    db.flush()
    return draft


def _get_discrepancies(
    db: Session,
    draft: OrderDraft,
    line: DraftLineItem | None = None,
) -> list[DiscrepancyFlag]:
    """Retrieve all discrepancy flags associated with the draft or specific line."""
    flags = list(draft.discrepancy_flags)
    if not flags:
        flags = list(
            db.scalars(
                select(DiscrepancyFlag).where(DiscrepancyFlag.draft_id == draft.id)
            ).all()
        )
    if line is not None:
        matched = [f for f in flags if f.line_item_id == line.id or f.line_item is line]
        if not matched and line.discrepancy_flags:
            matched = list(line.discrepancy_flags)
        return matched
    return flags


# -----------------------------------------------------------------------------
# AC1 — PriceMismatch
# -----------------------------------------------------------------------------

def test_price_mismatch_creates_blocking_unresolved_discrepancy(db_session):
    """AC1: Stated unit price != authoritative contract tier price creates PriceMismatch."""
    seed_baseline(db_session)
    # SKU-WRAP-18 under CONTRACT-ACME-2026: Q=10 tier price is 2500 cents ($25.00).
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2400,  # 2400 != authoritative tier price 2500
        extracted_line_total_cents=24000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result, line)
    price_flags = [f for f in flags if f.discrepancy_type == "PriceMismatch"]
    assert len(price_flags) == 1, f"Expected 1 PriceMismatch flag, found {len(price_flags)}"
    flag = price_flags[0]
    assert flag.severity == "Blocking"
    assert flag.resolution_state == "Unresolved"
    assert flag.line_item_id == line.id or flag.line_item is line
    assert flag.expected_value and ("25" in flag.expected_value or "2500" in flag.expected_value)
    assert flag.requested_value and ("24" in flag.requested_value or "2400" in flag.requested_value)
    assert bool(flag.explanation and flag.explanation.strip())


def test_price_mismatch_uses_deterministic_contract_tier_not_catalog_base(db_session):
    """AC1: Price validation strictly uses contract tier lookup, never catalog base price."""
    seed_baseline(db_session)
    # SKU-WRAP-18: catalog base_price_cents=2450.
    # Contract CONTRACT-ACME-2026 tiers: Q=1 -> 2600, Q=10 -> 2500, Q=50 -> 2300.
    # For quantity=50: authoritative tier price is 2300 cents ($23.00).
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty bulk",
        extracted_quantity=50,
        extracted_unit_price_cents=2450,  # Customer stated catalog base price ($24.50)
        extracted_line_total_cents=122500,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result, line)
    price_flags = [f for f in flags if f.discrepancy_type == "PriceMismatch"]
    assert len(price_flags) == 1, "Expected PriceMismatch when stated price equals catalog base instead of tier price"
    flag = price_flags[0]
    assert "23" in flag.expected_value
    assert "24.5" not in flag.expected_value


# -----------------------------------------------------------------------------
# AC2 — QuantityOrPackagingBreach: MOQ
# -----------------------------------------------------------------------------

def test_quantity_below_moq_creates_quantity_or_packaging_breach(db_session):
    """AC2: Requested quantity < catalog product min_order_quantity creates QuantityOrPackagingBreach."""
    seed_baseline(db_session)
    # SKU-WRAP-18 has min_order_quantity = 5.
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=3,  # 3 < MOQ (5)
        extracted_unit_price_cents=2600,  # Tier price for Q=1..9 is 2600
        extracted_line_total_cents=7800,  # 3 * 2600
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result, line)
    moq_flags = [f for f in flags if f.discrepancy_type == "QuantityOrPackagingBreach"]
    assert len(moq_flags) == 1, f"Expected 1 QuantityOrPackagingBreach flag, found {len(moq_flags)}"
    flag = moq_flags[0]
    assert flag.severity == "Blocking"
    assert flag.resolution_state == "Unresolved"
    assert flag.line_item_id == line.id or flag.line_item is line
    assert flag.expected_value and ("5" in flag.expected_value or "MOQ" in flag.expected_value)
    assert flag.requested_value and ("3" in flag.requested_value)
    assert bool(flag.explanation and flag.explanation.strip())


# -----------------------------------------------------------------------------
# AC3 — QuantityOrPackagingBreach: package increment
# -----------------------------------------------------------------------------

def test_quantity_violating_package_increment_creates_quantity_or_packaging_breach(db_session):
    """AC3: Quantity satisfying MOQ but not a multiple of package_increment creates QuantityOrPackagingBreach."""
    seed_baseline(db_session)
    # SKU-TAPE-03: min_order_quantity = 4, package_increment = 2.
    db_session.add(ContractPriceTier(
        id="TIER-ACME-TAPE03-Q1",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-TAPE-03",
        min_quantity=1,
        tier_price_cents=800,
    ))
    db_session.flush()

    line = DraftLineItem(
        line_number=1,
        customer_description="Industrial Filament Strapping Tape 3in",
        extracted_quantity=5,  # 5 >= MOQ (4), but 5 % package_increment (2) != 0
        extracted_unit_price_cents=800,
        extracted_line_total_cents=4000,  # 5 * 800
        matched_sku="SKU-TAPE-03",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result, line)
    pkg_flags = [f for f in flags if f.discrepancy_type == "QuantityOrPackagingBreach"]
    assert len(pkg_flags) == 1, f"Expected 1 QuantityOrPackagingBreach flag for package increment breach, found {len(pkg_flags)}"
    flag = pkg_flags[0]
    assert flag.severity == "Blocking"
    assert flag.resolution_state == "Unresolved"
    assert flag.expected_value and (
        "2" in flag.expected_value
        or "increment" in flag.expected_value.lower()
        or "multiple" in flag.expected_value.lower()
    )
    assert flag.requested_value and ("5" in flag.requested_value)
    assert bool(flag.explanation and flag.explanation.strip())


# -----------------------------------------------------------------------------
# AC4 — valid quantity (negative / clean case)
# -----------------------------------------------------------------------------

def test_valid_quantity_satisfying_moq_and_increment_creates_no_quantity_breach(db_session):
    """AC4: Quantity satisfying both MOQ and package increment creates no QuantityOrPackagingBreach."""
    seed_baseline(db_session)
    # SKU-TAPE-03: min_order_quantity = 4, package_increment = 2.
    db_session.add(ContractPriceTier(
        id="TIER-ACME-TAPE03-Q1",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-TAPE-03",
        min_quantity=1,
        tier_price_cents=800,
    ))
    db_session.flush()

    line = DraftLineItem(
        line_number=1,
        customer_description="Industrial Filament Strapping Tape 3in",
        extracted_quantity=6,  # 6 >= 4 and 6 % 2 == 0
        extracted_unit_price_cents=800,
        extracted_line_total_cents=4800,  # 6 * 800
        matched_sku="SKU-TAPE-03",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    flags = _get_discrepancies(db_session, result, line)
    pkg_flags = [f for f in flags if f.discrepancy_type == "QuantityOrPackagingBreach"]
    assert pkg_flags == [], (
        "Valid quantity satisfying MOQ and package increment must not produce QuantityOrPackagingBreach"
    )


# -----------------------------------------------------------------------------
# AC5 — ArithmeticMismatch: line arithmetic
# -----------------------------------------------------------------------------

def test_line_arithmetic_mismatch_creates_arithmetic_discrepancy(db_session):
    """AC5: Customer stated line total != quantity * unit price creates ArithmeticMismatch."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,  # 10 * 2500 = 25000 cents
        extracted_line_total_cents=26000,  # Stated line total 26000 != 25000
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result, line)
    arithmetic_flags = [f for f in flags if f.discrepancy_type == "ArithmeticMismatch"]
    assert len(arithmetic_flags) == 1, f"Expected 1 ArithmeticMismatch flag, found {len(arithmetic_flags)}"
    flag = arithmetic_flags[0]
    assert flag.severity == "Blocking"
    assert flag.resolution_state == "Unresolved"
    assert flag.line_item_id == line.id or flag.line_item is line
    assert flag.expected_value and ("250" in flag.expected_value or "25000" in flag.expected_value)
    assert flag.requested_value and ("260" in flag.requested_value or "26000" in flag.requested_value)
    assert bool(flag.explanation and flag.explanation.strip())


# -----------------------------------------------------------------------------
# AC6 — ArithmeticMismatch: Order-Level Arithmetic
# (Reconciled under HG-P2-01 Option A; genuine RED acceptance contract for T028)
# -----------------------------------------------------------------------------

def test_order_arithmetic_mismatch_creates_blocking_order_discrepancy(db_session):
    """AC6A: Customer stated order total != sum of customer stated line totals creates order-level ArithmeticMismatch."""
    seed_baseline(db_session)
    line1 = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    line2 = DraftLineItem(
        line_number=2,
        customer_description="Standard pallet wrap",
        extracted_quantity=5,
        extracted_unit_price_cents=2000,
        extracted_line_total_cents=10000,
        matched_sku="SKU-WRAP-15",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    # Stated line total sum: 25000 + 10000 = 35000 cents ($350.00)
    # Stated order total: 36000 cents ($360.00) != 35000
    draft = _create_test_draft(
        db_session,
        extracted_order_total_cents=36000,
        line_items=[line1, line2],
    )

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result)
    order_arith_flags = [
        f for f in flags
        if f.discrepancy_type == "ArithmeticMismatch" and f.line_item_id is None
    ]
    assert len(order_arith_flags) == 1, (
        f"Expected exactly 1 order-level ArithmeticMismatch flag, found {len(order_arith_flags)}"
    )
    flag = order_arith_flags[0]
    assert flag.severity == "Blocking"
    assert flag.resolution_state == "Unresolved"
    assert flag.line_item_id is None
    assert flag.expected_value and ("350" in flag.expected_value or "35000" in flag.expected_value)
    assert flag.requested_value and ("360" in flag.requested_value or "36000" in flag.requested_value)
    assert bool(flag.explanation and flag.explanation.strip())


def test_correct_stated_order_arithmetic_produces_no_order_arithmetic_mismatch(db_session):
    """AC6B: Stated order total == sum of customer stated line totals produces no order ArithmeticMismatch."""
    seed_baseline(db_session)
    line1 = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    line2 = DraftLineItem(
        line_number=2,
        customer_description="Standard pallet wrap",
        extracted_quantity=5,
        extracted_unit_price_cents=2000,
        extracted_line_total_cents=10000,
        matched_sku="SKU-WRAP-15",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    # Stated line sum: 25000 + 10000 = 35000 cents ($350.00)
    # Stated order total: 35000 cents ($350.00) == 35000
    draft = _create_test_draft(
        db_session,
        extracted_order_total_cents=35000,
        line_items=[line1, line2],
    )

    result = evaluate_clean_draft(db_session, draft)

    flags = _get_discrepancies(db_session, result)
    order_arith_flags = [
        f for f in flags
        if f.discrepancy_type == "ArithmeticMismatch" and f.line_item_id is None
    ]
    assert order_arith_flags == [], (
        "Clean matching customer order total must produce no order-level ArithmeticMismatch"
    )


def test_order_arithmetic_separation_from_price_mismatch(db_session):
    """AC6C: PriceMismatch must be flagged while ArithmeticMismatch is not, freezing Human Gate semantics."""
    seed_baseline(db_session)
    # SKU-WRAP-18: Q=10 contract tier price is 2500 cents ($25.00).
    # Customer stated unit price = 2400 cents ($24.00), stated line total = 24000 cents ($240.00).
    # Customer stated order total = 24000 cents ($240.00).
    # Customer math: 10 * $24.00 = $240.00 == order total $240.00 -> Arithmetic is SOUND.
    # Contract pricing: 10 * $25.00 = $250.00 -> PriceMismatch = YES.
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2400,
        extracted_line_total_cents=24000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(
        db_session,
        extracted_order_total_cents=24000,
        line_items=[line],
    )

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result)
    arith_flags = [f for f in flags if f.discrepancy_type == "ArithmeticMismatch"]
    assert arith_flags == [], (
        "Order-level arithmetic must evaluate customer stated values independently from contract pricing"
    )
    price_flags = [f for f in flags if f.discrepancy_type == "PriceMismatch"]
    assert len(price_flags) == 1, (
        f"Expected 1 PriceMismatch flag for unit price breach, found {len(price_flags)}"
    )


def test_incomplete_source_arithmetic_does_not_fabricate_order_arithmetic_mismatch(db_session):
    """AC6D: Missing line total must not trigger fabricated order ArithmeticMismatch, but blocks Ready for Approval."""
    seed_baseline(db_session)
    line1 = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    line2 = DraftLineItem(
        line_number=2,
        customer_description="Standard pallet wrap",
        extracted_quantity=5,
        extracted_unit_price_cents=2000,
        extracted_line_total_cents=None,  # Missing source-stated line total evidence
        matched_sku="SKU-WRAP-15",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(
        db_session,
        extracted_order_total_cents=35000,
        line_items=[line1, line2],
    )

    result = evaluate_clean_draft(db_session, draft)

    assert result.status != "Ready for Approval"
    flags = _get_discrepancies(db_session, result)
    order_arith_flags = [
        f for f in flags
        if f.discrepancy_type == "ArithmeticMismatch" and f.line_item_id is None
    ]
    assert order_arith_flags == [], (
        "Engine must not fabricate order ArithmeticMismatch when source line totals are incomplete"
    )


# -----------------------------------------------------------------------------
# AC7 — CatalogMatchingMismatch: Ambiguous
# -----------------------------------------------------------------------------

def test_ambiguous_sku_creates_catalog_matching_mismatch(db_session):
    """AC7: sku_confidence == 'Ambiguous' creates CatalogMatchingMismatch."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=1,
        customer_description="Standard pallet wrap",
        extracted_quantity=2,
        extracted_unit_price_cents=2000,
        extracted_line_total_cents=4000,
        matched_sku=None,
        sku_confidence="Ambiguous",
        sku_resolution_source="NONE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result, line)
    cat_flags = [f for f in flags if f.discrepancy_type == "CatalogMatchingMismatch"]
    assert len(cat_flags) == 1, f"Expected 1 CatalogMatchingMismatch flag, found {len(cat_flags)}"
    flag = cat_flags[0]
    assert flag.severity == "Blocking"
    assert flag.resolution_state == "Unresolved"
    assert flag.line_item_id == line.id or flag.line_item is line
    assert bool(flag.expected_value and flag.expected_value.strip())
    assert bool(flag.requested_value and flag.requested_value.strip())
    assert bool(flag.explanation and flag.explanation.strip())


# -----------------------------------------------------------------------------
# AC8 — CatalogMatchingMismatch: Unrecognized
# -----------------------------------------------------------------------------

def test_unrecognized_sku_creates_catalog_matching_mismatch_without_auto_substitution(db_session):
    """AC8: sku_confidence == 'Unrecognized' creates CatalogMatchingMismatch without auto-substituting SKU."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=1,
        customer_description="Completely unknown specialty item xyz",
        extracted_quantity=1,
        extracted_unit_price_cents=5000,
        extracted_line_total_cents=5000,
        matched_sku=None,
        sku_confidence="Unrecognized",
        sku_resolution_source="NONE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    assert line.matched_sku is None
    assert line.contract_price_cents is None
    flags = _get_discrepancies(db_session, result, line)
    cat_flags = [f for f in flags if f.discrepancy_type == "CatalogMatchingMismatch"]
    assert len(cat_flags) == 1, f"Expected 1 CatalogMatchingMismatch flag, found {len(cat_flags)}"
    flag = cat_flags[0]
    assert flag.severity == "Blocking"
    assert flag.resolution_state == "Unresolved"
    assert bool(flag.explanation and flag.explanation.strip())


# -----------------------------------------------------------------------------
# AC9 — clean catalog match
# -----------------------------------------------------------------------------

def test_clean_catalog_match_creates_no_catalog_matching_mismatch(db_session):
    """AC9: High confidence match resolved to catalog SKU creates no CatalogMatchingMismatch."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    flags = _get_discrepancies(db_session, result, line)
    cat_flags = [f for f in flags if f.discrepancy_type == "CatalogMatchingMismatch"]
    assert cat_flags == [], "High confidence catalog match must not create CatalogMatchingMismatch"


# -----------------------------------------------------------------------------
# Operator Selected SKU Acceptance Coverage
# -----------------------------------------------------------------------------

def test_operator_selected_sku_is_resolved_and_allows_ready_for_approval(db_session):
    """Operator-selected SKU with High confidence is treated as resolved, allowing Ready for Approval."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="OPERATOR_SELECTED",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    # 1. No unresolved CatalogMatchingMismatch
    flags = _get_discrepancies(db_session, result, line)
    cat_flags = [
        f for f in flags
        if f.discrepancy_type == "CatalogMatchingMismatch" and f.resolution_state == "Unresolved"
    ]
    assert cat_flags == [], "Operator-selected SKU must not produce unresolved CatalogMatchingMismatch"

    # 2. Valid SKU is preserved
    assert line.matched_sku == "SKU-WRAP-18"

    # 3. Deterministic contract tier is evaluated normally
    assert line.contract_price_cents == 2500
    assert line.calculated_line_total_cents == 25000
    assert result.calculated_subtotal_cents == 25000

    # 4. No discrepancy exists solely because resolution source is OPERATOR_SELECTED
    assert flags == [], "No discrepancy should exist solely because resolution source is OPERATOR_SELECTED"

    # 5. When no other discrepancy exists, draft can reach Ready for Approval
    assert result.status == "Ready for Approval"


# -----------------------------------------------------------------------------
# AC10 — no false positive clean case
# -----------------------------------------------------------------------------

def test_clean_draft_has_zero_unresolved_discrepancies_and_ready_for_approval(db_session):
    """AC10: Completely clean draft transitions to Ready for Approval with zero unresolved discrepancies."""
    seed_baseline(db_session)
    lines = [
        DraftLineItem(
            line_number=1,
            customer_description="18in stretch film heavy duty",
            extracted_quantity=10,
            extracted_unit_price_cents=2500,
            extracted_line_total_cents=25000,
            matched_sku="SKU-WRAP-18",
            sku_confidence="High",
            sku_resolution_source="AI_HIGH_CONFIDENCE",
            status="Active",
        ),
        DraftLineItem(
            line_number=2,
            customer_description="Standard Pallet Wrap 15in 65ga",
            extracted_quantity=5,
            extracted_unit_price_cents=2000,
            extracted_line_total_cents=10000,
            matched_sku="SKU-WRAP-15",
            sku_confidence="High",
            sku_resolution_source="AI_HIGH_CONFIDENCE",
            status="Active",
        ),
    ]
    draft = _create_test_draft(db_session, line_items=lines)

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Ready for Approval"
    assert result.calculated_subtotal_cents == 35000
    all_flags = _get_discrepancies(db_session, result)
    unresolved_flags = [f for f in all_flags if f.resolution_state == "Unresolved"]
    assert unresolved_flags == []


# -----------------------------------------------------------------------------
# AC11 — unresolved discrepancy blocks readiness
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("category", [
    "PriceMismatch",
    "QuantityOrPackagingBreach",
    "ArithmeticMismatch",
    "CatalogMatchingMismatch",
])
def test_existing_unresolved_flag_blocks_readiness_for_all_categories(db_session, category):
    """AC11: Any unresolved blocking discrepancy flag of any category forces Needs Review, never Ready for Approval."""
    seed_baseline(db_session)
    draft = _create_test_draft(db_session)
    flag = DiscrepancyFlag(
        draft=draft,
        line_item=draft.line_items[0],
        discrepancy_type=category,
        severity="Blocking",
        expected_value="expected_val",
        requested_value="requested_val",
        explanation=f"Blocking discrepancy of type {category}",
        resolution_state="Unresolved",
    )
    db_session.add(flag)
    db_session.flush()

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    assert result.status != "Ready for Approval"


@pytest.mark.parametrize("defect_type,mutator", [
    ("PriceMismatch", lambda line: setattr(line, "extracted_unit_price_cents", 2499)),
    ("QuantityOrPackagingBreach", lambda line: setattr(line, "extracted_quantity", 2)),
    ("ArithmeticMismatch", lambda line: setattr(line, "extracted_line_total_cents", 99999)),
    ("CatalogMatchingMismatch", lambda line: (
        setattr(line, "sku_confidence", "Ambiguous"),
        setattr(line, "sku_resolution_source", "NONE"),
        setattr(line, "matched_sku", None),
    )),
])
def test_evaluation_detected_discrepancy_blocks_readiness(db_session, defect_type, mutator):
    """AC11: Evaluation-detected discrepancy in each category strictly prevents Ready for Approval."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])
    mutator(line)

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    assert result.status != "Ready for Approval"
    flags = _get_discrepancies(db_session, result, line)
    matching = [f for f in flags if f.discrepancy_type == defect_type]
    assert len(matching) >= 1, f"Expected {defect_type} discrepancy to be generated by evaluation"


# -----------------------------------------------------------------------------
# AC12 — multiple discrepancy categories
# -----------------------------------------------------------------------------

def test_multiple_discrepancy_categories_accumulate_without_early_exit(db_session):
    """AC12: A line/draft violating multiple independent rules detects all applicable discrepancy categories."""
    seed_baseline(db_session)
    # A single line simultaneously has:
    # 1. Price mismatch: extracted_unit_price_cents = 2400 (contract tier is 2600 for Q < 10)
    # 2. MOQ breach: extracted_quantity = 3 (< MOQ 5 for SKU-WRAP-18)
    # 3. Arithmetic mismatch: extracted_line_total_cents = 10000 (3 * 2400 = 7200 != 10000)
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=3,
        extracted_unit_price_cents=2400,
        extracted_line_total_cents=10000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    result = evaluate_clean_draft(db_session, draft)

    assert result.status == "Needs Review"
    flags = _get_discrepancies(db_session, result, line)
    found_types = {f.discrepancy_type for f in flags}
    expected_types = {"PriceMismatch", "QuantityOrPackagingBreach", "ArithmeticMismatch"}

    assert expected_types.issubset(found_types), (
        f"Engine must not stop on first error; expected {expected_types}, got {found_types}"
    )
    assert all(f.severity == "Blocking" for f in flags)
    assert all(f.resolution_state == "Unresolved" for f in flags)


# -----------------------------------------------------------------------------
# Re-evaluation / Idempotency Acceptance Coverage
# -----------------------------------------------------------------------------

def test_discrepancy_re_evaluation_is_idempotent_without_duplicate_flags(db_session):
    """Re-evaluating an unchanged draft must be idempotent and not create duplicate unresolved flags."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2400,  # Contract tier price is 2500 -> PriceMismatch
        extracted_line_total_cents=24000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    # First evaluation
    res1 = evaluate_clean_draft(db_session, draft)
    assert res1.status == "Needs Review"
    flags1 = _get_discrepancies(db_session, res1, line)
    price_flags1 = [
        f for f in flags1
        if f.discrepancy_type == "PriceMismatch" and f.resolution_state == "Unresolved"
    ]
    assert len(price_flags1) == 1, f"Expected 1 unresolved PriceMismatch on first run, found {len(price_flags1)}"

    # Second evaluation on the same unchanged draft
    res2 = evaluate_clean_draft(db_session, draft)
    assert res2.status == "Needs Review"
    flags2 = _get_discrepancies(db_session, res2, line)
    price_flags2 = [
        f for f in flags2
        if f.discrepancy_type == "PriceMismatch" and f.resolution_state == "Unresolved"
    ]
    # Idempotency requirement: exactly one unresolved PriceMismatch, no duplicates created
    assert len(price_flags2) == 1, (
        f"Expected exactly 1 unresolved PriceMismatch after re-evaluation, found {len(price_flags2)} (duplicates generated)"
    )

    # Derived pricing and calculation output remains deterministic
    assert line.contract_price_cents == 2500
    assert line.calculated_line_total_cents == 25000
    assert res2.calculated_subtotal_cents == 25000


# -----------------------------------------------------------------------------
# AC13 — deterministic behavior
# -----------------------------------------------------------------------------

def test_deterministic_reconciliation_repeatability_without_ai_or_network(db_session):
    """AC13: Business-rule evaluation produces identical deterministic output on successive evaluations."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=1,
        customer_description="18in stretch film heavy duty",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    draft = _create_test_draft(db_session, line_items=[line])

    res1 = evaluate_clean_draft(db_session, draft)
    status1 = res1.status
    subtotal1 = res1.calculated_subtotal_cents
    line_total1 = res1.line_items[0].calculated_line_total_cents
    flags1 = [
        (f.discrepancy_type, f.severity, f.resolution_state)
        for f in _get_discrepancies(db_session, res1)
    ]

    res2 = evaluate_clean_draft(db_session, draft)
    status2 = res2.status
    subtotal2 = res2.calculated_subtotal_cents
    line_total2 = res2.line_items[0].calculated_line_total_cents
    flags2 = [
        (f.discrepancy_type, f.severity, f.resolution_state)
        for f in _get_discrepancies(db_session, res2)
    ]

    assert status1 == status2 == "Ready for Approval"
    assert subtotal1 == subtotal2 == 25000
    assert line_total1 == line_total2 == 25000
    assert flags1 == flags2 == []


# =============================================================================
# T029: grounded field correction and line removal (service layer)
# =============================================================================

T029_RAW_TEXT = (
    "PURCHASE ORDER\n"                                                    # line 1
    "Acme Industrial Supplies\n"                                          # line 2
    "PO-10023\n"                                                          # line 3
    "18in stretch film heavy duty   Qty: 10 rolls   $25.00   $250.00\n"   # line 4
    "Standard pallet wrap   2 cs   $20.00   $40.00\n"                     # line 5
    "Ref 210 units, 10.5 kg, 1,000 pcs, -5 adj, .5 gal\n"                 # line 6
    "TOTAL $290.00\n"                                                     # line 7
)


def _txt(line_number: int, snippet: str, raw_text: str = T029_RAW_TEXT) -> dict:
    """TXT location of the first occurrence of snippet on the given 1-based line."""
    return {
        "type": "txt",
        "line_number": line_number,
        "char_offset": raw_text.splitlines()[line_number - 1].index(snippet),
    }


def _t029_draft(
    db: Session, *, quantity_1: int = 10, quantity_2: int = 2, total_2: int = 4000, **overrides,
) -> OrderDraft:
    """Two-line ACME draft over T029_RAW_TEXT, evaluated and flushed."""
    seed_baseline(db)
    lines = [
        DraftLineItem(
            line_number=1,
            customer_description="18in stretch film heavy duty",
            extracted_quantity=quantity_1,
            extracted_unit_price_cents=2500,
            extracted_line_total_cents=25000,
            matched_sku="SKU-WRAP-18",
            sku_confidence="High",
            sku_resolution_source="AI_HIGH_CONFIDENCE",
            status="Active",
        ),
        DraftLineItem(
            line_number=2,
            customer_description="Standard pallet wrap",
            extracted_quantity=quantity_2,
            extracted_unit_price_cents=2000,
            extracted_line_total_cents=total_2,
            matched_sku="SKU-WRAP-15",
            sku_confidence="High",
            sku_resolution_source="AI_HIGH_CONFIDENCE",
            status="Active",
        ),
    ]
    draft = _create_test_draft(db, line_items=lines, raw_text=T029_RAW_TEXT, **overrides)
    evaluate_clean_draft(db, draft)
    db.flush()
    return draft


def _snapshot(draft: OrderDraft) -> tuple:
    """Service-visible state that a failed mutation must leave untouched."""
    return (
        draft.status,
        draft.calculated_subtotal_cents,
        draft.document.raw_text,
        [
            (
                line.customer_description, line.extracted_quantity, line.extracted_unit_price_cents,
                line.extracted_line_total_cents, line.matched_sku, line.sku_confidence,
                line.sku_resolution_source, line.candidate_skus_json, line.matching_rationale,
                line.contract_price_cents, line.calculated_line_total_cents, line.status,
            )
            for line in draft.line_items
        ],
        [(id(flag), flag.discrepancy_type, flag.resolution_state) for flag in draft.discrepancy_flags],
        [
            (id(record), record.field_name, record.verbatim_snippet, record.location_type, record.location_data_json)
            for record in draft.provenance_records
        ],
    )


def _assert_rejected_without_mutation(db: Session, draft: OrderDraft, error: type[Exception], call) -> None:
    db.flush()
    before = _snapshot(draft)
    with pytest.raises(error):
        call()
    assert _snapshot(draft) == before
    assert not db.new and not db.dirty and not db.deleted


def _states(draft: OrderDraft, line: DraftLineItem | None, discrepancy_type: str) -> list[str]:
    return sorted(
        flag.resolution_state for flag in draft.discrepancy_flags
        if flag.line_item is line and flag.discrepancy_type == discrepancy_type
    )


def test_t029_grounded_txt_quantity_correction_updates_field_provenance_flags_and_readiness(db_session):
    """Test A: a quantity mis-extracted as 1 is corrected to the source-stated 10."""
    # Line 2 is internally consistent, so only line 1 blocks the draft.
    draft = _t029_draft(db_session, quantity_1=1, quantity_2=5, total_2=10000)
    line = draft.line_items[0]
    stale = FieldProvenance(
        draft=draft, line_item=line, field_name="extracted_quantity", verbatim_snippet="1",
        location_type="txt", location_data_json='{"char_offset":0,"line_number":4,"type":"txt"}',
    )
    db_session.add(stale)
    evaluate_clean_draft(db_session, draft)
    db_session.flush()
    for category in ("QuantityOrPackagingBreach", "ArithmeticMismatch", "PriceMismatch"):
        assert _states(draft, line, category) == ["Unresolved"]
    assert draft.status == "Needs Review"
    flag_count = len(draft.discrepancy_flags)
    location = _txt(4, "Qty: 10 rolls")

    result = correct_line_field(
        db_session, draft, line, field="extracted_quantity", value=10,
        source_snippet="Qty: 10 rolls", source_location=location,
    )

    assert result is draft
    assert line.extracted_quantity == 10
    for category in ("QuantityOrPackagingBreach", "ArithmeticMismatch", "PriceMismatch"):
        assert _states(draft, line, category) == ["ResolvedByCorrection"]
    assert len(draft.discrepancy_flags) == flag_count  # history kept, nothing new
    assert line.contract_price_cents == 2500
    assert line.calculated_line_total_cents == 25000
    assert draft.calculated_subtotal_cents == 35000
    assert draft.status == "Ready for Approval"
    records = [r for r in line.provenance_records if r.field_name == "extracted_quantity"]
    assert records == [stale]
    assert stale.verbatim_snippet == "Qty: 10 rolls"
    assert stale.location_type == "txt"
    assert json.loads(stale.location_data_json) == location
    db_session.flush()
    assert db_session.scalars(select(DraftLineItem).where(DraftLineItem.id == line.id)).one().extracted_quantity == 10


def test_t029_correction_to_source_value_that_still_breaches_keeps_draft_blocked(db_session):
    """A grounded correction is not a commercial override: the true source value is re-evaluated."""
    draft = _t029_draft(db_session, quantity_2=3)
    line = draft.line_items[1]
    assert _states(draft, line, "QuantityOrPackagingBreach") == ["Unresolved"]

    correct_line_field(
        db_session, draft, line, field="extracted_quantity", value=2,
        source_snippet="2 cs", source_location=_txt(5, "2 cs"),
    )

    assert line.extracted_quantity == 2
    assert _states(draft, line, "QuantityOrPackagingBreach") == ["ResolvedByCorrection", "Unresolved"]
    current = next(
        flag for flag in draft.discrepancy_flags
        if flag.line_item is line and flag.discrepancy_type == "QuantityOrPackagingBreach"
        and flag.resolution_state == "Unresolved"
    )
    assert current.requested_value == "Qty: 2"
    assert draft.status == "Needs Review"


def test_t029_forged_txt_snippet_is_rejected_without_mutation(db_session):
    """Test B: the cited location holds "2"; the caller claims "10" (MOQ bypass attempt)."""
    draft = _t029_draft(db_session)
    line = draft.line_items[1]
    location = _txt(5, "2 cs")
    assert T029_RAW_TEXT.splitlines()[4][location["char_offset"]] == "2"

    _assert_rejected_without_mutation(
        db_session, draft, SourceGroundingMismatchError,
        lambda: correct_line_field(
            db_session, draft, line, field="extracted_quantity", value=10,
            source_snippet="10", source_location=location,
        ),
    )
    assert line.extracted_quantity == 2


def test_t029_real_snippet_with_dishonest_value_is_rejected(db_session):
    """Test C: the snippet is genuinely at the location but does not state the value."""
    draft = _t029_draft(db_session)
    line = draft.line_items[1]

    _assert_rejected_without_mutation(
        db_session, draft, SourceGroundingMismatchError,
        lambda: correct_line_field(
            db_session, draft, line, field="extracted_quantity", value=10,
            source_snippet="2 cs", source_location=_txt(5, "2 cs"),
        ),
    )


def test_t029_snippet_elsewhere_on_the_line_is_not_searched_for(db_session):
    """Test D: "10" exists on line 4, but not at the claimed offset."""
    draft = _t029_draft(db_session, quantity_1=1)
    line = draft.line_items[0]
    real = _txt(4, "10")

    for offset in (real["char_offset"] - 1, real["char_offset"] + 1, 0):
        _assert_rejected_without_mutation(
            db_session, draft, SourceGroundingMismatchError,
            lambda offset=offset: correct_line_field(
                db_session, draft, line, field="extracted_quantity", value=10,
                source_snippet="10", source_location={"type": "txt", "line_number": 4, "char_offset": offset},
            ),
        )
    # Same snippet on the wrong line, and a snippet running past the end of its line.
    for location, snippet in (
        ({"type": "txt", "line_number": 5, "char_offset": real["char_offset"]}, "10"),
        ({"type": "txt", "line_number": 99, "char_offset": 0}, "10"),
    ):
        _assert_rejected_without_mutation(
            db_session, draft, SourceGroundingMismatchError,
            lambda location=location, snippet=snippet: correct_line_field(
                db_session, draft, line, field="extracted_quantity", value=10,
                source_snippet=snippet, source_location=location,
            ),
        )
    # A snippet may not run past the end of its line into the next one, even though
    # the same characters are contiguous in raw_text and state the value.
    spanning = "$250.00\nStandard pallet wrap   2 cs"
    assert spanning in T029_RAW_TEXT
    _assert_rejected_without_mutation(
        db_session, draft, SourceGroundingMismatchError,
        lambda: correct_line_field(
            db_session, draft, line, field="customer_description", value="Standard pallet wrap",
            source_snippet=spanning, source_location=_txt(4, "$250.00"),
        ),
    )


@pytest.mark.parametrize("snippet, value", [
    ("10", 10),       # only ever part of "210" or "10.5" on this line
    ("10", 210),      # the snippet does not contain the whole "210" token
    ("21", 210),
    ("210", 21),      # value is only a prefix of the token
    ("10.5", 10),     # fractional token is not an integer quantity
    ("1,000", 1000),  # comma tokens are not interpreted
    ("1", 1),         # cut out of "210", "10.5" or "1,000"
    ("-5", 5),        # signed token
    ("5", 5),         # cut out of "10.5", "-5" or ".5"
    (".5", 5),        # leading-dot token
])
def test_t029_quantity_requires_a_whole_numeric_token(db_session, snippet, value):
    """Section 20: a value is not grounded by digits inside a larger, signed or fractional number."""
    draft = _t029_draft(db_session)
    line = draft.line_items[0]
    source_line = T029_RAW_TEXT.splitlines()[5]
    offsets = [index for index in range(len(source_line)) if source_line.startswith(snippet, index)]
    assert offsets

    for offset in offsets:
        _assert_rejected_without_mutation(
            db_session, draft, SourceGroundingMismatchError,
            lambda offset=offset: correct_line_field(
                db_session, draft, line, field="extracted_quantity", value=value,
                source_snippet=snippet, source_location={"type": "txt", "line_number": 6, "char_offset": offset},
            ),
        )


def test_t029_t027_anchor_on_committed_discrepancy_fixture(db_session, po_discrepancy_apex_text):
    """The T027 acceptance example: line 10, offset 43 of the committed fixture states "2", not "10"."""
    seed_baseline(db_session)
    line = DraftLineItem(
        line_number=2, customer_description="Standard pallet wrap", extracted_quantity=2,
        extracted_unit_price_cents=2000, extracted_line_total_cents=4000, matched_sku="SKU-WRAP-15",
        sku_confidence="High", sku_resolution_source="AI_HIGH_CONFIDENCE", status="Active",
    )
    draft = _create_test_draft(
        db_session, customer_id="CUST-APEX", customer_name="Apex Distribution", po_number="PO-APEX",
        line_items=[line], raw_text=po_discrepancy_apex_text,
    )
    evaluate_clean_draft(db_session, draft)
    location = {"type": "txt", "line_number": 10, "char_offset": 43}
    assert po_discrepancy_apex_text.splitlines()[9][43] == "2"

    _assert_rejected_without_mutation(
        db_session, draft, SourceGroundingMismatchError,
        lambda: correct_line_field(
            db_session, draft, line, field="extracted_quantity", value=10,
            source_snippet="10", source_location=location,
        ),
    )

    correct_line_field(
        db_session, draft, line, field="extracted_quantity", value=2,
        source_snippet="2", source_location=location,
    )
    assert line.extracted_quantity == 2
    assert draft.status == "Needs Review"  # the true source quantity still breaches MOQ


@pytest.mark.parametrize("snippet", ["10", "10 rolls", "Qty: 10", "Qty: 10 rolls   $25.00"])
def test_t029_quantity_is_grounded_by_a_whole_token_with_surrounding_text(db_session, snippet):
    draft = _t029_draft(db_session, quantity_1=1)
    line = draft.line_items[0]

    correct_line_field(
        db_session, draft, line, field="extracted_quantity", value=10,
        source_snippet=snippet, source_location=_txt(4, snippet),
    )

    assert line.extracted_quantity == 10


@pytest.mark.parametrize("location", [
    {"type": "txt", "line_number": 0, "char_offset": 0},
    {"type": "txt", "line_number": -1, "char_offset": 0},
    {"type": "txt", "line_number": 4, "char_offset": -1},
    {"type": "txt", "line_number": 4.0, "char_offset": 36},
    {"type": "txt", "line_number": 4, "char_offset": 36.5},
    {"type": "txt", "line_number": "4", "char_offset": 36},
    {"type": "txt", "line_number": 4, "char_offset": "36"},
    {"type": "txt", "line_number": True, "char_offset": 36},
    {"type": "txt", "line_number": 4},
    {"type": "txt", "line_number": 4, "char_offset": 36, "char_end": 38},
    {"type": "TXT", "line_number": 4, "char_offset": 36},
    {"type": "docx", "line_number": 4, "char_offset": 36},
    {"line_number": 4, "char_offset": 36},
    {"type": "pdf", "page_number": 1, "char_start": 0, "char_end": 2},  # PDF location on a TXT document
    None,
    "line 4",
    [4, 36],
])
def test_t029_malformed_txt_location_fails_closed(db_session, location):
    """Test E: non-canonical locations are rejected, never coerced."""
    draft = _t029_draft(db_session, quantity_1=1)
    line = draft.line_items[0]
    assert T029_RAW_TEXT.splitlines()[3][36:38] == "10"

    _assert_rejected_without_mutation(
        db_session, draft, SourceGroundingMismatchError,
        lambda: correct_line_field(
            db_session, draft, line, field="extracted_quantity", value=10,
            source_snippet="10", source_location=location,
        ),
    )


@pytest.mark.parametrize("snippet", ["", None, 10, b"10"])
def test_t029_source_snippet_must_be_a_non_empty_string(db_session, snippet):
    draft = _t029_draft(db_session, quantity_1=1)

    _assert_rejected_without_mutation(
        db_session, draft, SourceGroundingMismatchError,
        lambda: correct_line_field(
            db_session, draft, draft.line_items[0], field="extracted_quantity", value=10,
            source_snippet=snippet, source_location=_txt(4, "10"),
        ),
    )


def _pdf_draft(db: Session) -> tuple[OrderDraft, DraftLineItem]:
    draft = _t029_draft(db, quantity_1=1)
    draft.document.content_type = "application/pdf"
    draft.document.filename = "test_po.pdf"
    db.flush()
    return draft, draft.line_items[0]


def test_t029_valid_pdf_canonical_span_grounds_a_correction(db_session):
    """Test F: raw_text[char_start:char_end] equals the snippet."""
    draft, line = _pdf_draft(db_session)
    start = T029_RAW_TEXT.index("Qty: 10 rolls")
    location = {"type": "pdf", "page_number": 1, "char_start": start, "char_end": start + len("Qty: 10 rolls")}

    correct_line_field(
        db_session, draft, line, field="extracted_quantity", value=10,
        source_snippet="Qty: 10 rolls", source_location=location,
    )

    assert line.extracted_quantity == 10
    record = next(r for r in line.provenance_records if r.field_name == "extracted_quantity")
    assert record.location_type == "pdf"
    assert json.loads(record.location_data_json) == location
    assert record.verbatim_snippet == "Qty: 10 rolls"


def test_t029_invalid_pdf_span_is_rejected(db_session):
    """Test G: malformed, out-of-range and non-matching PDF spans all fail grounding."""
    draft, line = _pdf_draft(db_session)
    start = T029_RAW_TEXT.index("10 rolls")
    end = start + 2
    size = len(T029_RAW_TEXT)
    cases = [
        ({"type": "pdf", "page_number": 1, "char_start": -1, "char_end": end}, "10"),
        ({"type": "pdf", "page_number": 1, "char_start": start, "char_end": start}, "10"),
        ({"type": "pdf", "page_number": 1, "char_start": end, "char_end": start}, "10"),
        ({"type": "pdf", "page_number": 1, "char_start": size - 1, "char_end": size + 1}, "\n"),
        ({"type": "pdf", "page_number": 1, "char_start": start, "char_end": end + 1}, "10"),
        ({"type": "pdf", "page_number": 1, "char_start": start + 1, "char_end": end + 1}, "10"),
        ({"type": "pdf", "page_number": 0, "char_start": start, "char_end": end}, "10"),
        ({"type": "pdf", "page_number": 1, "char_start": float(start), "char_end": end}, "10"),
        ({"type": "pdf", "page_number": 1, "char_start": str(start), "char_end": end}, "10"),
        ({"type": "pdf", "page_number": 1, "char_start": start}, "10"),
        ({"type": "txt", "line_number": 4, "char_offset": 36}, "10"),  # TXT location on a PDF document
    ]
    for location, snippet in cases:
        _assert_rejected_without_mutation(
            db_session, draft, SourceGroundingMismatchError,
            lambda location=location, snippet=snippet: correct_line_field(
                db_session, draft, line, field="extracted_quantity", value=10,
                source_snippet=snippet, source_location=location,
            ),
        )
    # The same span is accepted once it is exact.
    correct_line_field(
        db_session, draft, line, field="extracted_quantity", value=10, source_snippet="10",
        source_location={"type": "pdf", "page_number": 1, "char_start": start, "char_end": end},
    )
    assert line.extracted_quantity == 10


@pytest.mark.parametrize("value", ["25.00", "25", Decimal("25.00"), Decimal("25"), 25])
def test_t029_money_correction_is_grounded_by_exact_amount(db_session, value):
    """Test H: "$25.00" grounds exactly 25.00 and is persisted as integer cents."""
    draft = _t029_draft(db_session)
    line = draft.line_items[0]
    line.extracted_unit_price_cents = 2400
    evaluate_clean_draft(db_session, draft)
    assert _states(draft, line, "PriceMismatch") == ["Unresolved"]
    assert _states(draft, line, "ArithmeticMismatch") == ["Unresolved"]

    correct_line_field(
        db_session, draft, line, field="extracted_unit_price", value=value,
        source_snippet="$25.00", source_location=_txt(4, "$25.00"),
    )

    assert line.extracted_unit_price_cents == 2500
    assert type(line.extracted_unit_price_cents) is int
    assert _states(draft, line, "PriceMismatch") == ["ResolvedByCorrection"]
    assert _states(draft, line, "ArithmeticMismatch") == ["ResolvedByCorrection"]


@pytest.mark.parametrize("value, snippet", [
    ("24.50", "$25.00"),    # different amount
    ("250.00", "$25.00"),   # different amount sharing digits
    ("25.00", "$250.00"),   # snippet states another amount
    ("2.00", "$20.00"),
    ("5.00", "$25.00"),     # digits inside the token
])
def test_t029_money_correction_with_a_different_amount_is_rejected(db_session, value, snippet):
    draft = _t029_draft(db_session)
    line = draft.line_items[0]
    line_number = 4 if snippet in T029_RAW_TEXT.splitlines()[3] else 5

    _assert_rejected_without_mutation(
        db_session, draft, SourceGroundingMismatchError,
        lambda: correct_line_field(
            db_session, draft, line, field="extracted_unit_price", value=value,
            source_snippet=snippet, source_location=_txt(line_number, snippet),
        ),
    )


@pytest.mark.parametrize("value", [
    25.0, 25.005, "25.005", Decimal("25.005"), "-25.00", Decimal("-25.00"), -25, True,
    "25.", ".25", " 25.00", "25,00", "2.5e1", "NaN", Decimal("NaN"), Decimal("Infinity"), "$25.00", None, [25],
])
@pytest.mark.parametrize("field", ["extracted_unit_price", "extracted_line_total"])
def test_t029_invalid_money_values_are_rejected_without_rounding(db_session, field, value):
    """Test H: floats, sub-cent, negative and non-plain values never reach persistence."""
    draft = _t029_draft(db_session)

    _assert_rejected_without_mutation(
        db_session, draft, LineMutationValidationError,
        lambda: correct_line_field(
            db_session, draft, draft.line_items[0], field=field, value=value,
            source_snippet="$25.00", source_location=_txt(4, "$25.00"),
        ),
    )


@pytest.mark.parametrize("value", [0, -1, 10.0, "10", True, None, Decimal("10")])
def test_t029_invalid_quantity_values_are_rejected(db_session, value):
    draft = _t029_draft(db_session, quantity_1=1)

    _assert_rejected_without_mutation(
        db_session, draft, LineMutationValidationError,
        lambda: correct_line_field(
            db_session, draft, draft.line_items[0], field="extracted_quantity", value=value,
            source_snippet="10", source_location=_txt(4, "10"),
        ),
    )


def test_t029_line_total_correction_also_closes_stale_order_level_arithmetic(db_session):
    """Section 26: the order-level check sums stated line totals."""
    draft = _t029_draft(db_session, quantity_2=5, extracted_order_total_cents=29000)
    line1, line2 = draft.line_items
    line1.extracted_line_total_cents = 20000  # mis-extracted; source says $250.00
    evaluate_clean_draft(db_session, draft)
    assert _states(draft, line1, "ArithmeticMismatch") == ["Unresolved"]
    assert _states(draft, None, "ArithmeticMismatch") == ["Unresolved"]
    assert _states(draft, line2, "ArithmeticMismatch") == ["Unresolved"]  # 5 x $20.00 != $40.00

    correct_line_field(
        db_session, draft, line1, field="extracted_line_total", value="250.00",
        source_snippet="$250.00", source_location=_txt(4, "$250.00"),
    )

    assert line1.extracted_line_total_cents == 25000
    assert _states(draft, line1, "ArithmeticMismatch") == ["ResolvedByCorrection"]
    assert _states(draft, None, "ArithmeticMismatch") == ["ResolvedByCorrection"]  # 250 + 40 == 290
    assert _states(draft, line2, "ArithmeticMismatch") == ["Unresolved"]  # another line's flag is untouched
    assert draft.status == "Needs Review"


@pytest.mark.parametrize("status", ["Approved", "Rejected"])
def test_t029_terminal_draft_correction_fails_before_mutation(db_session, status):
    """Test I: the terminal check precedes every other validation."""
    draft = _t029_draft(db_session, quantity_1=1)
    draft.status = status

    _assert_rejected_without_mutation(
        db_session, draft, TerminalDraftMutationError,
        lambda: correct_line_field(
            db_session, draft, draft.line_items[0], field="extracted_quantity", value=10,
            source_snippet="Qty: 10 rolls", source_location=_txt(4, "Qty: 10 rolls"),
        ),
    )
    _assert_rejected_without_mutation(
        db_session, draft, TerminalDraftMutationError,
        lambda: correct_line_field(
            db_session, draft, draft.line_items[0], field="status", value=object(),
            source_snippet="", source_location=None,
        ),
    )


@pytest.mark.parametrize("status", ["Approved", "Rejected"])
def test_t029_terminal_draft_removal_fails_before_mutation(db_session, status):
    """Test J: a terminal draft's lines cannot be removed."""
    draft = _t029_draft(db_session)
    draft.status = status

    _assert_rejected_without_mutation(
        db_session, draft, TerminalDraftMutationError,
        lambda: remove_line(db_session, draft, draft.line_items[1]),
    )
    assert draft.line_items[1].status == "Active"


def test_t029_line_of_another_draft_fails_closed(db_session):
    """Section 11: mismatched draft/line objects are rejected by both mutations."""
    draft = _t029_draft(db_session, quantity_1=1)
    foreign_line = DraftLineItem(
        line_number=1, customer_description="18in stretch film heavy duty", extracted_quantity=1,
        extracted_unit_price_cents=2500, extracted_line_total_cents=25000, matched_sku="SKU-WRAP-18",
        sku_confidence="High", sku_resolution_source="AI_HIGH_CONFIDENCE", status="Active",
    )
    other = _create_test_draft(db_session, line_items=[foreign_line], raw_text=T029_RAW_TEXT)
    evaluate_clean_draft(db_session, other)
    db_session.flush()
    other_before = _snapshot(other)

    _assert_rejected_without_mutation(
        db_session, draft, LineMutationValidationError,
        lambda: correct_line_field(
            db_session, draft, foreign_line, field="extracted_quantity", value=10,
            source_snippet="Qty: 10 rolls", source_location=_txt(4, "Qty: 10 rolls"),
        ),
    )
    _assert_rejected_without_mutation(
        db_session, draft, LineMutationValidationError,
        lambda: remove_line(db_session, draft, foreign_line),
    )
    assert _snapshot(other) == other_before


@pytest.mark.parametrize("field", [
    "matched_sku", "sku_confidence", "sku_resolution_source", "candidate_skus", "contract_price",
    "calculated_line_total", "line_number", "status", "draft_id", "customer_id", "po_number",
    "customer_name", "extracted_order_total", "extracted_unit_price_cents", "", None, 4,
])
def test_t029_only_the_four_extracted_line_fields_are_correctable(db_session, field):
    """Section 12."""
    draft = _t029_draft(db_session)

    _assert_rejected_without_mutation(
        db_session, draft, LineMutationValidationError,
        lambda: correct_line_field(
            db_session, draft, draft.line_items[0], field=field, value=10,
            source_snippet="10", source_location=_txt(4, "10"),
        ),
    )


def test_t029_description_correction_is_exact_and_never_rematches_the_sku(db_session):
    """Sections 19, 24, 26: text is grounded case-sensitively; SKU state and its flag stay as they are."""
    draft = _t029_draft(db_session, quantity_2=5)
    line = draft.line_items[1]
    line.customer_description = "Standard palet wrp"
    line.extracted_line_total_cents = 10000
    line.matched_sku = None
    line.sku_confidence = "Ambiguous"
    line.sku_resolution_source = "NONE"
    line.candidate_skus_json = '[{"sku":"SKU-WRAP-15"}]'
    line.matching_rationale = "Two wraps are plausible."
    evaluate_clean_draft(db_session, draft)
    assert _states(draft, line, "CatalogMatchingMismatch") == ["Unresolved"]
    snippet = "Standard pallet wrap   2 cs"

    for dishonest in ("Industrial stretch film", "standard pallet wrap", "Standard pallet wrap 15in"):
        _assert_rejected_without_mutation(
            db_session, draft, SourceGroundingMismatchError,
            lambda dishonest=dishonest: correct_line_field(
                db_session, draft, line, field="customer_description", value=dishonest,
                source_snippet=snippet, source_location=_txt(5, snippet),
            ),
        )
    for blank in ("", "   ", None, 5):
        _assert_rejected_without_mutation(
            db_session, draft, LineMutationValidationError,
            lambda blank=blank: correct_line_field(
                db_session, draft, line, field="customer_description", value=blank,
                source_snippet=snippet, source_location=_txt(5, snippet),
            ),
        )

    correct_line_field(
        db_session, draft, line, field="customer_description", value="Standard pallet wrap",
        source_snippet=snippet, source_location=_txt(5, snippet),
    )

    assert line.customer_description == "Standard pallet wrap"
    assert (line.matched_sku, line.sku_confidence, line.sku_resolution_source) == (None, "Ambiguous", "NONE")
    assert line.candidate_skus_json == '[{"sku":"SKU-WRAP-15"}]'
    assert line.matching_rationale == "Two wraps are plausible."
    assert _states(draft, line, "CatalogMatchingMismatch") == ["Unresolved"]
    assert draft.status == "Needs Review"


def test_t029_provenance_is_created_when_absent_and_never_duplicated(db_session):
    """Section 23."""
    draft = _t029_draft(db_session, quantity_1=1)
    line = draft.line_items[0]
    assert line.provenance_records == []

    correct_line_field(
        db_session, draft, line, field="extracted_quantity", value=10,
        source_snippet="Qty: 10", source_location=_txt(4, "Qty: 10"),
    )
    correct_line_field(
        db_session, draft, line, field="extracted_quantity", value=10,
        source_snippet="10 rolls", source_location=_txt(4, "10 rolls"),
    )
    correct_line_field(
        db_session, draft, line, field="extracted_unit_price", value="25.00",
        source_snippet="$25.00", source_location=_txt(4, "$25.00"),
    )
    db_session.flush()

    persisted = db_session.scalars(
        select(FieldProvenance).where(FieldProvenance.line_item_id == line.id)
    ).all()
    assert sorted(record.field_name for record in persisted) == ["extracted_quantity", "extracted_unit_price"]
    quantity = next(record for record in persisted if record.field_name == "extracted_quantity")
    assert quantity.draft_id == draft.id
    assert quantity.verbatim_snippet == "10 rolls"
    assert quantity.location_data_json == json.dumps(
        _txt(4, "10 rolls"), sort_keys=True, separators=(",", ":"),
    )


def test_t029_removed_line_cannot_be_corrected(db_session):
    draft = _t029_draft(db_session, quantity_1=1)
    line = draft.line_items[0]
    remove_line(db_session, draft, line)

    _assert_rejected_without_mutation(
        db_session, draft, LineMutationValidationError,
        lambda: correct_line_field(
            db_session, draft, line, field="extracted_quantity", value=10,
            source_snippet="Qty: 10 rolls", source_location=_txt(4, "Qty: 10 rolls"),
        ),
    )
    assert line.status == "Removed"


def test_t029_remove_line_keeps_history_and_resolves_every_unresolved_line_flag(db_session):
    """Sections 29, 30, 32."""
    draft = _t029_draft(db_session)
    kept, removed = draft.line_items
    removed.extracted_unit_price_cents = 1900
    removed.matched_sku = None
    removed.sku_confidence = "Unrecognized"
    removed.sku_resolution_source = "NONE"
    evaluate_clean_draft(db_session, draft)
    removed.matched_sku = "SKU-WRAP-15"  # lets the quantity rule see the product as well
    evaluate_clean_draft(db_session, draft)
    earlier = DiscrepancyFlag(
        draft=draft, line_item=removed, discrepancy_type="PriceMismatch", severity="Blocking",
        expected_value="$20.00", requested_value="$18.00", explanation="Earlier, already corrected.",
        resolution_state="ResolvedByCorrection",
    )
    provenance = FieldProvenance(
        draft=draft, line_item=removed, field_name="extracted_quantity", verbatim_snippet="2 cs",
        location_type="txt", location_data_json=json.dumps(_txt(5, "2 cs"), sort_keys=True, separators=(",", ":")),
    )
    db_session.add_all([earlier, provenance])
    db_session.flush()
    unresolved_before = {
        flag.discrepancy_type for flag in removed.discrepancy_flags if flag.resolution_state == "Unresolved"
    }
    assert unresolved_before == {
        "CatalogMatchingMismatch", "QuantityOrPackagingBreach", "ArithmeticMismatch", "PriceMismatch",
    }
    flag_ids = {flag.id for flag in draft.discrepancy_flags}

    result = remove_line(db_session, draft, removed)
    db_session.flush()

    assert result is draft
    assert removed.status == "Removed"
    assert kept.status == "Active"
    assert db_session.get(DraftLineItem, removed.id) is removed
    assert db_session.get(FieldProvenance, provenance.id) is provenance
    assert {flag.id for flag in draft.discrepancy_flags} == flag_ids  # nothing deleted, nothing re-raised
    assert earlier.resolution_state == "ResolvedByCorrection"
    assert all(
        flag.resolution_state == "ResolvedByLineRemoval"
        for flag in removed.discrepancy_flags if flag is not earlier
    )
    assert draft.calculated_subtotal_cents == kept.calculated_line_total_cents == 25000
    assert draft.status == "Ready for Approval"


def test_t029_remove_line_preserves_blockers_of_remaining_lines(db_session):
    draft = _t029_draft(db_session)
    removed, blocked = draft.line_items
    assert _states(draft, blocked, "QuantityOrPackagingBreach") == ["Unresolved"]

    remove_line(db_session, draft, removed)

    assert _states(draft, blocked, "QuantityOrPackagingBreach") == ["Unresolved"]
    assert draft.calculated_subtotal_cents == blocked.calculated_line_total_cents
    assert draft.status == "Needs Review"


def test_t029_remove_line_reconciles_stale_order_level_arithmetic(db_session):
    """Section 31: the order-level flag is closed, then re-raised with current values if still wrong."""
    draft = _t029_draft(db_session, quantity_2=5, extracted_order_total_cents=25000)
    kept, removed = draft.line_items
    removed.extracted_line_total_cents = 10000
    evaluate_clean_draft(db_session, draft)
    assert _states(draft, None, "ArithmeticMismatch") == ["Unresolved"]  # 250 + 100 != 250

    remove_line(db_session, draft, removed)

    assert _states(draft, None, "ArithmeticMismatch") == ["ResolvedByLineRemoval"]  # 250 == 250
    assert draft.status == "Ready for Approval"

    # Still wrong after removal: history is closed and a current flag carries the new values.
    draft2 = _t029_draft(db_session, quantity_2=5, extracted_order_total_cents=99900)
    kept2, removed2 = draft2.line_items
    stale = next(flag for flag in draft2.discrepancy_flags if flag.line_item is None)
    assert stale.expected_value == "$290.00"

    remove_line(db_session, draft2, removed2)

    assert stale.resolution_state == "ResolvedByLineRemoval"
    assert _states(draft2, None, "ArithmeticMismatch") == ["ResolvedByLineRemoval", "Unresolved"]
    current = next(
        flag for flag in draft2.discrepancy_flags
        if flag.line_item is None and flag.resolution_state == "Unresolved"
    )
    assert current.expected_value == "$250.00"
    assert draft2.status == "Needs Review"


def test_t029_removing_every_line_never_makes_the_draft_ready(db_session):
    """Section 33."""
    draft = _t029_draft(db_session, quantity_2=5)

    for line in list(draft.line_items):
        remove_line(db_session, draft, line)

    assert [line.status for line in draft.line_items] == ["Removed", "Removed"]
    assert draft.calculated_subtotal_cents == 0
    assert all(flag.resolution_state != "Unresolved" for flag in draft.discrepancy_flags)
    assert draft.status == "Needs Review"


def test_t029_repeated_removal_is_a_side_effect_free_no_op(db_session):
    """Section 34."""
    draft = _t029_draft(db_session)
    line = draft.line_items[1]
    remove_line(db_session, draft, line)
    db_session.flush()
    before = _snapshot(draft)

    assert remove_line(db_session, draft, line) is draft

    assert _snapshot(draft) == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    assert line.status == "Removed"


def test_t029_mutations_leave_the_transaction_to_the_caller(db_session, monkeypatch):
    """Section 8: no commit or rollback; an outer rollback undoes the whole mutation."""
    draft = _t029_draft(db_session, quantity_1=1)
    line1, line2 = draft.line_items
    db_session.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("T029 must not own the transaction")

    with monkeypatch.context() as patched:
        patched.setattr(db_session, "commit", forbidden)
        patched.setattr(db_session, "rollback", forbidden)
        correct_line_field(
            db_session, draft, line1, field="extracted_quantity", value=10,
            source_snippet="Qty: 10 rolls", source_location=_txt(4, "Qty: 10 rolls"),
        )
        remove_line(db_session, draft, line2)
        with pytest.raises(SourceGroundingMismatchError):
            correct_line_field(
                db_session, draft, line1, field="extracted_quantity", value=7,
                source_snippet="Qty: 10 rolls", source_location=_txt(4, "Qty: 10 rolls"),
            )
    assert (line1.extracted_quantity, line2.status) == (10, "Removed")

    db_session.rollback()

    assert (line1.extracted_quantity, line2.status) == (1, "Active")
    assert db_session.scalars(
        select(FieldProvenance).where(FieldProvenance.draft_id == draft.id)
    ).all() == []


def test_t029_canonical_raw_text_is_never_modified(db_session):
    draft = _t029_draft(db_session, quantity_1=1)
    line1, line2 = draft.line_items

    correct_line_field(
        db_session, draft, line1, field="extracted_quantity", value=10,
        source_snippet="Qty: 10 rolls", source_location=_txt(4, "Qty: 10 rolls"),
    )
    remove_line(db_session, draft, line2)
    db_session.flush()

    assert draft.document.raw_text == T029_RAW_TEXT
    assert draft.document not in db_session.dirty
