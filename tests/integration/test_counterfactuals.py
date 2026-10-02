"""Integration tests for counterfactual operator actions: discrepancy_apex behavior,
positive controls, isolation guarantees, and state non-modification invariant.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.database import Base
from app.models.entities import (
    AuditEvent,
    CatalogProduct,
    ContractPriceTier,
    CustomerContract,
    DiscrepancyFlag,
    DraftLineItem,
    FieldProvenance,
    OrderDraft,
    PurchaseOrderDocument,
    VerifiedOrderRecord,
)
from app.models.schemas import decimal_to_cents
from app.services.ai_provider import FixtureAIProvider
from app.services.document_parser import parse_document
from app.services.order_service import ingest_order
from app.symbolic.counterfactuals import (
    PlanEvaluationResult,
    RemoveLineAction,
    RequestCorrectedPOAction,
    SelectSKUAction,
    evaluate_action_plan,
    search_counterfactual_plans,
)


@pytest.fixture
def seeded_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_baseline(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _ingest_fixture_draft(
    db: Session,
    fixture_id: str,
    text_content: str,
    filename: str,
) -> OrderDraft:
    doc = parse_document(
        text_content.encode("utf-8"),
        filename=filename,
        content_type="text/plain",
    )
    draft = ingest_order(
        db,
        document=doc,
        provider=FixtureAIProvider(fixture_id=fixture_id),
    )
    db.commit()
    return draft


def _capture_db_snapshot(db: Session, draft_id: str) -> dict[str, Any]:
    """Capture comprehensive snapshot of all draft-related and reference data."""
    with db.no_autoflush:
        draft = db.get(OrderDraft, draft_id)
        assert draft is not None

        return {
            "draft_status": draft.status,
            "calculated_subtotal_cents": draft.calculated_subtotal_cents,
            "extracted_order_total_cents": draft.extracted_order_total_cents,
            "lines": [
                {
                    "id": l.id,
                    "line_number": l.line_number,
                    "status": l.status,
                    "matched_sku": l.matched_sku,
                    "sku_confidence": l.sku_confidence,
                    "sku_resolution_source": l.sku_resolution_source,
                    "extracted_quantity": l.extracted_quantity,
                    "extracted_unit_price_cents": l.extracted_unit_price_cents,
                    "extracted_line_total_cents": l.extracted_line_total_cents,
                    "contract_price_cents": l.contract_price_cents,
                    "calculated_line_total_cents": l.calculated_line_total_cents,
                }
                for l in sorted(draft.line_items, key=lambda x: x.line_number)
            ],
            "flags": [
                {
                    "id": f.id,
                    "line_item_id": f.line_item_id,
                    "discrepancy_type": f.discrepancy_type,
                    "severity": f.severity,
                    "expected_value": f.expected_value,
                    "requested_value": f.requested_value,
                    "resolution_state": f.resolution_state,
                }
                for f in sorted(draft.discrepancy_flags, key=lambda x: (x.line_item_id or "", x.discrepancy_type))
            ],
            "audit_count": len(draft.audit_events),
            "verified_order_exists": draft.verified_order is not None,
            "catalog_products_count": len(db.scalars(select(CatalogProduct)).all()),
            "customer_contracts_count": len(db.scalars(select(CustomerContract)).all()),
            "contract_tiers_count": len(db.scalars(select(ContractPriceTier)).all()),
        }


# =============================================================================
# 1. Mandatory Tests on discrepancy_apex Fixture
# =============================================================================

def test_discrepancy_apex_initial_state(seeded_db: Session, po_discrepancy_apex_text: str):
    """discrepancy_apex starts in Needs Review with PriceMismatch on Line 1 and ambiguous SKU on Line 2."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-discrepancy-apex",
        po_discrepancy_apex_text,
        "po_discrepancy_apex.txt",
    )

    assert draft.status == "Needs Review"
    lines = sorted(draft.line_items, key=lambda l: l.line_number)
    assert len(lines) == 2

    # Line 1 has PriceMismatch
    line1 = lines[0]
    line1_flags = [f for f in line1.discrepancy_flags if f.resolution_state == "Unresolved"]
    assert any(f.discrepancy_type == "PriceMismatch" for f in line1_flags)

    # Line 2 has CatalogMatchingMismatch (ambiguous SKU)
    line2 = lines[1]
    line2_flags = [f for f in line2.discrepancy_flags if f.resolution_state == "Unresolved"]
    assert any(f.discrepancy_type == "CatalogMatchingMismatch" for f in line2_flags)


