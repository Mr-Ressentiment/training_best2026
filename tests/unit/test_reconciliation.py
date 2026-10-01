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

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.models.entities import (
    ContractPriceTier,
    DiscrepancyFlag,
    DraftLineItem,
    OrderDraft,
    PurchaseOrderDocument,
)
from app.services.reconciliation import (
    evaluate_clean_draft,
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
