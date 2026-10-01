"""Focused integration tests for P2 operator mutation routes: PATCH, DELETE, and POST reject."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.models.entities import (
    AuditEvent, DiscrepancyFlag, DraftLineItem, OrderDraft,
)
from app.models.schemas import ErrorResponse


pytestmark = pytest.mark.usefixtures("no_external_network")


@pytest.fixture
def seeded_db(db_session):
    seed_baseline(db_session)
    db_session.commit()
    return db_session


def _observer(db_session):
    engine = db_session.get_bind()
    db_session.close()
    return Session(bind=engine)


def _assert_error(response, expected_status, error_name):
    assert response.status_code == expected_status, response.text
    err = ErrorResponse.model_validate(response.json())
    assert err.error == error_name
    assert err.message.strip()


def _get_line_by_number(draft_data: dict, line_number: int) -> dict:
    for item in draft_data.get("line_items", []):
        if item.get("line_number") == line_number:
            return item
    raise ValueError(f"Line number {line_number} not found in draft line_items")


# =============================================================================
# A. SelectSKU audit & Happy Path
# =============================================================================


def test_select_sku_success_updates_line_and_records_audit_event(client, seeded_db):
    """Successful SelectSKU updates SKU, sets OPERATOR_SELECTED, and appends SKUSelected audit event."""
    draft = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_id = draft["line_items"][0]["line_id"]

    resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "Ready for Approval"

    line = next(l for l in data["line_items"] if l["line_id"] == line_id)
    assert line["matched_sku"] == "SKU-WRAP-15"
    assert line["sku_resolution_source"] == "OPERATOR_SELECTED"
    assert line["contract_price"] == "20.00"

    with _observer(seeded_db) as obs:
        db_line = obs.get(DraftLineItem, line_id)
        assert db_line.matched_sku == "SKU-WRAP-15"
        assert db_line.sku_resolution_source == "OPERATOR_SELECTED"

        events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "SKUSelected",
            )
        ).all()
        assert len(events) == 1
        event = events[0]
        assert event.actor == "Operator"
        details = json.loads(event.details_json)
        assert details["line_number"] == 1
        assert details["previous_sku"] is None
        assert details["selected_sku"] == "SKU-WRAP-15"
        assert details["resolution_source"] == "OPERATOR_SELECTED"


def test_select_sku_allows_valid_catalog_sku_outside_ai_candidates(client, seeded_db):
    """Operator can select a valid catalog SKU not suggested in candidate_skus."""
    draft = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_id = draft["line_items"][0]["line_id"]

    resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-TAPE-02"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["line_items"][0]["matched_sku"] == "SKU-TAPE-02"

    with _observer(seeded_db) as obs:
        events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "SKUSelected",
            )
        ).all()
        assert len(events) == 1
        details = json.loads(events[0].details_json)
        assert details["selected_sku"] == "SKU-TAPE-02"


# =============================================================================
# B. Unknown SKU atomic failure
# =============================================================================


def test_select_sku_unknown_sku_fails_with_422_atomically(client, seeded_db):
    """Selecting an unknown SKU returns 422 LineMutationValidationError and leaves state unmutated."""
    draft = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_id = draft["line_items"][0]["line_id"]

    with _observer(seeded_db) as obs:
        audit_count_before = len(obs.scalars(select(AuditEvent).where(AuditEvent.draft_id == draft_id)).all())

    resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-UNKNOWN-999"},
    )
    _assert_error(resp, 422, "LineMutationValidationError")

    with _observer(seeded_db) as obs:
        db_line = obs.get(DraftLineItem, line_id)
        assert db_line.matched_sku is None
        assert db_line.sku_resolution_source == "NONE"

        audit_count_after = len(obs.scalars(select(AuditEvent).where(AuditEvent.draft_id == draft_id)).all())
        assert audit_count_after == audit_count_before

        sku_events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "SKUSelected",
            )
        ).all()
        assert sku_events == []


# =============================================================================
# C. CorrectField audit & atomicity
# =============================================================================


def test_correct_field_success_creates_audit_event_and_updates_line(client, seeded_db):
    """Successful field correction records FieldCorrected event with line details."""
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_2 = _get_line_by_number(draft, 2)
    line_2_id = line_2["line_id"]

    # Grounded in fixture text: line 10 offset 43 is '2'
    resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_2_id}",
        json={
            "action": "CorrectField",
            "field": "extracted_quantity",
            "value": 2,
            "source_snippet": "2",
            "source_location": {
                "type": "txt",
                "line_number": 10,
                "char_offset": 43,
            },
        },
    )
    assert resp.status_code == 200, resp.text

    with _observer(seeded_db) as obs:
        db_line = obs.get(DraftLineItem, line_2_id)
        assert db_line.extracted_quantity == 2

        events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "FieldCorrected",
            )
        ).all()
        assert len(events) == 1
        event = events[0]
        assert event.actor == "Operator"
        details = json.loads(event.details_json)
        assert details["line_number"] == 2
        assert details["field"] == "extracted_quantity"
        assert details["previous_value"] == 2
        assert details["new_value"] == 2
        assert details["source_snippet"] == "2"
        assert details["source_location"] == {"char_offset": 43, "line_number": 10, "type": "txt"}


def test_correct_field_forged_grounding_fails_with_422_atomically(client, seeded_db):
    """Forged grounding snippet returns 422 SourceGroundingMismatchError with no audit events."""
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_2_id = _get_line_by_number(draft, 2)["line_id"]

    with _observer(seeded_db) as obs:
        audit_count_before = len(obs.scalars(select(AuditEvent).where(AuditEvent.draft_id == draft_id)).all())

    resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_2_id}",
        json={
            "action": "CorrectField",
            "field": "extracted_quantity",
            "value": 999,
            "source_snippet": "999",
            "source_location": {
                "type": "txt",
                "line_number": 1,
                "char_offset": 0,
            },
        },
    )
    _assert_error(resp, 422, "SourceGroundingMismatchError")

    with _observer(seeded_db) as obs:
        audit_count_after = len(obs.scalars(select(AuditEvent).where(AuditEvent.draft_id == draft_id)).all())
        assert audit_count_after == audit_count_before

        field_events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "FieldCorrected",
            )
        ).all()
        assert field_events == []


# =============================================================================
# D. Line removal audit
# =============================================================================


def test_remove_line_marks_status_removed_and_records_audit_event(client, seeded_db):
    """DELETE line marks status Removed, preserves flags, and emits LineRemoved audit event."""
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_1 = _get_line_by_number(draft, 1)
    line_1_id = line_1["line_id"]

    resp = client.delete(f"/api/v1/drafts/{draft_id}/lines/{line_1_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    line_resp = next(l for l in data["line_items"] if l["line_id"] == line_1_id)
    assert line_resp["status"] == "Removed"

    with _observer(seeded_db) as obs:
        db_line = obs.get(DraftLineItem, line_1_id)
        assert db_line.status == "Removed"

        flags = obs.scalars(
            select(DiscrepancyFlag).where(DiscrepancyFlag.line_item_id == line_1_id)
        ).all()
        assert all(f.resolution_state == "ResolvedByLineRemoval" for f in flags)

        events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "LineRemoved",
            )
        ).all()
        assert len(events) == 1
        event = events[0]
        assert event.actor == "Operator"
        details = json.loads(event.details_json)
        assert details["line_number"] == 1
        assert details["matched_sku"] == "SKU-WRAP-18"


# =============================================================================
# E. Rejection projection & validation
# =============================================================================


def test_reject_draft_sets_rejected_status_and_exposes_reason(client, seeded_db):
    """POST /reject transitions to Rejected and serializes rejection_reason."""
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]

    resp = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"operator_id": "op-sarah", "reason": "Customer cancelled order."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "Rejected"
    assert data["rejection_reason"] == "Customer cancelled order."

    with _observer(seeded_db) as obs:
        events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "DraftRejected",
            )
        ).all()
        assert len(events) == 1
        event = events[0]
        assert "op-sarah" in event.actor or "op-sarah" in event.details_json
        assert "Customer cancelled order." in event.details_json


def test_reject_draft_validation_fails_closed(client, seeded_db):
    """Rejection with missing or blank fields returns 422 and does not mutate draft."""
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]

    # Missing reason
    r1 = client.post(f"/api/v1/drafts/{draft_id}/reject", json={"operator_id": "op-sarah"})
    assert r1.status_code == 422

    # Blank reason
    r2 = client.post(f"/api/v1/drafts/{draft_id}/reject", json={"operator_id": "op-sarah", "reason": "   "})
    assert r2.status_code == 422

    # Missing operator_id
    r3 = client.post(f"/api/v1/drafts/{draft_id}/reject", json={"reason": "Valid reason"})
    assert r3.status_code == 422

    with _observer(seeded_db) as obs:
        db_draft = obs.get(OrderDraft, draft_id)
        assert db_draft.status == "Needs Review"
        assert db_draft.rejection_reason is None
        events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "DraftRejected",
            )
        ).all()
        assert events == []


# =============================================================================
# F. Terminal state protection
# =============================================================================


def test_approved_draft_blocks_mutations_with_409(client, seeded_db):
    """Approved draft returns 409 TerminalDraftConflictError on all mutation endpoints."""
    draft = client.post("/api/v1/fixtures/fixture-clean-acme/ingest").json()
    draft_id = draft["draft_id"]
    line_id = draft["line_items"][0]["line_id"]

    approve_resp = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"operator_id": "op-sarah"})
    assert approve_resp.status_code == 200

    # 1. PATCH SelectSKU
    r_patch = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    _assert_error(r_patch, 409, "TerminalDraftConflictError")

    # 2. DELETE line
    r_del = client.delete(f"/api/v1/drafts/{draft_id}/lines/{line_id}")
    _assert_error(r_del, 409, "TerminalDraftConflictError")

    # 3. POST reject
    r_rej = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"operator_id": "op-sarah", "reason": "Cannot reject approved"},
    )
    _assert_error(r_rej, 409, "TerminalDraftConflictError")


def test_rejected_draft_blocks_mutations_with_409(client, seeded_db):
    """Rejected draft returns 409 TerminalDraftConflictError on all mutation endpoints."""
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_id = draft["line_items"][0]["line_id"]

    reject_resp = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"operator_id": "op-sarah", "reason": "Initial rejection"},
    )
    assert reject_resp.status_code == 200

    # 1. PATCH SelectSKU
    r_patch = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    _assert_error(r_patch, 409, "TerminalDraftConflictError")

    # 2. DELETE line
    r_del = client.delete(f"/api/v1/drafts/{draft_id}/lines/{line_id}")
    _assert_error(r_del, 409, "TerminalDraftConflictError")

    # 3. POST reject repeat
    r_rej = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"operator_id": "op-other", "reason": "Second rejection"},
    )
    _assert_error(r_rej, 409, "TerminalDraftConflictError")

    # 4. POST approve
    r_app = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"operator_id": "op-other"})
    _assert_error(r_app, 409, "TerminalDraftConflictError")


# =============================================================================
# G. Missing resources & removed line mutations
# =============================================================================


def test_missing_resources_return_404(client, seeded_db):
    """Missing draft or missing line returns deterministic 404."""
    draft = client.post("/api/v1/fixtures/fixture-clean-acme/ingest").json()
    draft_id = draft["draft_id"]
    line_id = draft["line_items"][0]["line_id"]

    # Nonexistent draft
    r1 = client.patch(
        "/api/v1/drafts/draft-does-not-exist/lines/l1",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    _assert_error(r1, 404, "DraftNotFoundError")

    r2 = client.delete("/api/v1/drafts/draft-does-not-exist/lines/l1")
    _assert_error(r2, 404, "DraftNotFoundError")

    r3 = client.post(
        "/api/v1/drafts/draft-does-not-exist/reject",
        json={"operator_id": "op-sarah", "reason": "Reason"},
    )
    _assert_error(r3, 404, "DraftNotFoundError")

    # Nonexistent line
    r4 = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/line-does-not-exist",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    _assert_error(r4, 404, "LineNotFoundError")

    r5 = client.delete(f"/api/v1/drafts/{draft_id}/lines/line-does-not-exist")
    _assert_error(r5, 404, "LineNotFoundError")


def test_cannot_mutate_line_belonging_to_another_draft(client, seeded_db):
    """Mutating a line belonging to another draft returns 404 LineNotFoundError without leaking."""
    draft1 = client.post("/api/v1/fixtures/fixture-clean-acme/ingest").json()
    draft2 = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()

    line_from_draft2 = draft2["line_items"][0]["line_id"]

    r_patch = client.patch(
        f"/api/v1/drafts/{draft1['draft_id']}/lines/{line_from_draft2}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    _assert_error(r_patch, 404, "LineNotFoundError")

    r_del = client.delete(f"/api/v1/drafts/{draft1['draft_id']}/lines/{line_from_draft2}")
    _assert_error(r_del, 404, "LineNotFoundError")


def test_cannot_patch_removed_line(client, seeded_db):
    """Attempting to PATCH a line that is status=Removed fails with 422 LineMutationValidationError."""
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_1_id = _get_line_by_number(draft, 1)["line_id"]

    del_resp = client.delete(f"/api/v1/drafts/{draft_id}/lines/{line_1_id}")
    assert del_resp.status_code == 200

    r_patch = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_1_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    _assert_error(r_patch, 422, "LineMutationValidationError")