def test_discrepancy_apex_select_sku_line2_retains_blockers(
    seeded_db: Session, po_discrepancy_apex_text: str
):
    """Selecting SKU-WRAP-15 for Line 2 resolves ambiguity but reveals MOQ breach; status remains Needs Review."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-discrepancy-apex",
        po_discrepancy_apex_text,
        "po_discrepancy_apex.txt",
    )
    lines = sorted(draft.line_items, key=lambda l: l.line_number)
    line2_id = lines[1].id

    plan = [SelectSKUAction(line_id=line2_id, sku="SKU-WRAP-15")]
    res = evaluate_action_plan(seeded_db, draft.id, plan)

    assert res.outcome == "blocked"
    assert res.simulated_status == "Needs Review"
    assert not res.is_successful

    # Line 1 PriceMismatch must still be present
    blocker_types = {b.category for b in res.remaining_blockers}
    assert "PriceMismatch" in blocker_types

    # Line 2 MOQ breach (qty 2 < MOQ 5) must be present
    assert "QuantityOrPackagingBreach" in blocker_types
    moq_blocker = next(b for b in res.remaining_blockers if b.category == "QuantityOrPackagingBreach")
    assert "below MOQ 5" in moq_blocker.explanation


def test_discrepancy_apex_remove_line1_leaves_line2_ambiguous(
    seeded_db: Session, po_discrepancy_apex_text: str
):
    """Removing Line 1 resolves Line 1 blockers, but Line 2 remains ambiguous; status remains Needs Review."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-discrepancy-apex",
        po_discrepancy_apex_text,
        "po_discrepancy_apex.txt",
    )
    lines = sorted(draft.line_items, key=lambda l: l.line_number)
    line1_id = lines[0].id

    plan = [RemoveLineAction(line_id=line1_id)]
    res = evaluate_action_plan(seeded_db, draft.id, plan)

    assert res.outcome == "blocked"
    assert res.simulated_status == "Needs Review"
    assert not res.is_successful

    # Line 1 is removed
    assert len(res.removed_lines) == 1
    assert res.removed_lines[0].line_id == line1_id

    # Line 2 CatalogMatchingMismatch remains unresolved
    blocker_types = {b.category for b in res.remaining_blockers}
    assert "CatalogMatchingMismatch" in blocker_types
    assert "PriceMismatch" not in blocker_types


