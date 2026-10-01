"""T016: P1 intake, persistence, approval, failure safety, and replay isolation.

HTTP fields come from api-contracts.md (notably po_number_extracted on drafts).
The sole proposed DI seam is routes_orders.get_live_ai_provider, isolated in
_override_live_provider. It is an internal review seam for T017/T019/T020.
No P2 PATCH, DELETE, or reject routes are required by this P1 test pack.
"""

from copy import deepcopy
from datetime import datetime
import importlib
import importlib.util
import json
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
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


MODELS = {"qwen": "qwen3.8-flash", "gemini": "gemini-3.5-flash-lite"}
REPLAY_URL = "/api/v1/fixtures/fixture-clean-acme/ingest"


@pytest.fixture(autouse=True)
def controlled_network(no_external_network):
    """TestClient is in-process; every external connection is forbidden."""


@pytest.fixture
def seeded_db(db_session):
    seed_baseline(db_session)
    db_session.commit()
    return db_session


@pytest.fixture
def bind_live_provider(app_instance):
    """Restore all overrides even when a tests-first assertion fails."""
    previous = dict(app_instance.dependency_overrides)
    try:
        yield lambda provider: _override_live_provider(app_instance, provider)
    finally:
        app_instance.dependency_overrides.clear()
        app_instance.dependency_overrides.update(previous)


def _require_p1_application():
    if importlib.util.find_spec("app.main") is None:
        pytest.fail(
            "T017-T022 P1 application missing: app/main.py and intake/approval routes are not implemented",
            pytrace=False,
        )


def _override_live_provider(app, provider):
    """Constructor/factory adaptation lives here, not in lifecycle assertions."""
    routes = importlib.import_module("app.api.routes_orders")
    app.dependency_overrides[routes.get_live_ai_provider] = lambda: provider


def _clean_live_provider(mock_ai_provider, dataset, provider_name="qwen"):
    mock_ai_provider.provider_name = provider_name
    mock_ai_provider.model_name = MODELS[provider_name]
    mock_ai_provider.is_replay_mode = False
    mock_ai_provider.default_payload = AIExtractionPayload.model_validate(deepcopy(dataset["extraction"]))
    return mock_ai_provider


def _failed_live_provider(mock_ai_provider, module, provider_name, failure):
    mock_ai_provider.provider_name = provider_name
    mock_ai_provider.model_name = MODELS[provider_name]
    mock_ai_provider.is_replay_mode = False
    # T015 tests raw transport -> diagnostic mapping. Here inject its resulting
    # boundary exception, so API error mapping is tested without moving validation
    # or HTTP transport concerns into the order coordinator.
    error_class = (
        module.AIOutputValidationError if failure == "invalid-output"
        else module.AIProviderUnavailableError
    )
    mock_ai_provider.extract = Mock(side_effect=error_class(f"Controlled {provider_name} {failure}"))
    return mock_ai_provider


def _fixture_substitution_spy(module, monkeypatch):
    spy = Mock(side_effect=AssertionError("Live route must not invoke fixture substitution"))
    monkeypatch.setattr(module.FixtureAIProvider, "extract", spy)
    return spy


def _live_intake(client, path):
    return client.post(
        "/api/v1/orders/ingest",
        files={"file": (path.name, path.read_bytes(), "text/plain")},
    )


def _intake(client, path, mode):
    response = client.post(REPLAY_URL) if mode == "replay" else _live_intake(client, path)
    # The live contract specifies 201. Replay specifies a successful draft
    # response without freezing a particular successful HTTP status code.
    assert (response.is_success if mode == "replay" else response.status_code == 201), response.text
    return response.json()


