"""Unit tests for app.symbolic.counterfactuals: action validation, allowlist enforcement,
isolation semantics, and bounded search constraints.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.database import Base
from app.models.entities import (
    CatalogProduct,
    DraftLineItem,
    OrderDraft,
    PurchaseOrderDocument,
)
from app.symbolic.counterfactuals import (
    ALLOWED_ACTION_TYPES,
    FORBIDDEN_COMMERCIAL_FIELDS,
    LineItemSummary,
    PlanEvaluationResult,
    RemainingBlocker,
    RemoveLineAction,
    RequestCorrectedPOAction,
    SearchCoverageInfo,
    SelectSKUAction,
    evaluate_action_plan,
    parse_and_validate_action,
    search_counterfactual_plans,
)


# -----------------------------------------------------------------------------
# 1. Action Allowlist & Parsing Tests
# -----------------------------------------------------------------------------

def test_parse_valid_select_sku_action_dataclass():
    act = SelectSKUAction(line_id="line-1", sku="SKU-WRAP-15")
    parsed = parse_and_validate_action(act)
    assert parsed == act
    assert parsed.line_id == "line-1"
    assert parsed.sku == "SKU-WRAP-15"
    assert parsed.action_type == "SelectSKU"


def test_parse_valid_select_sku_action_dict():
    payload = {"action": "SelectSKU", "line_id": "line-1", "sku": "SKU-WRAP-15"}
    parsed = parse_and_validate_action(payload)
    assert isinstance(parsed, SelectSKUAction)
    assert parsed.line_id == "line-1"
    assert parsed.sku == "SKU-WRAP-15"

    # Also support matched_sku alias and action_type key
    payload2 = {"action_type": "SelectSKU", "line_id": "line-2", "matched_sku": "SKU-TAPE-01"}
    parsed2 = parse_and_validate_action(payload2)
    assert parsed2.line_id == "line-2"
    assert parsed2.sku == "SKU-TAPE-01"


def test_parse_valid_remove_line_action():
    act = RemoveLineAction(line_id="line-1")
    parsed = parse_and_validate_action(act)
    assert parsed == act

    payload = {"action": "RemoveLine", "line_id": "line-99"}
    parsed_dict = parse_and_validate_action(payload)
    assert isinstance(parsed_dict, RemoveLineAction)
    assert parsed_dict.line_id == "line-99"


def test_parse_valid_request_corrected_po_action():
    act = RequestCorrectedPOAction(reason="Pricing discrepancy on line 1")
    parsed = parse_and_validate_action(act)
    assert parsed == act

    payload = {"action": "RequestCorrectedPO", "reason": "MOQ mismatch"}
    parsed_dict = parse_and_validate_action(payload)
    assert isinstance(parsed_dict, RequestCorrectedPOAction)
    assert parsed_dict.reason == "MOQ mismatch"


# -----------------------------------------------------------------------------
# 2. Strict Rejection of Prohibited Actions & Commercial Overrides
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("disallowed_action", [
    "CorrectField",
    "ApproveOrder",
    "RejectDraft",
    "OverridePrice",
    "SetQuantity",
    "ArbitraryMutation",
    "",
])
def test_reject_unauthorized_action_types(disallowed_action):
    with pytest.raises(ValueError):
        parse_and_validate_action({"action": disallowed_action, "line_id": "line-1"})


@pytest.mark.parametrize("forbidden_field", sorted(FORBIDDEN_COMMERCIAL_FIELDS))
def test_reject_commercial_overrides_in_action_payload(forbidden_field):
    payload = {
        "action": "SelectSKU",
        "line_id": "line-1",
        "sku": "SKU-WRAP-15",
        forbidden_field: 100,
    }
    with pytest.raises(ValueError, match="Commercial overrides are forbidden"):
        parse_and_validate_action(payload)


def test_reject_unexpected_extra_fields():
    payload = {
        "action": "SelectSKU",
        "line_id": "line-1",
        "sku": "SKU-WRAP-15",
        "unknown_extra_param": "sneaky_value",
    }
    with pytest.raises(ValueError, match="Unexpected extra field"):
        parse_and_validate_action(payload)


@pytest.mark.parametrize("invalid_payload", [
    None,
    123,
    "SelectSKU",
    ["SelectSKU", "line-1"],
    {"action": "SelectSKU", "line_id": "", "sku": "SKU-1"},
    {"action": "SelectSKU", "line_id": "line-1", "sku": ""},
    {"action": "RemoveLine", "line_id": ""},
    {"action": "RequestCorrectedPO", "reason": 123},
])
def test_reject_malformed_action_payloads(invalid_payload):
    with pytest.raises((ValueError, TypeError)):
        parse_and_validate_action(invalid_payload)


# -----------------------------------------------------------------------------
# 3. Action Evaluation Semantics in SQLite Engine
# -----------------------------------------------------------------------------

@pytest.fixture
def test_db_session():
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


def _create_minimal_draft(
    db: Session,
    *,
    customer_id: str = "CUST-ACME",
    customer_name: str = "Acme Industrial Corp",
    po_number: str = "PO-UNIT-01",
    status: str = "Needs Review",
    lines_data: list[dict] | None = None,
) -> OrderDraft:
    doc = PurchaseOrderDocument(
        id=f"doc-{po_number}",
        filename=f"{po_number}.txt",
        content_type="text/plain",
        raw_text=f"{customer_name}\n{po_number}\nItem 1\n",
        status="Ingested",
    )
    db.add(doc)
    db.commit()

    draft = OrderDraft(
        id=f"draft-{po_number}",
        document_id=doc.id,
        customer_id=customer_id,
        customer_name_extracted=customer_name,
        po_number_extracted=po_number,
        status=status,
    )
    db.add(draft)
    db.commit()

    if lines_data is None:
        lines_data = [
            {
                "line_number": 1,
                "customer_description": "Packaging tape standard",
                "extracted_quantity": 36,
                "extracted_unit_price_cents": 1500,
                "extracted_line_total_cents": 54000,
                "matched_sku": "SKU-TAPE-02",
                "sku_confidence": "High",
                "sku_resolution_source": "AI_HIGH_CONFIDENCE",
                "status": "Active",
            }
        ]

    for item in lines_data:
        line = DraftLineItem(
            id=item.get("id", f"line-{po_number}-{item['line_number']}"),
            draft_id=draft.id,
            line_number=item["line_number"],
            customer_description=item["customer_description"],
            extracted_quantity=item.get("extracted_quantity"),
            extracted_unit_price_cents=item.get("extracted_unit_price_cents"),
            extracted_line_total_cents=item.get("extracted_line_total_cents"),
            matched_sku=item.get("matched_sku"),
            sku_confidence=item.get("sku_confidence"),
            sku_resolution_source=item.get("sku_resolution_source", "NONE"),
            candidate_skus_json=item.get("candidate_skus_json"),
            status=item.get("status", "Active"),
        )
        db.add(line)
    db.commit()
    return draft



def test_evaluate_action_plan_with_invalid_action(test_db_session: Session):
    draft = _create_minimal_draft(test_db_session)
    res = evaluate_action_plan(
        test_db_session,
        draft.id,
        [{"action": "CorrectField", "field": "price", "value": 100}],
    )
    assert res.outcome == "invalid"
    assert not res.is_successful
    assert "Commercial overrides" in res.explanation or "not in allowed" in res.explanation


def test_evaluate_action_plan_with_request_corrected_po(test_db_session: Session):
    draft = _create_minimal_draft(test_db_session)
    res = evaluate_action_plan(
        test_db_session,
        draft.id,
        [RequestCorrectedPOAction(reason="Pricing dispute")],
    )
    assert res.outcome == "requires_external_input"
    assert not res.is_successful
    assert res.simulated_status is None
    assert "Customer intervention is required" in res.explanation
    assert "Without a newly ingested purchase order" in res.explanation


def test_evaluate_action_plan_with_foreign_line_item(test_db_session: Session):
    draft = _create_minimal_draft(test_db_session)
    res = evaluate_action_plan(
        test_db_session,
        draft.id,
        [SelectSKUAction(line_id="foreign-line-id-999", sku="SKU-WRAP-15")],
    )
    assert res.outcome == "invalid"
    assert not res.is_successful
    assert "does not belong to draft" in res.explanation or "not found" in res.explanation


def test_evaluate_action_plan_with_unknown_catalog_sku(test_db_session: Session):
    draft = _create_minimal_draft(test_db_session)
    line_id = draft.line_items[0].id
    res = evaluate_action_plan(
        test_db_session,
        draft.id,
        [SelectSKUAction(line_id=line_id, sku="NON-EXISTENT-SKU-99999")],
    )
    assert res.outcome == "invalid"
    assert not res.is_successful
    assert "does not exist in catalog" in res.explanation


def test_evaluate_action_plan_on_terminal_draft(test_db_session: Session):
    draft = _create_minimal_draft(test_db_session, status="Approved")
    line_id = draft.line_items[0].id
    res = evaluate_action_plan(
        test_db_session,
        draft.id,
        [RemoveLineAction(line_id=line_id)],
    )
    assert res.outcome == "invalid"
    assert not res.is_successful
    assert "cannot be modified" in res.explanation


def test_search_on_already_ready_draft(test_db_session: Session):
    draft = _create_minimal_draft(test_db_session, status="Ready for Approval")
    res = search_counterfactual_plans(test_db_session, draft.id)

    assert res.is_already_ready
    assert len(res.successful_plans) == 1
    assert res.successful_plans[0].actions == ()
    assert res.successful_plans[0].outcome == "ready"
    assert res.coverage.total_scenarios_evaluated == 0
    assert "already Ready for Approval" in res.summary


def test_search_bounded_limits_and_truncation(test_db_session: Session):
    # Draft with 3 lines, each having candidate SKUs
    lines = [
        {
            "line_number": 1,
            "customer_description": "Wrap 1",
            "extracted_quantity": 10,
            "extracted_unit_price_cents": 2000,
            "extracted_line_total_cents": 20000,
            "candidate_skus_json": '["SKU-WRAP-15", "SKU-WRAP-18"]',
        },
        {
            "line_number": 2,
            "customer_description": "Tape 1",
            "extracted_quantity": 10,
            "extracted_unit_price_cents": 1500,
            "extracted_line_total_cents": 15000,
            "candidate_skus_json": '["SKU-TAPE-02", "SKU-TAPE-03"]',
        },
        {
            "line_number": 3,
            "customer_description": "Box 1",
            "extracted_quantity": 50,
            "extracted_unit_price_cents": 250,
            "extracted_line_total_cents": 12500,
            "candidate_skus_json": '["SKU-BOX-MED", "SKU-BOX-LRG"]',
        },
    ]
    draft = _create_minimal_draft(test_db_session, po_number="PO-LIMITS-01", lines_data=lines)

    # Search with very tight max_scenarios=3
    res = search_counterfactual_plans(
        test_db_session,
        draft.id,
        max_depth=2,
        max_scenarios=3,
    )

    assert res.coverage.is_truncated is True
    assert res.coverage.total_scenarios_evaluated == 3
    assert len(res.all_evaluated_plans) == 3


def test_invalid_search_arguments_raise_value_error(test_db_session: Session):
    draft = _create_minimal_draft(test_db_session)
    with pytest.raises(ValueError, match="max_depth must be at least 1"):
        search_counterfactual_plans(test_db_session, draft.id, max_depth=0)

    with pytest.raises(ValueError, match="max_scenarios must be at least 1"):
        search_counterfactual_plans(test_db_session, draft.id, max_scenarios=0)