def test_discrepancy_apex_remove_line1_and_select_sku_line2_retains_moq_breach(
    seeded_db: Session, po_discrepancy_apex_text: str
):
    """Combination of RemoveLine(Line 1) and SelectSKU(Line 2) cannot hide MOQ breach; status remains Needs Review."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-discrepancy-apex",
        po_discrepancy_apex_text,
        "po_discrepancy_apex.txt",
    )
    lines = sorted(draft.line_items, key=lambda l: l.line_number)
    line1_id = lines[0].id
    line2_id = lines[1].id

    plan = [
        RemoveLineAction(line_id=line1_id),
        SelectSKUAction(line_id=line2_id, sku="SKU-WRAP-15"),
    ]
    res = evaluate_action_plan(seeded_db, draft.id, plan)

    assert res.outcome == "blocked"
    assert res.simulated_status == "Needs Review"
    assert not res.is_successful

    # QuantityOrPackagingBreach remains
    blocker_types = {b.category for b in res.remaining_blockers}
    assert blocker_types == {"QuantityOrPackagingBreach"}
    assert "below MOQ 5" in res.remaining_blockers[0].explanation


def test_discrepancy_apex_remove_all_lines_does_not_reach_ready(
    seeded_db: Session, po_discrepancy_apex_text: str
):
    """Removing all lines leaves zero active lines; status remains Needs Review."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-discrepancy-apex",
        po_discrepancy_apex_text,
        "po_discrepancy_apex.txt",
    )
    lines = sorted(draft.line_items, key=lambda l: l.line_number)

    plan = [
        RemoveLineAction(line_id=lines[0].id),
        RemoveLineAction(line_id=lines[1].id),
    ]
    res = evaluate_action_plan(seeded_db, draft.id, plan)

    assert res.outcome == "blocked"
    assert res.simulated_status == "Needs Review"
    assert not res.is_successful
    assert len(res.active_lines) == 0
    assert len(res.removed_lines) == 2
    assert "No active line items remain in draft" in res.incompleteness_reasons


def test_discrepancy_apex_request_corrected_po(
    seeded_db: Session, po_discrepancy_apex_text: str
):
    """RequestCorrectedPO yields requires_external_input without fabricating commercial values."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-discrepancy-apex",
        po_discrepancy_apex_text,
        "po_discrepancy_apex.txt",
    )

    plan = [RequestCorrectedPOAction(reason="Pricing and MOQ mismatch")]
    res = evaluate_action_plan(seeded_db, draft.id, plan)

    assert res.outcome == "requires_external_input"
    assert res.simulated_status is None
    assert not res.is_successful
    assert "Customer intervention is required" in res.explanation
    assert len(res.remaining_blockers) > 0


def test_discrepancy_apex_search_produces_empty_successful_plans(
    seeded_db: Session, po_discrepancy_apex_text: str
):
    """In the strict action space without commercial overrides, discrepancy_apex has no successful plans."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-discrepancy-apex",
        po_discrepancy_apex_text,
        "po_discrepancy_apex.txt",
    )

    analysis = search_counterfactual_plans(seeded_db, draft.id, max_depth=2, max_scenarios=50)

    assert analysis.is_already_ready is False
    assert analysis.successful_plans == ()
    assert len(analysis.all_evaluated_plans) > 0
    assert analysis.coverage.total_scenarios_evaluated > 0
    assert "0 successful plan(s) found" in analysis.summary


# =============================================================================
# 2. Positive Controls (Valid Paths to 'Ready for Approval')
# =============================================================================