def _assert_clean_draft(draft, *, replay):
    assert draft["draft_id"]
    assert draft["document_id"]
    assert draft["customer_id"] == "CUST-ACME"
    assert draft["customer_name_extracted"] == "Acme Industrial Supplies"
    assert draft["po_number_extracted"] == "PO-10023"
    assert draft["is_replay_mode"] is replay
    assert draft["status"] == "Ready for Approval"
    assert draft["calculated_subtotal"] == "350.00"
    assert len(draft["line_items"]) == 2
    lines = sorted(draft["line_items"], key=lambda line: line["line_number"])
    assert [(line["line_number"], line["matched_sku"], line["extracted_quantity"],
             line["contract_price"], line["calculated_line_total"]) for line in lines] == [
        (1, "SKU-WRAP-18", 10, "25.00", "250.00"),
        (2, "SKU-WRAP-15", 5, "20.00", "100.00"),
    ]
    for line in lines:
        assert line["line_id"]
        assert line["status"] == "Active"
        assert line["sku_resolution_source"] == "AI_HIGH_CONFIDENCE"
        assert line["discrepancies"] == []


def _observer(db_session):
    """Read durable state after releasing the request's shared SQLite session.

    Closing rolls back any still-uncommitted writes, so an omitted application
    commit cannot look persisted just because StaticPool shares a connection.
    """
    engine = db_session.get_bind()
    db_session.close()
    return Session(bind=engine)


def _assert_no_partial_persistence(db_session):
    with _observer(db_session) as observer:
        for entity in (OrderDraft, DraftLineItem, FieldProvenance, VerifiedOrderRecord):
            assert observer.scalars(select(entity)).all() == [], f"Partial persisted {entity.__name__}"


def _assert_error(response, expected_status, error_name):
    assert response.status_code == expected_status, response.text
    diagnostic = ErrorResponse.model_validate(response.json())
    assert diagnostic.error == error_name
    assert diagnostic.message.strip()


def _assert_approved_persistence(db_session, draft_id, response, *, replay):
    assert response["draft_id"] == draft_id
    assert response["customer_id"] == "CUST-ACME"
    assert response["po_number"] == "PO-10023"
    assert response["grand_total"] == "350.00"
    assert response["approved_by"] == "op-sarah"
    assert response["is_replay_mode"] is replay
    assert response["line_items_count"] == 2
    assert response["order_number"]
    assert datetime.fromisoformat(response["approved_at"].replace("Z", "+00:00")).utcoffset() is not None
    with _observer(db_session) as observer:
        draft = observer.get(OrderDraft, draft_id)
        assert draft.status == "Approved"
        records = observer.scalars(select(VerifiedOrderRecord).where(VerifiedOrderRecord.draft_id == draft_id)).all()
        assert len(records) == 1
        record = records[0]
        assert record.id == response["order_id"]
        assert record.customer_id == "CUST-ACME"
        assert record.po_number == "PO-10023"
        assert record.grand_total_cents == 35000
        assert record.approved_by == "op-sarah"
        assert record.is_replay_mode is replay
        assert record.approved_at is not None
        assert len(json.loads(record.line_items_snapshot_json)) == 2
        approvals = observer.scalars(select(AuditEvent).where(
            AuditEvent.draft_id == draft_id, AuditEvent.event_type == "OrderApproved",
        )).all()
        assert len(approvals) == 1


def test_clean_replay_intake_is_ready_with_seeded_prices(client, seeded_db):
    _require_p1_application()
    response = client.post(REPLAY_URL)
    assert response.is_success, response.text
    draft = response.json()
    _assert_clean_draft(draft, replay=True)
    with _observer(seeded_db) as observer:
        persisted = observer.get(OrderDraft, draft["draft_id"])
        assert persisted.is_replay_mode is True
        assert persisted.status == "Ready for Approval"
        assert persisted.calculated_subtotal_cents == 35000
        assert observer.scalars(select(DiscrepancyFlag).where(
            DiscrepancyFlag.draft_id == draft["draft_id"],
            DiscrepancyFlag.resolution_state == "Unresolved",
        )).all() == []


