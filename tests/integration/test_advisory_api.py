"""Comprehensive integration tests for GET /api/v1/drafts/{draft_id}/advisory endpoint.

Covers:
- All 3 canonical fixtures (clean_acme, ambiguous_apex, discrepancy_apex).
- Strict read-only proofs: DB snapshot equality, session listener spy, pending write preservation.
- Full scenario validations (7 mandatory discrepancy_apex assertions).
- Terminal drafts (Approved/Rejected) handling.
- Input validation (404, 422 on boundary params).
- Grounding and bi-directional trace coverage.
- Interleaving with real PATCH operator actions.
- Determinism, Cache-Control header, and performance benchmarking.
"""
from __future__ import annotations

import copy
import json
import time
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.serialization import serialize_draft
from app.cli import seed_baseline
from app.models.advisory import ADVISORY_SCHEMA_VERSION, AdvisoryStatus, DraftAdvisoryResponse
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
from app.symbolic.counterfactuals import (
    RemoveLineAction,
    RequestCorrectedPOAction,
    SelectSKUAction,
    evaluate_action_plan,
)


@pytest.fixture
def seeded_db(db_session):
    seed_baseline(db_session)
    db_session.commit()
    return db_session


def _take_db_snapshot(db: Session) -> dict[str, list[dict[str, Any]]]:
    tables = [
        PurchaseOrderDocument,
        OrderDraft,
        DraftLineItem,
        FieldProvenance,
        DiscrepancyFlag,
        AuditEvent,
        VerifiedOrderRecord,
        CatalogProduct,
        CustomerContract,
        ContractPriceTier,
    ]
    snapshot: dict[str, list[dict[str, Any]]] = {}
    with db.no_autoflush:
        for t in tables:
            rows = db.scalars(select(t)).all()
            serialized = []
            for r in rows:
                row_dict = {}
                for col in r.__table__.columns:
                    val = getattr(r, col.name)
                    if isinstance(val, (int, str, bool, float, type(None))):
                        row_dict[col.name] = val
                    else:
                        row_dict[col.name] = str(val)
                serialized.append(row_dict)
            serialized.sort(key=lambda x: str(x.get("id", x.get("sku", ""))))
            snapshot[t.__tablename__] = serialized
    return snapshot


# -----------------------------------------------------------------------------
# 1. Clean Acme PO Scenario
# -----------------------------------------------------------------------------