def test_positive_control_ambiguous_apex_select_sku_achieves_ready(
    seeded_db: Session, po_ambiguous_apex_text: str
):
    """Positive Control A: In ambiguous_apex, SelectSKU(SKU-WRAP-15) resolves draft to Ready for Approval."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-ambiguous-apex",
        po_ambiguous_apex_text,
        "po_ambiguous_apex.txt",
    )

    assert draft.status == "Needs Review"
    line_id = draft.line_items[0].id

    # Evaluate explicit plan
    res = evaluate_action_plan(
        seeded_db,
        draft.id,
        [SelectSKUAction(line_id=line_id, sku="SKU-WRAP-15")],
    )

    assert res.outcome == "ready"
    assert res.simulated_status == "Ready for Approval"
    assert res.is_successful is True
    assert len(res.remaining_blockers) == 0
    assert len(res.incompleteness_reasons) == 0

    # Search must find this successful plan at depth 1
    analysis = search_counterfactual_plans(seeded_db, draft.id, max_depth=2)
    assert len(analysis.successful_plans) == 1
    found_plan = analysis.successful_plans[0]
    assert found_plan.is_successful is True
    assert len(found_plan.actions) == 1
    assert found_plan.actions[0] == SelectSKUAction(line_id=line_id, sku="SKU-WRAP-15")


def test_positive_control_remove_line_achieves_ready(seeded_db: Session):
    """Positive Control B: An order with one clean line and one flawed line reaches Ready when flawed line is removed."""
    # Create draft with Customer CUST-APEX
    doc = PurchaseOrderDocument(
        id="doc-pos-b",
        filename="po_pos_b.txt",
        content_type="text/plain",
        raw_text="Apex Distribution\nPO-POS-B\nClean line\nFlawed line\n",
        status="Ingested",
    )
    seeded_db.add(doc)
    seeded_db.commit()

    draft = OrderDraft(
        id="draft-pos-b",
        document_id=doc.id,
        customer_id="CUST-APEX",
        customer_name_extracted="Apex Distribution",
        po_number_extracted="PO-POS-B",
        status="Needs Review",
    )
    seeded_db.add(draft)

    # Line 1: Clean line (SKU-WRAP-15, Qty 10 >= MOQ 5, $20.00 exact tier price)
    l1 = DraftLineItem(
        id="line-clean-1",
        draft_id=draft.id,
        line_number=1,
        customer_description="Clean standard pallet wrap",
        extracted_quantity=10,
        extracted_unit_price_cents=2000,
        extracted_line_total_cents=20000,
        matched_sku="SKU-WRAP-15",
        sku_confidence="High",
        sku_resolution_source="OPERATOR_SELECTED",
        status="Active",
    )
    # Line 2: Flawed line (SKU-WRAP-18, Qty 10, Stated $18.00 vs Contract Tier $19.00 -> PriceMismatch)
    l2 = DraftLineItem(
        id="line-flawed-2",
        draft_id=draft.id,
        line_number=2,
        customer_description="Flawed industrial wrap with bad price",
        extracted_quantity=10,
        extracted_unit_price_cents=1800,
        extracted_line_total_cents=18000,
        matched_sku="SKU-WRAP-18",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    seeded_db.add_all([l1, l2])
    seeded_db.commit()

    # Plan: Remove line 2
    res = evaluate_action_plan(
        seeded_db,
        draft.id,
        [RemoveLineAction(line_id=l2.id)],
    )

    assert res.outcome == "ready"
    assert res.simulated_status == "Ready for Approval"
    assert res.is_successful is True
    assert len(res.active_lines) == 1
    assert res.active_lines[0].line_id == l1.id
    assert len(res.removed_lines) == 1
    assert res.removed_lines[0].line_id == l2.id
    assert len(res.remaining_blockers) == 0

    # Search finds RemoveLine on line 2 as a successful plan
    analysis = search_counterfactual_plans(seeded_db, draft.id, max_depth=2)
    assert any(
        p.is_successful and len(p.actions) == 1 and p.actions[0] == RemoveLineAction(line_id=l2.id)
        for p in analysis.successful_plans
    )


# =============================================================================
# 3. Isolation & State Invariance Tests
# =============================================================================

def test_source_state_deep_invariance_before_and_after_search(
    seeded_db: Session, po_discrepancy_apex_text: str
):
    """Running counterfactual evaluation guarantees zero side effects on the source database."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-discrepancy-apex",
        po_discrepancy_apex_text,
        "po_discrepancy_apex.txt",
    )

    before_snap = _capture_db_snapshot(seeded_db, draft.id)

    # Execute both single-plan evaluation and multi-scenario search
    lines = sorted(draft.line_items, key=lambda l: l.line_number)
    evaluate_action_plan(
        seeded_db,
        draft.id,
        [
            RemoveLineAction(line_id=lines[0].id),
            SelectSKUAction(line_id=lines[1].id, sku="SKU-WRAP-15"),
        ],
    )
    search_counterfactual_plans(seeded_db, draft.id, max_depth=2, max_scenarios=50)

    after_snap = _capture_db_snapshot(seeded_db, draft.id)

    assert before_snap == after_snap