@pytest.mark.parametrize("provider_name", MODELS)
def test_clean_controlled_live_intake_is_ready_and_persists_grounding(
    client, seeded_db, bind_live_provider, mock_ai_provider,
    clean_acme_fixture, po_clean_acme_path, provider_name,
):
    _require_p1_application()
    provider = _clean_live_provider(mock_ai_provider, clean_acme_fixture, provider_name)
    bind_live_provider(provider)
    draft = _intake(client, po_clean_acme_path, "live")
    _assert_clean_draft(draft, replay=False)
    assert provider.call_count == 1
    with _observer(seeded_db) as observer:
        persisted = observer.get(OrderDraft, draft["draft_id"])
        assert persisted.status == "Ready for Approval"
        assert persisted.customer_id == "CUST-ACME"
        assert persisted.po_number_extracted == "PO-10023"
        assert persisted.is_replay_mode is False
        document = observer.get(PurchaseOrderDocument, draft["document_id"])
        assert document.raw_text == po_clean_acme_path.read_bytes().decode("utf-8")
        citations = observer.scalars(select(FieldProvenance).where(FieldProvenance.draft_id == persisted.id)).all()
        assert len(citations) == 10  # Two header fields + four fields on each line.
        assert {citation.field_name for citation in citations if citation.line_item_id is None} == {
            "customer_name", "po_number",
        }
        lines = observer.scalars(select(DraftLineItem).where(DraftLineItem.draft_id == persisted.id)).all()
        assert len(lines) == 2
        for line in lines:
            assert {citation.field_name for citation in citations if citation.line_item_id == line.id} == {
                "customer_description", "extracted_quantity", "extracted_unit_price", "extracted_line_total",
            }
        for citation in citations:
            assert citation.location_type == "txt"
            location = json.loads(citation.location_data_json)
            source_line = document.raw_text.splitlines()[location["line_number"] - 1]
            start = location["char_offset"]
            assert source_line[start:start + len(citation.verbatim_snippet)] == citation.verbatim_snippet
        assert observer.scalars(select(DiscrepancyFlag).where(
            DiscrepancyFlag.draft_id == persisted.id, DiscrepancyFlag.resolution_state == "Unresolved",
        )).all() == []


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_draft_inspection_matches_persisted_intake(
    client, seeded_db, bind_live_provider, mock_ai_provider, clean_acme_fixture, po_clean_acme_path, mode,
):
    _require_p1_application()
    bind_live_provider(_clean_live_provider(mock_ai_provider, clean_acme_fixture))
    intake = _intake(client, po_clean_acme_path, mode)
    response = client.get(f"/api/v1/drafts/{intake['draft_id']}")
    assert response.status_code == 200, response.text
    inspected = response.json()
    _assert_clean_draft(inspected, replay=mode == "replay")
    for field in ("draft_id", "document_id", "customer_id", "po_number_extracted", "is_replay_mode", "status", "calculated_subtotal"):
        assert inspected[field] == intake[field]
    assert inspected["line_items"] == intake["line_items"]
    with _observer(seeded_db) as observer:
        persisted = observer.get(OrderDraft, intake["draft_id"])
        assert persisted.calculated_subtotal_cents == 35000
        assert persisted.status == inspected["status"]


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_approval_atomically_creates_verified_record_status_and_audit(
    client, seeded_db, bind_live_provider, mock_ai_provider, clean_acme_fixture, po_clean_acme_path, mode,
):
    _require_p1_application()
    bind_live_provider(_clean_live_provider(mock_ai_provider, clean_acme_fixture))
    draft = _intake(client, po_clean_acme_path, mode)
    response = client.post(f"/api/v1/drafts/{draft['draft_id']}/approve", json={"operator_id": "op-sarah"})
    assert response.status_code == 200, response.text
    _assert_approved_persistence(seeded_db, draft["draft_id"], response.json(), replay=mode == "replay")
    inspected = client.get(f"/api/v1/drafts/{draft['draft_id']}")
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["status"] == "Approved"


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_duplicate_approval_returns_409_and_keeps_one_unchanged_verified_order(
    client, seeded_db, bind_live_provider, mock_ai_provider, clean_acme_fixture, po_clean_acme_path, mode,
):
    _require_p1_application()
    bind_live_provider(_clean_live_provider(mock_ai_provider, clean_acme_fixture))
    draft = _intake(client, po_clean_acme_path, mode)
    url = f"/api/v1/drafts/{draft['draft_id']}/approve"
    first = client.post(url, json={"operator_id": "op-sarah"})
    assert first.status_code == 200, first.text
    with _observer(seeded_db) as observer:
        record = observer.scalars(select(VerifiedOrderRecord)).one()
        before = (record.id, record.approved_by, record.approved_at, record.grand_total_cents, record.line_items_snapshot_json)
    second = client.post(url, json={"operator_id": "op-other"})
    _assert_error(second, 409, "TerminalDraftConflictError")
    with _observer(seeded_db) as observer:
        records = observer.scalars(select(VerifiedOrderRecord)).all()
        assert len(records) == 1
        record = records[0]
        assert (record.id, record.approved_by, record.approved_at, record.grand_total_cents, record.line_items_snapshot_json) == before
        assert observer.get(OrderDraft, draft["draft_id"]).status == "Approved"
        assert len(observer.scalars(select(AuditEvent).where(AuditEvent.event_type == "OrderApproved")).all()) == 1


