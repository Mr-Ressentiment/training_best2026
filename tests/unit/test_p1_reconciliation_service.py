"""P1 readiness regressions without the deferred P2 discrepancy engine."""

from datetime import date

import pytest
from sqlalchemy import delete, select

from app.cli import seed_baseline
from app.models.entities import (
    ContractPriceTier, CustomerContract, DiscrepancyFlag, DraftLineItem,
    OrderDraft, PurchaseOrderDocument,
)
from app.services.reconciliation import evaluate_clean_draft


@pytest.fixture
def clean_draft(db_session, po_clean_acme_text):
    seed_baseline(db_session)
    draft = OrderDraft(
        document=PurchaseOrderDocument(
            filename="po_clean_acme.txt", content_type="text/plain",
            raw_text=po_clean_acme_text, status="Ingested",
        ),
        customer_id="CUST-ACME", customer_name_extracted="Acme Industrial Supplies",
        po_number_extracted="PO-10023", status="Ingested", is_replay_mode=False,
        line_items=[
            DraftLineItem(
                line_number=1, customer_description="18in stretch film heavy duty",
                extracted_quantity=10, extracted_unit_price_cents=2500,
                extracted_line_total_cents=25000, matched_sku="SKU-WRAP-18",
                sku_confidence="High", sku_resolution_source="AI_HIGH_CONFIDENCE", status="Active",
            ),
            DraftLineItem(
                line_number=2, customer_description="Standard Pallet Wrap 15in 65ga",
                extracted_quantity=5, extracted_unit_price_cents=2000,
                extracted_line_total_cents=10000, matched_sku="SKU-WRAP-15",
                sku_confidence="High", sku_resolution_source="AI_HIGH_CONFIDENCE", status="Active",
            ),
        ],
    )
    db_session.add(draft)
    db_session.commit()
    return draft


def test_clean_acme_uses_contract_prices_integer_totals_and_subtotal(db_session, clean_draft):
    # Ignore stale derived fields and misleading catalog base prices.
    for line in clean_draft.line_items:
        line.contract_price_cents = 1
        line.calculated_line_total_cents = 1
    clean_draft.calculated_subtotal_cents = 2

    result = evaluate_clean_draft(db_session, clean_draft)

    assert result is clean_draft
    assert result.status == "Ready for Approval"
    assert [(line.contract_price_cents, line.calculated_line_total_cents) for line in result.line_items] == [
        (2500, 25000), (2000, 10000),
    ]
    assert result.calculated_subtotal_cents == 35000
    assert type(result.calculated_subtotal_cents) is int
    assert db_session.scalars(select(DiscrepancyFlag)).all() == []


@pytest.mark.parametrize("field,value", [
    ("customer_id", None), ("customer_id", "UNKNOWN-CUSTOMER"),
    ("customer_name_extracted", None), ("customer_name_extracted", "   "),
    ("po_number_extracted", None), ("po_number_extracted", ""),
])
def test_missing_headers_or_unknown_customer_never_become_ready(db_session, clean_draft, field, value):
    setattr(clean_draft, field, value)
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert db_session.scalars(select(DiscrepancyFlag)).all() == []


@pytest.mark.parametrize("confidence", ["Ambiguous", "Unrecognized"])
def test_unresolved_line_never_becomes_ready(db_session, clean_draft, confidence):
    line = clean_draft.line_items[0]
    line.sku_confidence = confidence
    line.sku_resolution_source = "NONE"
    line.matched_sku = None
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert line.contract_price_cents is None
    assert line.calculated_line_total_cents == 0


def test_unknown_catalog_sku_is_not_ready_and_evaluator_does_not_flush(db_session, clean_draft):
    clean_draft.line_items[0].matched_sku = "SKU-NOT-IN-CATALOG"
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert clean_draft.line_items[0].contract_price_cents is None


@pytest.mark.parametrize("field,value", [
    ("extracted_quantity", None), ("extracted_unit_price_cents", None),
    ("extracted_line_total_cents", None), ("customer_description", "   "),
    ("sku_resolution_source", "NONE"), ("sku_confidence", None),
])
def test_incomplete_active_line_cannot_pass_empty_discrepancy_gate(db_session, clean_draft, field, value):
    setattr(clean_draft.line_items[0], field, value)
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert db_session.scalars(select(DiscrepancyFlag)).all() == []


def test_no_eligible_tier_has_no_base_or_lowest_tier_fallback(db_session, clean_draft):
    db_session.execute(delete(ContractPriceTier).where(ContractPriceTier.sku == "SKU-WRAP-18"))
    db_session.add(ContractPriceTier(
        contract_id="CONTRACT-ACME-2026", sku="SKU-WRAP-18", min_quantity=50, tier_price_cents=2300,
    ))
    db_session.flush()
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert clean_draft.line_items[0].contract_price_cents is None
    assert clean_draft.line_items[0].calculated_line_total_cents == 0


def test_po_unit_price_mismatch_blocks_readiness_without_p2_flags(db_session, clean_draft):
    clean_draft.line_items[0].extracted_unit_price_cents = 2499
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert clean_draft.line_items[0].contract_price_cents == 2500
    assert clean_draft.line_items[0].calculated_line_total_cents == 25000
    assert db_session.scalars(select(DiscrepancyFlag)).all() == []


def test_multiple_eligible_contracts_fail_closed_without_selecting_one(db_session, clean_draft):
    db_session.add(CustomerContract(
        id="overlap", customer_id="CUST-ACME", customer_name="Acme Industrial Supplies",
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
    ))
    db_session.flush()
    db_session.add(ContractPriceTier(
        contract_id="overlap", sku="SKU-WRAP-18", min_quantity=1, tier_price_cents=2500,
    ))
    db_session.flush()
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert clean_draft.line_items[0].contract_price_cents is None
    assert clean_draft.line_items[0].calculated_line_total_cents == 0
    assert db_session.scalars(select(DiscrepancyFlag)).all() == []


def test_no_active_lines_is_not_a_clean_order(db_session, clean_draft):
    for line in clean_draft.line_items:
        line.status = "Removed"
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert clean_draft.calculated_subtotal_cents == 0


@pytest.mark.parametrize("status", ["Approved", "Rejected"])
def test_evaluation_cannot_mutate_terminal_draft(db_session, clean_draft, status):
    clean_draft.status = status
    before = clean_draft.calculated_subtotal_cents
    with pytest.raises(ValueError, match="terminal"):
        evaluate_clean_draft(db_session, clean_draft)
    assert clean_draft.status == status
    assert clean_draft.calculated_subtotal_cents == before


def test_existing_unresolved_flag_remains_a_readiness_blocker(db_session, clean_draft):
    flag = DiscrepancyFlag(
        draft=clean_draft, discrepancy_type="PriceMismatch", severity="Blocking",
        expected_value="25.00", requested_value="24.99", explanation="Existing blocker",
        resolution_state="Unresolved",
    )
    db_session.add(flag)
    db_session.flush()
    assert evaluate_clean_draft(db_session, clean_draft).status == "Needs Review"
    assert db_session.scalars(select(DiscrepancyFlag)).all() == [flag]