def test_caller_session_unrelated_pending_writes_preserved(
    seeded_db: Session, po_ambiguous_apex_text: str
):
    """Counterfactual evaluator never flushes, commits, or rolls back caller session pending writes."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-ambiguous-apex",
        po_ambiguous_apex_text,
        "po_ambiguous_apex.txt",
    )
    draft_id = draft.id

    # Add an uncommitted pending object to caller session
    pending_doc = PurchaseOrderDocument(
        id="doc-unrelated-pending",
        filename="unrelated.txt",
        content_type="text/plain",
        raw_text="Pending doc text",
        status="Ingested",
    )
    seeded_db.add(pending_doc)

    assert pending_doc in seeded_db.new
    assert seeded_db.is_modified(pending_doc) or pending_doc in seeded_db.new

    # Run search
    search_counterfactual_plans(seeded_db, draft_id, max_depth=2)

    # Pending write is still present in caller session
    assert pending_doc in seeded_db.new
    seeded_db.rollback()


def test_exception_in_plan_leaves_source_session_unaltered(
    seeded_db: Session, po_ambiguous_apex_text: str
):
    """An invalid action or runtime exception during evaluation does not pollute the source session."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-ambiguous-apex",
        po_ambiguous_apex_text,
        "po_ambiguous_apex.txt",
    )
    before_snap = _capture_db_snapshot(seeded_db, draft.id)

    # Plan with foreign line and unknown SKU
    res = evaluate_action_plan(
        seeded_db,
        draft.id,
        [SelectSKUAction(line_id="non-existent-line", sku="NON-EXISTENT-SKU")],
    )
    assert res.outcome == "invalid"

    after_snap = _capture_db_snapshot(seeded_db, draft.id)
    assert before_snap == after_snap


def test_order_independence_and_determinism(
    seeded_db: Session, po_ambiguous_apex_text: str
):
    """Independent scenario evaluation produces identical results regardless of invocation order."""
    draft = _ingest_fixture_draft(
        seeded_db,
        "fixture-ambiguous-apex",
        po_ambiguous_apex_text,
        "po_ambiguous_apex.txt",
    )
    line_id = draft.line_items[0].id

    plan_a = [SelectSKUAction(line_id=line_id, sku="SKU-WRAP-15")]
    plan_b = [SelectSKUAction(line_id=line_id, sku="SKU-WRAP-18")]

    # Run A then B
    res_a1 = evaluate_action_plan(seeded_db, draft.id, plan_a)
    res_b1 = evaluate_action_plan(seeded_db, draft.id, plan_b)

    # Run B then A
    res_b2 = evaluate_action_plan(seeded_db, draft.id, plan_b)
    res_a2 = evaluate_action_plan(seeded_db, draft.id, plan_a)

    assert res_a1.outcome == res_a2.outcome
    assert res_a1.simulated_status == res_a2.simulated_status
    assert res_b1.outcome == res_b2.outcome
    assert res_b1.simulated_status == res_b2.simulated_status