@pytest.mark.parametrize("provider_name", MODELS)
@pytest.mark.parametrize("failure", ["timeout", "upstream"])
def test_live_provider_failure_returns_503_without_partial_persistence(
    client, seeded_db, bind_live_provider, mock_ai_provider, require_ai_provider,
    po_clean_acme_path, provider_name, failure, monkeypatch,
):
    _require_p1_application()
    module = require_ai_provider()
    fixture_spy = _fixture_substitution_spy(module, monkeypatch)
    provider = _failed_live_provider(mock_ai_provider, module, provider_name, failure)
    bind_live_provider(provider)
    response = _live_intake(client, po_clean_acme_path)
    _assert_error(response, 503, "AIProviderUnavailableError")
    assert provider.extract.call_count == 1
    fixture_spy.assert_not_called()
    _assert_no_partial_persistence(seeded_db)


@pytest.mark.parametrize("provider_name", MODELS)
def test_invalid_ai_output_returns_502_without_partial_persistence(
    client, seeded_db, bind_live_provider, mock_ai_provider, require_ai_provider, po_clean_acme_path, provider_name, monkeypatch,
):
    _require_p1_application()
    module = require_ai_provider()
    fixture_spy = _fixture_substitution_spy(module, monkeypatch)
    provider = _failed_live_provider(mock_ai_provider, module, provider_name, "invalid-output")
    bind_live_provider(provider)
    response = _live_intake(client, po_clean_acme_path)
    _assert_error(response, 502, "AIOutputValidationError")
    assert provider.extract.call_count == 1
    fixture_spy.assert_not_called()
    _assert_no_partial_persistence(seeded_db)


@pytest.mark.parametrize("provider_name", MODELS)
def test_replay_requires_explicit_fixture_route_after_live_failure(
    client, seeded_db, bind_live_provider, mock_ai_provider, require_ai_provider, po_clean_acme_path, provider_name, monkeypatch,
):
    _require_p1_application()
    module = require_ai_provider()
    provider = _failed_live_provider(mock_ai_provider, module, provider_name, "upstream")
    bind_live_provider(provider)
    with monkeypatch.context() as patch:
        fixture_spy = _fixture_substitution_spy(module, patch)
        _assert_error(_live_intake(client, po_clean_acme_path), 503, "AIProviderUnavailableError")
        fixture_spy.assert_not_called()
    _assert_no_partial_persistence(seeded_db)
    replay = _intake(client, po_clean_acme_path, "replay")
    _assert_clean_draft(replay, replay=True)
    assert provider.extract.call_count == 1  # Explicit replay does not invoke live inference.
    with _observer(seeded_db) as observer:
        drafts = observer.scalars(select(OrderDraft)).all()
        assert len(drafts) == 1
        assert drafts[0].id == replay["draft_id"]
        assert drafts[0].is_replay_mode is True