def test_advisory_clean_acme(client, seeded_db, no_external_network):
    # Ingest clean Acme fixture
    ingest_resp = client.post("/api/v1/fixtures/fixture-clean-acme/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    resp = client.get(f"/api/v1/drafts/{draft_id}/advisory")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"

    data = resp.json()
    validated = DraftAdvisoryResponse.model_validate(data)
    assert validated.schema_version == ADVISORY_SCHEMA_VERSION
    assert validated.advisory_status == AdvisoryStatus.READY_NO_ACTION_NEEDED

    # Baseline has 0 blockers and simulated status is Ready for Approval
    assert validated.baseline is not None
    assert validated.baseline.simulated_status == "Ready for Approval"
    assert validated.baseline.blockers == []
    assert validated.baseline.is_stale_evaluation is False

    # Counterfactuals: no actions needed
    assert validated.counterfactuals is not None
    assert validated.counterfactuals.successful_plans == []

    # Trace: no blocking steps
    assert validated.trace is not None
    blocking_steps = [s for s in validated.trace.steps if s.outcome == "blocking"]
    assert len(blocking_steps) == 0


# -----------------------------------------------------------------------------
# 2. Ambiguous Apex PO Scenario
# -----------------------------------------------------------------------------

def test_advisory_ambiguous_apex(client, seeded_db, no_external_network):
    ingest_resp = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    resp = client.get(f"/api/v1/drafts/{draft_id}/advisory")
    assert resp.status_code == 200
    data = resp.json()
    validated = DraftAdvisoryResponse.model_validate(data)

    assert validated.advisory_status == AdvisoryStatus.ACTIONABLE_PLANS_FOUND
    assert validated.counterfactuals is not None
    assert len(validated.counterfactuals.successful_plans) >= 1

    # Positive control: Selecting SKU-WRAP-15 succeeds
    best_plan = validated.counterfactuals.successful_plans[0]
    assert best_plan.is_successful is True
    assert best_plan.simulated_status == "Ready for Approval"
    assert any(a.action_type == "SelectSKU" and a.sku == "SKU-WRAP-15" for a in best_plan.actions)

    # Selecting SKU-WRAP-18 is blocked by PriceMismatch against APEX contract tier ($22.00 vs $20.00 requested)
    wrap18_plans = [
        p for p in validated.counterfactuals.blocked_plans
        if any(a.action_type == "SelectSKU" and a.sku == "SKU-WRAP-18" for a in p.actions)
    ]
    assert len(wrap18_plans) >= 1
    assert any("PriceMismatch" in b.category for b in wrap18_plans[0].remaining_blockers)


# -----------------------------------------------------------------------------
# 3. Discrepancy Apex PO Scenario (All 7 Mandatory Checks)
# -----------------------------------------------------------------------------

def test_advisory_discrepancy_apex_all_seven_assertions(client, seeded_db, no_external_network):
    ingest_resp = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    draft = seeded_db.get(OrderDraft, draft_id)
    line1 = next(l for l in draft.line_items if l.line_number == 1)
    line2 = next(l for l in draft.line_items if l.line_number == 2)
    line1_id = line1.id
    line2_id = line2.id

    resp = client.get(f"/api/v1/drafts/{draft_id}/advisory")
    assert resp.status_code == 200
    data = resp.json()
    validated = DraftAdvisoryResponse.model_validate(data)

    # Assertion 1: Discrepancy apex has 0 successful internal plans
    assert validated.counterfactuals is not None
    assert validated.counterfactuals.successful_plans == []
    assert validated.advisory_status == AdvisoryStatus.BLOCKED_NO_INTERNAL_PLAN

    # Line 1 PriceMismatch check: requested $18.00 vs contract tier $22.00 (TIER-APEX-WRAP18-Q10)
    p_blocker = next(b for b in validated.baseline.blockers if b.line_number == 1 and b.discrepancy_type == "PriceMismatch")
    assert p_blocker.price_source is not None
    assert p_blocker.price_source.tier_price == "22.00"
    assert p_blocker.price_source.contract_id == "CONTRACT-APEX-2026"

    # Assertion 2: SelectSKU(Line 2, SKU-WRAP-15) removes ambiguity but reveals MOQ breach (qty 2 < MOQ 5) and retains Line 1 PriceMismatch
    plan_sel2 = evaluate_action_plan(seeded_db, draft_id, [SelectSKUAction(line_id=line2_id, sku="SKU-WRAP-15")])
    assert plan_sel2.is_successful is False
    assert any(b.category == "QuantityOrPackagingBreach" and b.line_number == 2 for b in plan_sel2.remaining_blockers)
    assert any(b.category == "PriceMismatch" and b.line_number == 1 for b in plan_sel2.remaining_blockers)

    # Assertion 3: RemoveLine(Line 1) leaves Line 2 catalog ambiguous
    plan_rem1 = evaluate_action_plan(seeded_db, draft_id, [RemoveLineAction(line_id=line1_id)])
    assert plan_rem1.is_successful is False
    assert any(b.category == "CatalogMatchingMismatch" and b.line_number == 2 for b in plan_rem1.remaining_blockers)

    # Assertion 4: RemoveLine(Line 1) + SelectSKU(Line 2, SKU-WRAP-15) retains Line 2 MOQ breach
    plan_combo = evaluate_action_plan(
        seeded_db,
        draft_id,
        [RemoveLineAction(line_id=line1_id), SelectSKUAction(line_id=line2_id, sku="SKU-WRAP-15")],
    )
    assert plan_combo.is_successful is False
    assert any(b.category == "QuantityOrPackagingBreach" and b.line_number == 2 for b in plan_combo.remaining_blockers)

    # Assertion 5: Removing all lines fails (0 active lines left -> data incompleteness)
    plan_rem_all = evaluate_action_plan(
        seeded_db,
        draft_id,
        [RemoveLineAction(line_id=line1_id), RemoveLineAction(line_id=line2_id)],
    )
    assert plan_rem_all.is_successful is False
    assert any("No active line items remain in draft" in r for r in plan_rem_all.incompleteness_reasons)

    # Assertion 6: RequestCorrectedPO yields requires_external_input
    plan_req_po = evaluate_action_plan(seeded_db, draft_id, [RequestCorrectedPOAction(reason="Pricing & MOQ error")])
    assert plan_req_po.outcome == "requires_external_input"
    assert plan_req_po.is_successful is False

    # Assertion 7: requires_external_input in advisory response details required corrections
    assert validated.counterfactuals.requires_external_input is not None
    assert validated.counterfactuals.requires_external_input.is_required is True
    assert len(validated.counterfactuals.requires_external_input.reasons) >= 1


# -----------------------------------------------------------------------------
# 4. Strict Read-Only Proofs Across Entire TestClient Request
# -----------------------------------------------------------------------------

def test_strict_read_only_and_pending_writes_preservation(client, seeded_db, no_external_network):
    ingest_resp = client.post("/api/v1/fixtures/fixture-clean-acme/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    # 1. Uncommitted pending write in seeded_db
    pending_event = AuditEvent(
        draft_id=draft_id,
        event_type="TEST_PENDING_EVENT",
        actor="test-op",
        details_json="{}",
    )
    seeded_db.add(pending_event)
    assert pending_event in seeded_db.new

    # 2. Attach strict SQLAlchemy listeners
    mutation_events = []
    def fail_event(name):
        def handler(*args, **kwargs):
            mutation_events.append(name)
        return handler

    event.listen(seeded_db, "before_flush", fail_event("before_flush"))
    event.listen(seeded_db, "after_flush", fail_event("after_flush"))
    event.listen(seeded_db, "after_commit", fail_event("after_commit"))
    event.listen(seeded_db, "after_rollback", fail_event("after_rollback"))

    # 3. Take DB snapshot
    before_snapshot = _take_db_snapshot(seeded_db)
    draft_before_serialization = serialize_draft(seeded_db.get(OrderDraft, draft_id))

    # 4. Perform GET advisory request
    resp = client.get(f"/api/v1/drafts/{draft_id}/advisory")
    assert resp.status_code == 200

    # 5. Assert zero mutation events fired
    assert mutation_events == []

    # 6. Assert pending write is still pending in session.new
    assert pending_event in seeded_db.new

    # 7. Assert serialize_draft is byte-for-byte identical
    draft_after_serialization = serialize_draft(seeded_db.get(OrderDraft, draft_id))
    assert draft_before_serialization == draft_after_serialization

    # 8. Assert DB snapshot is identical
    after_snapshot = _take_db_snapshot(seeded_db)
    assert before_snapshot == after_snapshot

    seeded_db.expunge(pending_event)


# -----------------------------------------------------------------------------
# 5. Terminal Draft Handling (Approved & Rejected)
# -----------------------------------------------------------------------------

def test_terminal_drafts_return_200_not_applicable_terminal(client, seeded_db, no_external_network):
    # Test Approved draft
    ingest_resp = client.post("/api/v1/fixtures/fixture-clean-acme/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    approve_resp = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"operator_id": "op-01"})
    assert approve_resp.status_code == 200

    with patch("app.services.advisory.search_counterfactual_plans") as spy_cf:
        advisory_resp = client.get(f"/api/v1/drafts/{draft_id}/advisory")

    assert advisory_resp.status_code == 200
    assert spy_cf.call_count == 0
    data = advisory_resp.json()
    assert data["advisory_status"] == "not_applicable_terminal"
    assert data["status_persisted"] == "Approved"

    # Test Rejected draft
    ingest2 = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest")
    assert ingest2.status_code == 201
    draft2_id = ingest2.json()["draft_id"]

    reject_resp = client.post(
        f"/api/v1/drafts/{draft2_id}/reject",
        json={"operator_id": "op-02", "reason": "Commercial mismatch"},
    )
    assert reject_resp.status_code == 200

    with patch("app.services.advisory.search_counterfactual_plans") as spy_cf2:
        advisory_resp2 = client.get(f"/api/v1/drafts/{draft2_id}/advisory")

    assert advisory_resp2.status_code == 200
    assert spy_cf2.call_count == 0
    data2 = advisory_resp2.json()
    assert data2["advisory_status"] == "not_applicable_terminal"
    assert data2["status_persisted"] == "Rejected"


# -----------------------------------------------------------------------------
# 6. HTTP Status & Query Parameter Validation (404 & 422)
# -----------------------------------------------------------------------------

def test_error_status_codes_and_validation(client, seeded_db, no_external_network):
    # 404 on unknown draft
    r404 = client.get("/api/v1/drafts/non-existent-draft-id/advisory")
    assert r404.status_code == 404
    assert r404.json() == {"error": "DraftNotFoundError", "message": "Draft does not exist"}

    ingest_resp = client.post("/api/v1/fixtures/fixture-clean-acme/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    # 422 on invalid sections
    r_bad_sec = client.get(f"/api/v1/drafts/{draft_id}/advisory?sections=unknown_section")
    assert r_bad_sec.status_code == 422
    assert r_bad_sec.json()["error"] == "RequestValidationError"

    # 422 on empty sections
    r_empty_sec = client.get(f"/api/v1/drafts/{draft_id}/advisory?sections=")
    assert r_empty_sec.status_code == 422

    # 422 on max_depth out of bounds (1..3)
    assert client.get(f"/api/v1/drafts/{draft_id}/advisory?max_depth=0").status_code == 422
    assert client.get(f"/api/v1/drafts/{draft_id}/advisory?max_depth=4").status_code == 422
    assert client.get(f"/api/v1/drafts/{draft_id}/advisory?max_depth=invalid").status_code == 422

    # 422 on max_scenarios out of bounds (1..50)
    assert client.get(f"/api/v1/drafts/{draft_id}/advisory?max_scenarios=0").status_code == 422
    assert client.get(f"/api/v1/drafts/{draft_id}/advisory?max_scenarios=51").status_code == 422
    assert client.get(f"/api/v1/drafts/{draft_id}/advisory?max_scenarios=xyz").status_code == 422


# -----------------------------------------------------------------------------
# 7. Section Filtering & Truncation Limits
# -----------------------------------------------------------------------------

def test_section_filtering_and_limits(client, seeded_db, no_external_network):
    ingest_resp = client.post("/api/v1/fixtures/fixture-clean-acme/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    # Request only review_priority and sku_confidence
    resp = client.get(f"/api/v1/drafts/{draft_id}/advisory?sections=review_priority,sku_confidence")
    assert resp.status_code == 200
    data = resp.json()
    assert data["review_priority"] is not None
    assert data["sku_confidence"] is not None
    assert data["baseline"] is None
    assert data["counterfactuals"] is None
    assert data["trace"] is None
    assert data["included_sections"] == ["review_priority", "sku_confidence"]

    # Test truncation warning when max_scenarios=1
    resp_trunc = client.get(f"/api/v1/drafts/{draft_id}/advisory?sections=counterfactuals&max_scenarios=1")
    assert resp_trunc.status_code == 200
    cf_data = resp_trunc.json()["counterfactuals"]
    assert cf_data["limits"]["max_scenarios"] == 1


# -----------------------------------------------------------------------------
# 8. Determinism and Interleaving with PATCH Line
# -----------------------------------------------------------------------------

def test_determinism_and_interleaving_with_patch(client, seeded_db, no_external_network):
    ingest_resp = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    # 1. Three consecutive identical requests return byte-for-byte identical content
    r1 = client.get(f"/api/v1/drafts/{draft_id}/advisory").content
    r2 = client.get(f"/api/v1/drafts/{draft_id}/advisory").content
    r3 = client.get(f"/api/v1/drafts/{draft_id}/advisory").content
    assert r1 == r2 == r3

    # Initial state is actionable_plans_found (ambiguous)
    init_data = json.loads(r1)
    assert init_data["advisory_status"] == "actionable_plans_found"

    # 2. Interleave with a real PATCH operator action
    draft = seeded_db.get(OrderDraft, draft_id)
    line1 = next(l for l in draft.line_items if l.line_number == 1)

    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line1.id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"},
    )
    assert patch_resp.status_code == 200

    # 3. Advisory reflects the updated state: now ready_no_action_needed
    post_patch_resp = client.get(f"/api/v1/drafts/{draft_id}/advisory")
    assert post_patch_resp.status_code == 200
    post_data = post_patch_resp.json()
    assert post_data["advisory_status"] == "ready_no_action_needed"


# -----------------------------------------------------------------------------
# 9. Performance Budget Assertion
# -----------------------------------------------------------------------------

def test_performance_budget(client, seeded_db, no_external_network):
    # Test performance on all fixtures with default max_depth=2, max_scenarios=24
    for fix_id in ("fixture-clean-acme", "fixture-ambiguous-apex", "fixture-discrepancy-apex"):
        ingest_resp = client.post(f"/api/v1/fixtures/{fix_id}/ingest")
        assert ingest_resp.status_code == 201
        draft_id = ingest_resp.json()["draft_id"]

        start = time.perf_counter()
        resp = client.get(f"/api/v1/drafts/{draft_id}/advisory?max_depth=2&max_scenarios=24")
        duration = time.perf_counter() - start

        assert resp.status_code == 200
        assert duration < 3.0, f"Advisory on {fix_id} took {duration:.3f}s (> 3.0s budget)"