def test_order_arithmetic_mismatch_preserved_on_line_removal(seeded_db: Session):
    """When an order has extracted_order_total_cents, removing a line reveals order ArithmeticMismatch."""
    doc = PurchaseOrderDocument(
        id="doc-arith-test",
        filename="po_arith.txt",
        content_type="text/plain",
        raw_text="Acme Industrial Corp\nPO-ARITH\nLine 1\nLine 2\n",
        status="Ingested",
    )
    seeded_db.add(doc)

    draft = OrderDraft(
        id="draft-arith-test",
        document_id=doc.id,
        customer_id="CUST-ACME",
        customer_name_extracted="Acme Industrial Corp",
        po_number_extracted="PO-ARITH",
        extracted_order_total_cents=108000,  # $1,080.00 = sum of both lines (54000 + 54000)
        status="Needs Review",
    )
    seeded_db.add(draft)

    l1 = DraftLineItem(
        id="line-arith-1",
        draft_id=draft.id,
        line_number=1,
        customer_description="Packaging tape standard",
        extracted_quantity=36,
        extracted_unit_price_cents=1500,
        extracted_line_total_cents=54000,
        matched_sku="SKU-TAPE-02",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    l2 = DraftLineItem(
        id="line-arith-2",
        draft_id=draft.id,
        line_number=2,
        customer_description="Packaging tape standard second pack",
        extracted_quantity=36,
        extracted_unit_price_cents=1500,
        extracted_line_total_cents=54000,
        matched_sku="SKU-TAPE-02",
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        status="Active",
    )
    seeded_db.add_all([l1, l2])
    seeded_db.commit()

    # Removing line 2 leaves only line 1 ($540.00), which conflicts with extracted_order_total_cents ($1,080.00)
    res = evaluate_action_plan(
        seeded_db,
        draft.id,
        [RemoveLineAction(line_id=l2.id)],
    )

    assert res.outcome == "blocked"
    assert res.simulated_status == "Needs Review"
    order_blocker = next((b for b in res.remaining_blockers if b.scope == "order"), None)
    assert order_blocker is not None
    assert order_blocker.category == "ArithmeticMismatch"
    assert "Customer-stated order total differs" in order_blocker.explanation


def test_uses_caller_custom_contract_context_not_hardcoded_baseline(seeded_db: Session):
    """Evaluation adheres to the specific caller database contract tiers, not hardcoded baseline."""
    # Create custom customer CUST-CUSTOM with non-standard tier price $25.00 for SKU-WRAP-15
    from datetime import date
    custom_contract = CustomerContract(
        id="contract-custom-01",
        customer_id="CUST-CUSTOM",
        customer_name="Custom Logistics",
        valid_from=date(2025, 1, 1),
        valid_to=date(2027, 12, 31),
    )
    seeded_db.add(custom_contract)
    seeded_db.commit()

    custom_tier = ContractPriceTier(
        id="tier-custom-01",
        contract_id=custom_contract.id,
        sku="SKU-WRAP-15",
        min_quantity=5,
        tier_price_cents=2500,  # $25.00 instead of baseline $20.00
    )
    seeded_db.add(custom_tier)
    seeded_db.commit()

    doc = PurchaseOrderDocument(
        id="doc-custom-test",
        filename="custom.txt",
        content_type="text/plain",
        raw_text="Custom Logistics\nPO-CUSTOM\nWrap\n",
        status="Ingested",
    )
    seeded_db.add(doc)

    draft = OrderDraft(
        id="draft-custom-test",
        document_id=doc.id,
        customer_id="CUST-CUSTOM",
        customer_name_extracted="Custom Logistics",
        po_number_extracted="PO-CUSTOM",
        status="Needs Review",
    )
    seeded_db.add(draft)

    # Line has extracted_unit_price $25.00 (matching custom tier, but breaching baseline $20.00 tier)
    line = DraftLineItem(
        id="line-custom-1",
        draft_id=draft.id,
        line_number=1,
        customer_description="Wrap",
        extracted_quantity=10,
        extracted_unit_price_cents=2500,  # $25.00
        extracted_line_total_cents=25000,
        matched_sku=None,
        sku_confidence="Ambiguous",
        candidate_skus_json='["SKU-WRAP-15"]',
        status="Active",
    )
    seeded_db.add(line)
    seeded_db.commit()

    res = evaluate_action_plan(
        seeded_db,
        draft.id,
        [SelectSKUAction(line_id=line.id, sku="SKU-WRAP-15")],
    )

    # Must succeed using the custom tier price $25.00!
    assert res.outcome == "ready"
    assert res.simulated_status == "Ready for Approval"
    assert res.is_successful is True
    assert res.active_lines[0].contract_price_cents == 2500