@pytest.mark.parametrize("mode,provider_name,model", [
    ("live", "qwen", "qwen3.8-flash"),
    ("live", "gemini", "gemini-3.5-flash-lite"),
    ("replay", "fixture", "pre-verified-dataset"),
])
def test_audit_persists_actual_provider_and_model_identity(
    client, seeded_db, bind_live_provider, mock_ai_provider, clean_acme_fixture, po_clean_acme_path,
    mode, provider_name, model,
):
    _require_p1_application()
    if mode == "live":
        bind_live_provider(_clean_live_provider(mock_ai_provider, clean_acme_fixture, provider_name))
    draft = _intake(client, po_clean_acme_path, mode)
    with _observer(seeded_db) as observer:
        events = observer.scalars(select(AuditEvent).where(
            AuditEvent.draft_id == draft["draft_id"], AuditEvent.event_type == "AIExtractionCompleted",
        )).all()
        assert len(events) == 1
        details = json.loads(events[0].details_json)
        assert details["provider"] == provider_name
        assert details["model"] == model
        assert observer.get(OrderDraft, draft["draft_id"]).is_replay_mode is (mode == "replay")


@pytest.mark.parametrize("mode", ["live", "replay"])
@pytest.mark.parametrize("failed_entity", [VerifiedOrderRecord, AuditEvent], ids=["verified-record-write", "approval-audit-write"])
def test_approval_write_failure_does_not_leave_half_approved_state(
    client, seeded_db, bind_live_provider, mock_ai_provider, clean_acme_fixture, po_clean_acme_path, mode, failed_entity,
):
    _require_p1_application()
    bind_live_provider(_clean_live_provider(mock_ai_provider, clean_acme_fixture))
    draft = _intake(client, po_clean_acme_path, mode)

    fault_calls = []

    def fail_approval_write(mapper, connection, target):
        if isinstance(target, VerifiedOrderRecord) or target.event_type == "OrderApproved":
            fault_calls.append(target)
            raise RuntimeError("Controlled approval persistence failure")

    event.listen(failed_entity, "before_insert", fail_approval_write)
    try:
        # TestClient propagates unhandled server errors; an app exception handler
        # may instead serialize HTTP 500. Both must preserve durable state.
        try:
            response = client.post(f"/api/v1/drafts/{draft['draft_id']}/approve", json={"operator_id": "op-sarah"})
        except RuntimeError as error:
            assert str(error) == "Controlled approval persistence failure"
        else:
            assert 500 <= response.status_code < 600, response.text
    finally:
        event.remove(failed_entity, "before_insert", fail_approval_write)

    assert fault_calls, "The controlled persistence failure must actually be exercised"
    with _observer(seeded_db) as observer:
        persisted = observer.get(OrderDraft, draft["draft_id"])
        assert persisted.status == "Ready for Approval"
        assert observer.scalars(select(VerifiedOrderRecord)).all() == []
        assert observer.scalars(select(AuditEvent).where(AuditEvent.event_type == "OrderApproved")).all() == []
        assert persisted.calculated_subtotal_cents == 35000
        assert len(observer.scalars(select(DraftLineItem).where(DraftLineItem.draft_id == persisted.id)).all()) == 2

    # A subsequent approval remains possible after the injected write fault.
    retry = client.post(f"/api/v1/drafts/{draft['draft_id']}/approve", json={"operator_id": "op-sarah"})
    assert retry.status_code == 200, retry.text
    _assert_approved_persistence(seeded_db, draft["draft_id"], retry.json(), replay=mode == "replay")
