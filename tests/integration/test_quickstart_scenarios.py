"""T039: Automated Quickstart Verification Suite.

Validates all five accepted scenarios from specs/001-ordershield-po-reconciliation/quickstart.md:
1. Clean live intake and approval (Happy Path)
2. Discrepancy + ambiguity resolution (Operator Resolution)
3. Provider failure semantics (Immediate errors, stalled timeouts, zero failover/substitution/persistence)
4. Replay isolation / disclosure (Explicit fixture intake and unclosable disclosure)
5. Audit trail + provenance (Full chronological audit history and grounded citations)
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import routes_orders
from app.cli import seed_baseline
from app.config import Settings
from app.models.entities import (
    AuditEvent,
    DiscrepancyFlag,
    DraftLineItem,
    FieldProvenance,
    OrderDraft,
    PurchaseOrderDocument,
    VerifiedOrderRecord,
)
from app.models.schemas import AIExtractionPayload, ErrorResponse
from app.services.ai_provider import (
    FixtureAIProvider,
    LiveAIProvider,
)
from tests.conftest import MockAIProvider


# -----------------------------------------------------------------------------
# Test Harness & Common Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def guard_no_external_network(no_external_network):
    """Enforce credential-free and deterministic execution with zero external network."""


@pytest.fixture(autouse=True)
def seed_test_database(db_session: Session):
    """Ensure baseline catalog products and customer contracts are seeded in clean in-memory state."""
    seed_baseline(db_session)
    db_session.commit()


@pytest.fixture
def override_live_provider(app_instance):
    """Restore FastAPI dependency overrides after test completion."""
    previous = dict(app_instance.dependency_overrides)

    def _override(provider: Any) -> None:
        app_instance.dependency_overrides[routes_orders.get_live_ai_provider] = lambda: provider

    try:
        yield _override
    finally:
        app_instance.dependency_overrides.clear()
        app_instance.dependency_overrides.update(previous)


def _isolated_session(db_session: Session) -> Session:
    """Create an isolated Session bound to the same engine to inspect committed state."""
    engine = db_session.get_bind()
    return Session(bind=engine)


def _assert_zero_partial_persistence(db_session: Session) -> None:
    """Verify zero order-processing entities exist in the database."""
    with _isolated_session(db_session) as obs:
        for entity in (
            PurchaseOrderDocument,
            OrderDraft,
            DraftLineItem,
            FieldProvenance,
            DiscrepancyFlag,
            AuditEvent,
            VerifiedOrderRecord,
        ):
            count = obs.scalar(select(func.count()).select_from(entity))
            assert count == 0, f"Found {count} persisted records for {entity.__name__} after failed intake"


class ControlledTransport:
    """Deterministic, credential-free in-memory transport for live provider verification."""

    def __init__(self, extraction_text: str = "{}", *, failure: str | int | None = None) -> None:
        self.extraction_text = extraction_text
        self.failure = failure
        self.calls = {"qwen": 0, "gemini": 0}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        model = body.get("model", "")
        if model == "qwen3.8-flash":
            name = "qwen"
        elif model == "gemini-3.5-flash-lite" or "gemini-3.5-flash-lite" in request.url.path:
            name = "gemini"
        else:
            raise AssertionError(f"Unexpected live model in controlled request: {model} {request.url.path}")

        self.calls[name] += 1
        self.requests.append(request)

        if self.failure == "timeout":
            raise httpx.ReadTimeout("Controlled stalled inference at client deadline", request=request)
        if self.failure == "connection":
            raise httpx.ConnectError("Controlled connection refusal", request=request)
        if isinstance(self.failure, int):
            return httpx.Response(self.failure, json={"error": {"message": f"Controlled HTTP {self.failure}"}})

        if name == "qwen":
            response = {"choices": [{"message": {"content": self.extraction_text}}]}
        else:
            response = {"candidates": [{"content": {"parts": [{"text": self.extraction_text}]}}]}
        return httpx.Response(200, json=response)


# -----------------------------------------------------------------------------
# Scenario 1: Clean Live Intake & One-Click Approval
# -----------------------------------------------------------------------------


def test_quickstart_scenario_1_clean_live_intake_and_approval(
    client,
    override_live_provider,
    clean_acme_fixture,
    po_clean_acme_path,
    po_clean_acme_text,
):
    """Quickstart Scenario 1: Clean live PO intake, grounding, one-click approval, and terminal protection."""
    # 1. Controlled live provider injection with committed clean Acme extraction
    clean_payload = AIExtractionPayload.model_validate(clean_acme_fixture["extraction"])
    provider = MockAIProvider(
        default_payload=clean_payload,
        provider_name="qwen",
        model_name="qwen3.8-flash",
    )
    provider.is_replay_mode = False
    override_live_provider(provider)

    # 2. Intake via POST /api/v1/orders/ingest (not replay)
    response = client.post(
        "/api/v1/orders/ingest",
        files={"file": (po_clean_acme_path.name, po_clean_acme_text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 201, response.text
    draft = response.json()

    # Required assertions on ingested draft
    assert draft["customer_id"] == "CUST-ACME"
    assert draft["customer_name_extracted"] == "Acme Industrial Supplies"
    assert draft["po_number_extracted"] == "PO-10023"
    assert draft["is_replay_mode"] is False
    assert draft["status"] == "Ready for Approval"

    # Verify both active lines
    line_items = draft["line_items"]
    assert len(line_items) == 2
    for line in line_items:
        assert line["status"] == "Active"
        assert line["sku_confidence"] == "High"
        assert line["sku_resolution_source"] == "AI_HIGH_CONFIDENCE"

    # Zero unresolved discrepancies
    unresolved_discrepancies = [
        flag
        for line in line_items
        for flag in line.get("discrepancies", [])
        if flag.get("resolution_state") == "Unresolved"
    ]
    assert unresolved_discrepancies == []
    assert all(line["discrepancies"] == [] for line in line_items)

    # Canonical deterministic contract pricing and subtotal
    assert draft["calculated_subtotal"] == "350.00"
    lines_by_num = {line["line_number"]: line for line in line_items}
    assert lines_by_num[1]["matched_sku"] == "SKU-WRAP-18"
    assert lines_by_num[1]["extracted_quantity"] == 10
    assert lines_by_num[1]["contract_price"] == "25.00"
    assert lines_by_num[1]["calculated_line_total"] == "250.00"

    assert lines_by_num[2]["matched_sku"] == "SKU-WRAP-15"
    assert lines_by_num[2]["extracted_quantity"] == 5
    assert lines_by_num[2]["contract_price"] == "20.00"
    assert lines_by_num[2]["calculated_line_total"] == "100.00"

    # 3. Source grounding verification
    # Header grounding
    header_prov = draft["header_provenance"]
    for header_field in ("customer_name", "po_number"):
        entry = header_prov.get(header_field)
        assert entry is not None, f"Missing header provenance for {header_field}"
        assert entry["verbatim_snippet"], f"Empty snippet for {header_field}"
        assert isinstance(entry["location"], dict), f"Missing location for {header_field}"
        assert entry["location"].get("type") == "txt"

    # Line grounding
    for line in line_items:
        fp = line["field_provenance"]
        for field in ("customer_description", "extracted_quantity", "extracted_unit_price", "extracted_line_total"):
            entry = fp.get(field)
            assert entry is not None, f"Missing line provenance for {field} on line {line['line_number']}"
            assert entry["verbatim_snippet"], f"Empty snippet for {field} on line {line['line_number']}"
            assert isinstance(entry["location"], dict), f"Missing location for {field} on line {line['line_number']}"
            assert entry["location"].get("type") == "txt"

    # 4. One-Click Approval
    draft_id = draft["draft_id"]
    approve_response = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"operator_id": "quickstart-op"},
    )
    assert approve_response.status_code == 200, approve_response.text
    order = approve_response.json()

    assert order["order_number"] == "VO-2026-0001"
    assert re.match(r"^VO-\d{4}-\d{4}$", order["order_number"])
    assert order["approved_by"] == "quickstart-op"
    assert order["is_replay_mode"] is False
    assert order["customer_id"] == "CUST-ACME"
    assert order["po_number"] == "PO-10023"
    assert order["grand_total"] == "350.00"
    assert order["line_items_count"] == 2

    # 5. Terminal Protection: second approval and mutations must return 409
    second_approval = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"operator_id": "quickstart-op"},
    )
    assert second_approval.status_code == 409
    err_second = ErrorResponse.model_validate(second_approval.json())
    assert err_second.error == "TerminalDraftConflictError"

    first_line_id = line_items[0]["line_id"]
    patch_attempt = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{first_line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    assert patch_attempt.status_code == 409
    err_patch = ErrorResponse.model_validate(patch_attempt.json())
    assert err_patch.error == "TerminalDraftConflictError"

    delete_attempt = client.delete(
        f"/api/v1/drafts/{draft_id}/lines/{first_line_id}",
    )
    assert delete_attempt.status_code == 409
    err_delete = ErrorResponse.model_validate(delete_attempt.json())
    assert err_delete.error == "TerminalDraftConflictError"


# -----------------------------------------------------------------------------
# Scenario 2: Seeded Discrepancy & Ambiguity Handling
# -----------------------------------------------------------------------------


def test_quickstart_scenario_2_discrepancy_and_ambiguity_resolution(
    client,
    override_live_provider,
    discrepancy_apex_fixture,
    po_discrepancy_apex_path,
    po_discrepancy_apex_text,
    db_session,
):
    """Quickstart Scenario 2: Seeded discrepancy, blocked approval, SelectSKU, line removal, and resolution."""
    # 1. Controlled live provider injection with committed discrepancy Apex extraction
    discrepancy_payload = AIExtractionPayload.model_validate(discrepancy_apex_fixture["extraction"])
    provider = MockAIProvider(
        default_payload=discrepancy_payload,
        provider_name="qwen",
        model_name="qwen3.8-flash",
    )
    provider.is_replay_mode = False
    override_live_provider(provider)

    # 2. Intake via POST /api/v1/orders/ingest (not replay)
    response = client.post(
        "/api/v1/orders/ingest",
        files={"file": (po_discrepancy_apex_path.name, po_discrepancy_apex_text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    draft_id = draft["draft_id"]

    # Initial state: Needs Review
    assert draft["status"] == "Needs Review"
    lines_by_num = {line["line_number"]: line for line in draft["line_items"]}

    # Line 1: PriceMismatch unresolved
    line1 = lines_by_num[1]
    assert line1["matched_sku"] == "SKU-WRAP-18"
    assert line1["extracted_unit_price"] == "18.00"
    assert line1["contract_price"] == "22.00"
    price_flags = [
        f for f in line1["discrepancies"]
        if f["discrepancy_type"] == "PriceMismatch" and f["resolution_state"] == "Unresolved"
    ]
    assert len(price_flags) == 1
    assert price_flags[0]["expected_value"] == "$22.00"
    assert price_flags[0]["requested_value"] == "$18.00"

    # Line 2: CatalogMatchingMismatch unresolved, Ambiguous
    line2 = lines_by_num[2]
    assert line2["matched_sku"] is None
    assert line2["sku_confidence"] == "Ambiguous"
    assert line2["sku_resolution_source"] == "NONE"
    catalog_flags = [
        f for f in line2["discrepancies"]
        if f["discrepancy_type"] == "CatalogMatchingMismatch" and f["resolution_state"] == "Unresolved"
    ]
    assert len(catalog_flags) == 1
    candidates = [c["sku"] for c in line2["candidate_skus"]]
    assert "SKU-WRAP-15" in candidates
    assert "SKU-WRAP-18" in candidates

    # 3. Approval must initially fail before reconciliation
    blocked_approval = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"operator_id": "quickstart-op"},
    )
    assert blocked_approval.status_code == 409
    err_approval = ErrorResponse.model_validate(blocked_approval.json())
    assert err_approval.error == "DraftNotReadyForApprovalError"

    # Verify no VerifiedOrderRecord and no OrderApproved audit event created
    with _isolated_session(db_session) as obs:
        assert obs.scalars(select(VerifiedOrderRecord)).all() == []
        events = obs.scalars(select(AuditEvent).where(AuditEvent.draft_id == draft_id)).all()
        assert not any(e.event_type == "OrderApproved" for e in events)

    # 4. Operator resolves line 2 ambiguity via PATCH action="SelectSKU"
    patch_response = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line2['line_id']}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"},
    )
    assert patch_response.status_code == 200, patch_response.text
    patched_draft = patch_response.json()
    line2_updated = next(l for l in patched_draft["line_items"] if l["line_id"] == line2["line_id"])
    assert line2_updated["sku_resolution_source"] == "OPERATOR_SELECTED"
    assert line2_updated["matched_sku"] == "SKU-WRAP-15"

    # Deterministic revalidation exposes genuine QuantityOrPackagingBreach (Qty 2 < MOQ 5)
    moq_breach = next(
        (f for f in line2_updated["discrepancies"]
         if f["discrepancy_type"] == "QuantityOrPackagingBreach" and f["resolution_state"] == "Unresolved"),
        None,
    )
    assert moq_breach is not None, "Expected unresolved QuantityOrPackagingBreach after SelectSKU"
    assert "2" in moq_breach["requested_value"]
    assert "5" in moq_breach["expected_value"]

    # 5. Operator removes non-compliant commercial violation on line 1 via DELETE
    delete_response = client.delete(
        f"/api/v1/drafts/{draft_id}/lines/{line1['line_id']}",
    )
    assert delete_response.status_code == 200, delete_response.text
    deleted_draft = delete_response.json()
    line1_updated = next(l for l in deleted_draft["line_items"] if l["line_id"] == line1["line_id"])
    assert line1_updated["status"] == "Removed"

    # Discrepancy history must remain present, marked ResolvedByLineRemoval
    discrepancy_history = line1_updated["discrepancies"]
    assert len(discrepancy_history) > 0
    resolved_by_removal = [
        f for f in discrepancy_history if f["resolution_state"] == "ResolvedByLineRemoval"
    ]
    assert len(resolved_by_removal) == 1
    assert resolved_by_removal[0]["discrepancy_type"] == "PriceMismatch"

    # 6. Final State inspection via GET /api/v1/drafts/{draft_id}
    # Remaining unresolved MOQ breach on Line 2 keeps draft in Needs Review
    get_draft_response = client.get(f"/api/v1/drafts/{draft_id}")
    assert get_draft_response.status_code == 200
    final_draft = get_draft_response.json()
    assert final_draft["status"] == "Needs Review"

    active_unresolved = [
        flag
        for line in final_draft["line_items"]
        if line["status"] == "Active"
        for flag in line["discrepancies"]
        if flag["resolution_state"] == "Unresolved"
    ]
    assert len(active_unresolved) >= 1
    assert any(f["discrepancy_type"] == "QuantityOrPackagingBreach" for f in active_unresolved)

    # 7. Approval must remain blocked
    blocked_approval_2 = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"operator_id": "quickstart-op"},
    )
    assert blocked_approval_2.status_code == 409
    err_approval_2 = ErrorResponse.model_validate(blocked_approval_2.json())
    assert err_approval_2.error == "DraftNotReadyForApprovalError"

    # 8. Operator rejects draft with mandatory operator identity and reason
    rejection_reason = "Valid MOQ breach cannot be resolved without changing the source order."
    reject_response = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={
            "operator_id": "quickstart-op",
            "reason": rejection_reason,
        },
    )
    assert reject_response.status_code == 200, reject_response.text
    rejected_draft = reject_response.json()
    assert rejected_draft["status"] == "Rejected"
    assert rejected_draft["rejection_reason"] == rejection_reason

    # Verify DraftRejected audit event persistence
    with _isolated_session(db_session) as obs:
        reject_event = obs.scalar(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "DraftRejected",
            )
        )
        assert reject_event is not None
        assert reject_event.actor == "Operator"
        details = json.loads(reject_event.details_json)
        assert details["operator_id"] == "quickstart-op"
        assert details["reason"] == rejection_reason

    # 9. Terminal protection: rejected draft cannot be approved or modified
    post_reject_approval = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"operator_id": "quickstart-op"},
    )
    assert post_reject_approval.status_code == 409
    err_post_reject_app = ErrorResponse.model_validate(post_reject_approval.json())
    assert err_post_reject_app.error == "TerminalDraftConflictError"

    post_reject_patch = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line2['line_id']}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"},
    )
    assert post_reject_patch.status_code == 409
    err_post_reject_patch = ErrorResponse.model_validate(post_reject_patch.json())
    assert err_post_reject_patch.error == "TerminalDraftConflictError"

    # 10. Verify zero VerifiedOrderRecord created and audit trail completeness
    with _isolated_session(db_session) as obs:
        verified_records = obs.scalars(
            select(VerifiedOrderRecord).where(VerifiedOrderRecord.draft_id == draft_id)
        ).all()
        assert verified_records == [], "No VerifiedOrderRecord must exist for rejected draft"

        audit_events = obs.scalars(
            select(AuditEvent).where(AuditEvent.draft_id == draft_id).order_by(AuditEvent.timestamp.asc(), AuditEvent.id.asc())
        ).all()
        event_types = [e.event_type for e in audit_events]
        assert "OrderApproved" not in event_types
        for required_event in (
            "DocumentIngested",
            "AIExtractionCompleted",
            "SKUSelected",
            "LineRemoved",
            "DraftRejected",
        ):
            assert required_event in event_types, f"Missing audit event {required_event}"


# -----------------------------------------------------------------------------
# Scenario 3: AI Service Failure Resilience & Graceful Error
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    ["connection", 401, 429, 503],
    ids=["connection-refused", "auth-401", "quota-429", "upstream-503"],
)
def test_quickstart_scenario_3_immediate_provider_failure_is_explicit_and_atomic(
    client,
    override_live_provider,
    po_clean_acme_path,
    db_session,
    monkeypatch,
    failure,
):
    """Quickstart Scenario 3: Immediate detectable failures return 503, <=5s, with zero failover/substitution/persistence."""
    # 1. Spy on FixtureAIProvider to verify zero fixture substitution
    fixture_spy = Mock(side_effect=AssertionError("Failed live inference must never substitute fixture extraction"))
    monkeypatch.setattr(FixtureAIProvider, "extract", fixture_spy)

    # 2. Configure LiveAIProvider with in-memory ControlledTransport
    provider_name = "qwen"
    transport = ControlledTransport(failure=failure)
    mock_client = httpx.Client(transport=httpx.MockTransport(transport), timeout=None, trust_env=False)
    settings = Settings(
        llm_provider=provider_name,
        llm_api_key="mock-test-key",
        database_url="sqlite:///:memory:",
        live_inference_timeout=15.0,
    )
    live_provider = LiveAIProvider(settings=settings, client=mock_client)
    override_live_provider(live_provider)

    # 3. Time the intake request with monotonic clock
    started = time.monotonic()
    response = client.post(
        "/api/v1/orders/ingest",
        files={"file": (po_clean_acme_path.name, po_clean_acme_path.read_bytes(), "text/plain")},
    )
    elapsed = time.monotonic() - started

    # Timing requirement: target <=5.0 seconds from intake
    assert elapsed <= 5.0, f"Immediate failure took {elapsed}s, exceeded 5.0s limit"

    # HTTP response: 503 AIProviderUnavailableError
    assert response.status_code == 503, response.text
    err = ErrorResponse.model_validate(response.json())
    assert err.error == "AIProviderUnavailableError"
    assert err.message.strip()

    # Zero failover: exactly one request to configured provider, 0 to alternate
    assert transport.calls["qwen"] == 1
    assert transport.calls["gemini"] == 0

    # Zero fixture substitution
    fixture_spy.assert_not_called()

    # Zero partial persistence
    _assert_zero_partial_persistence(db_session)


def test_quickstart_scenario_3_stalled_provider_has_bounded_deadline(
    client,
    override_live_provider,
    po_clean_acme_path,
    db_session,
    monkeypatch,
):
    """Quickstart Scenario 3: Stalled inference is bounded by <=15s client deadline with zero failover/substitution/persistence."""
    # 1. Spy on FixtureAIProvider
    fixture_spy = Mock(side_effect=AssertionError("Failed live inference must never substitute fixture extraction"))
    monkeypatch.setattr(FixtureAIProvider, "extract", fixture_spy)

    # 2. Configure LiveAIProvider with stalled transport raising httpx.ReadTimeout
    provider_name = "qwen"
    transport = ControlledTransport(failure="timeout")
    mock_client = httpx.Client(transport=httpx.MockTransport(transport), timeout=None, trust_env=False)
    settings = Settings(
        llm_provider=provider_name,
        llm_api_key="mock-test-key",
        database_url="sqlite:///:memory:",
        live_inference_timeout=15.0,
    )
    live_provider = LiveAIProvider(settings=settings, client=mock_client)
    override_live_provider(live_provider)

    # 3. Post intake request
    response = client.post(
        "/api/v1/orders/ingest",
        files={"file": (po_clean_acme_path.name, po_clean_acme_path.read_bytes(), "text/plain")},
    )

    # Inspect outgoing request timeout configuration directly on real LiveAIProvider client
    assert len(transport.requests) == 1
    timeouts = transport.requests[0].extensions.get("timeout")
    assert timeouts is not None, "Provider must configure outgoing request timeout"
    assert all(
        val is not None and 0 < val <= 15.0 for val in timeouts.values()
    ), f"Timeouts must all be > 0 and <= 15.0s, got: {timeouts}"

    # HTTP response: 503 AIProviderUnavailableError
    assert response.status_code == 503, response.text
    err = ErrorResponse.model_validate(response.json())
    assert err.error == "AIProviderUnavailableError"

    # Zero failover
    assert transport.calls["qwen"] == 1
    assert transport.calls["gemini"] == 0

    # Zero fixture substitution
    fixture_spy.assert_not_called()

    # Zero partial persistence
    _assert_zero_partial_persistence(db_session)


# -----------------------------------------------------------------------------
# Scenario 4: Replay Mode & Visible Isolation
# -----------------------------------------------------------------------------


def test_quickstart_scenario_4_replay_is_explicit_and_isolated(
    client,
    app_instance,
    db_session,
    monkeypatch,
):
    """Quickstart Scenario 4: Replay route isolation, non-live badge, and banner binding contract."""
    # 1. Guard against live provider use during replay
    live_seam_spy = Mock(side_effect=AssertionError("Replay route must not call get_live_ai_provider seam"))
    app_instance.dependency_overrides[routes_orders.get_live_ai_provider] = live_seam_spy
    live_extract_spy = Mock(side_effect=AssertionError("Replay route must not invoke LiveAIProvider.extract"))
    monkeypatch.setattr(LiveAIProvider, "extract", live_extract_spy)

    # 2. Intake via dedicated fixture replay route
    response = client.post("/api/v1/fixtures/fixture-clean-acme/ingest")
    assert response.status_code == 201, response.text
    draft = response.json()

    # Explicit replay mode disclosure in API response
    assert draft["is_replay_mode"] is True
    assert draft["customer_id"] == "CUST-ACME"
    assert draft["po_number_extracted"] == "PO-10023"

    # Live provider remained untouched
    live_seam_spy.assert_not_called()
    live_extract_spy.assert_not_called()

    # 3. Provenance/Audit trail records actual replay identity rather than live provider
    with _isolated_session(db_session) as obs:
        events = obs.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft["draft_id"],
                AuditEvent.event_type == "AIExtractionCompleted",
            )
        ).all()
        assert len(events) == 1
        details = json.loads(events[0].details_json)
        assert details["provider"] == "fixture"
        assert details["model"] == "pre-verified-dataset"

    # 4. Mandatory Replay Banner Static Contract
    index_html = (Path("app/static/index.html")).read_text(encoding="utf-8")
    assert "⚠️ DEMO / REPLAY MODE (NON-LIVE FIXTURE DATA)" in index_html
    assert 'id="replay-banner"' in index_html

    # Banner visibility binding contract in SPA logic
    app_js = (Path("app/static/js/app.js")).read_text(encoding="utf-8")
    assert "state.draft.is_replay_mode === true" in app_js


# -----------------------------------------------------------------------------
# Scenario 5: Audit Trail & Grounded Provenance Inspection
# -----------------------------------------------------------------------------


def test_quickstart_scenario_5_audit_and_provenance_are_inspectable(
    client,
    override_live_provider,
    clean_acme_fixture,
    po_clean_acme_path,
    po_clean_acme_text,
):
    """Quickstart Scenario 5: Chronological audit history, actor verification, and grounded provenance inspection."""
    # 1. Build an approved order through live intake and operator interaction
    clean_payload = AIExtractionPayload.model_validate(clean_acme_fixture["extraction"])
    provider = MockAIProvider(
        default_payload=clean_payload,
        provider_name="qwen",
        model_name="qwen3.8-flash",
    )
    provider.is_replay_mode = False
    override_live_provider(provider)

    intake_resp = client.post(
        "/api/v1/orders/ingest",
        files={"file": (po_clean_acme_path.name, po_clean_acme_text.encode("utf-8"), "text/plain")},
    )
    assert intake_resp.status_code == 201
    draft = intake_resp.json()
    draft_id = draft["draft_id"]
    lines_by_num = {line["line_number"]: line for line in draft["line_items"]}

    # Operator selects SKU on line 1
    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{lines_by_num[1]['line_id']}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    assert patch_resp.status_code == 200

    # Operator removes line 2
    delete_resp = client.delete(
        f"/api/v1/drafts/{draft_id}/lines/{lines_by_num[2]['line_id']}",
    )
    assert delete_resp.status_code == 200

    # Operator approves draft
    approve_resp = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"operator_id": "quickstart-op"},
    )
    assert approve_resp.status_code == 200
    committed_order = approve_resp.json()
    order_id = committed_order["order_id"]

    # 2. Retrieve verified order via GET /api/v1/orders/{order_id}
    order_resp = client.get(f"/api/v1/orders/{order_id}")
    assert order_resp.status_code == 200
    order_data = order_resp.json()

    # 3. Verify chronological audit trail
    audit_trail = order_data["audit_trail"]
    assert len(audit_trail) >= 5

    event_types = [e["event_type"] for e in audit_trail]
    for expected in (
        "DocumentIngested",
        "AIExtractionCompleted",
        "SKUSelected",
        "LineRemoved",
        "OrderApproved",
    ):
        assert expected in event_types, f"Expected event {expected} not in audit trail: {event_types}"

    # Verify intake events occur before operator mutations and final approval
    intake_indices = [event_types.index("DocumentIngested"), event_types.index("AIExtractionCompleted")]
    sku_idx = event_types.index("SKUSelected")
    del_idx = event_types.index("LineRemoved")
    app_idx = event_types.index("OrderApproved")

    assert max(intake_indices) < sku_idx < del_idx < app_idx, (
        f"Audit events not in expected chronological order: {event_types}"
    )

    # Verify timestamps are non-decreasing
    timestamps = [datetime.fromisoformat(e["timestamp"]) for e in audit_trail]
    for i in range(len(timestamps) - 1):
        assert timestamps[i] <= timestamps[i + 1], f"Decreasing timestamp between event {i} and {i+1}"

    # Verify operator events expose server-returned actor and details
    sku_event = next(e for e in audit_trail if e["event_type"] == "SKUSelected")
    assert sku_event["actor"] == "Operator"
    assert sku_event["details"]["selected_sku"] == "SKU-WRAP-18"
    assert sku_event["details"]["resolution_source"] == "OPERATOR_SELECTED"

    remove_event = next(e for e in audit_trail if e["event_type"] == "LineRemoved")
    assert remove_event["actor"] == "Operator"
    assert remove_event["details"]["line_number"] == 2

    approve_event = next(e for e in audit_trail if e["event_type"] == "OrderApproved")
    assert approve_event["actor"] == "Operator"
    assert approve_event["details"]["operator_id"] == "quickstart-op"

    # 4. Provenance inspection
    # Header provenance
    hp = order_data["header_provenance"]
    assert hp["customer_name"] is not None
    assert hp["customer_name"]["verbatim_snippet"] == "Acme Industrial Supplies"
    assert hp["customer_name"]["location"]["line_number"] == 2
    assert hp["customer_name"]["location"]["char_offset"] == 10

    assert hp["po_number"] is not None
    assert hp["po_number"]["verbatim_snippet"] == "PO-10023"
    assert hp["po_number"]["location"]["line_number"] == 4
    assert hp["po_number"]["location"]["char_offset"] == 11

    # Committed lines provenance (line 2 was removed, so only line 1 is committed)
    committed_lines = order_data["line_items"]
    assert len(committed_lines) == 1
    committed_line = committed_lines[0]
    assert committed_line["sku"] == "SKU-WRAP-18"

    fp = committed_line["field_provenance"]
    for field in ("customer_description", "extracted_quantity", "extracted_unit_price", "extracted_line_total"):
        citation = fp.get(field)
        assert citation is not None, f"Missing line citation for {field}"
        assert citation["verbatim_snippet"], f"Empty snippet for {field}"
        assert isinstance(citation["location"], dict), f"Missing location dict for {field}"
        assert citation["location"].get("type") == "txt"

    # Representative exact-offset assertion for customer description
    assert fp["customer_description"]["verbatim_snippet"] == "18in stretch film heavy duty"
    assert fp["customer_description"]["location"]["line_number"] == 9
    assert fp["customer_description"]["location"]["char_offset"] == 0


def test_quickstart_scenario_5_static_spa_integration_contract():
    """Quickstart Scenario 5: Static SPA integration contract for provenance drawer, audit trail, and non-live banner."""
    index_html = (Path("app/static/index.html")).read_text(encoding="utf-8")
    assert "js/components/provenance_drawer.js" in index_html
    assert 'id="audit-load"' in index_html
    assert "source-evidence-control" in index_html
    assert 'id="replay-banner"' in index_html

    drawer_file = Path("app/static/js/components/provenance_drawer.js")
    assert drawer_file.is_file(), "Provenance drawer JS file must exist"
    assert len(drawer_file.read_text(encoding="utf-8").strip()) > 0
