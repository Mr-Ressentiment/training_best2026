"""T016: P1 intake, persistence, approval, failure safety, and replay isolation.

HTTP fields come from api-contracts.md (notably po_number_extracted on drafts).
The sole proposed DI seam is routes_orders.get_live_ai_provider, isolated in
_override_live_provider. It is an internal review seam for T017/T019/T020.
No P2 PATCH, DELETE, or reject routes are required by this P1 test pack.
"""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
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
    CatalogProduct,
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


# =============================================================================
# T027 / VIC-P2-TEST-01: P2 Operator Workflow Integration Acceptance Contracts
# =============================================================================


def _extract_line_from_response(response_data: dict, line_id: str) -> dict:
    """Extract line item dict from either draft response or direct line item response."""
    if "line_items" in response_data:
        for item in response_data["line_items"]:
            if item.get("line_id") == line_id:
                return item
        raise ValueError(f"Line {line_id} not found in response line_items")
    return response_data


def _get_line_by_number(draft_data: dict, line_number: int) -> dict:
    """Find a line item by 1-based line_number from a draft response."""
    for item in draft_data.get("line_items", []):
        if item.get("line_number") == line_number:
            return item
    raise ValueError(f"Line number {line_number} not found in draft line_items")


def test_catalog_search_by_query_returns_matching_products_with_decimal_prices(client, seeded_db):
    """P2 Catalog: GET /api/v1/catalog?query=wrap returns seeded products with 2-decimal money."""
    _require_p1_application()
    response = client.get("/api/v1/catalog?query=wrap")
    assert response.status_code == 200, response.text
    products = response.json()
    assert isinstance(products, list)

    skus = {p["sku"]: p for p in products}
    assert "SKU-WRAP-18" in skus
    assert "SKU-WRAP-15" in skus

    for sku, p in skus.items():
        assert p["name"]
        assert p["category"]
        assert p["unit_of_measure"]
        assert isinstance(p["base_price"], str)
        # Decimal serialization must be valid with exactly 2 decimal places
        parsed_money = Decimal(p["base_price"])
        assert parsed_money >= Decimal("0.00")
        assert len(p["base_price"].split(".")[1]) == 2
        assert isinstance(p["min_order_quantity"], int) and p["min_order_quantity"] >= 1
        assert isinstance(p["package_increment"], int) and p["package_increment"] >= 1


def test_catalog_search_by_category_filters_results(client, seeded_db):
    """P2 Catalog: GET /api/v1/catalog?category=Packaging returns filtered products."""
    _require_p1_application()
    response = client.get("/api/v1/catalog?category=Packaging")
    assert response.status_code == 200, response.text
    products = response.json()
    assert isinstance(products, list)
    assert len(products) >= 2
    assert all(p["category"] == "Packaging" for p in products)


def test_select_sku_happy_path_resolves_ambiguity_and_promotes_draft(client, seeded_db):
    """P2 SelectSKU: Resolving ambiguous line to SKU-WRAP-15 updates pricing and promotes to Ready."""
    _require_p1_application()
    intake = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest")
    assert intake.is_success, intake.text
    draft = intake.json()
    draft_id = draft["draft_id"]
    line = draft["line_items"][0]
    line_id = line["line_id"]

    assert draft["status"] == "Needs Review"
    assert line["sku_confidence"] == "Ambiguous"
    assert line["matched_sku"] is None
    assert line["sku_resolution_source"] == "NONE"

    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    updated_line = _extract_line_from_response(patch_resp.json(), line_id)
    assert updated_line["matched_sku"] == "SKU-WRAP-15"
    assert updated_line["sku_resolution_source"] == "OPERATOR_SELECTED"
    assert updated_line["contract_price"] == "20.00"
    assert updated_line["calculated_line_total"] == "200.00"

    inspected = client.get(f"/api/v1/drafts/{draft_id}")
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["status"] == "Ready for Approval"

    with _observer(seeded_db) as observer:
        persisted_line = observer.get(DraftLineItem, line_id)
        assert persisted_line.matched_sku == "SKU-WRAP-15"
        assert persisted_line.sku_resolution_source == "OPERATOR_SELECTED"
        assert persisted_line.contract_price_cents == 2000
        assert persisted_line.calculated_line_total_cents == 20000

        persisted_draft = observer.get(OrderDraft, draft_id)
        assert persisted_draft.status == "Ready for Approval"
        assert persisted_draft.calculated_subtotal_cents == 20000

        unresolved_flags = observer.scalars(
            select(DiscrepancyFlag).where(
                DiscrepancyFlag.draft_id == draft_id,
                DiscrepancyFlag.discrepancy_type == "CatalogMatchingMismatch",
                DiscrepancyFlag.resolution_state == "Unresolved",
            )
        ).all()
        assert unresolved_flags == []


def test_select_sku_allows_valid_catalog_sku_outside_ai_candidates(client, seeded_db):
    """P2 SelectSKU: Operator can select valid catalog SKU not present in AI candidate suggestions."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest").json()
    draft_id = draft["draft_id"]
    line = draft["line_items"][0]
    line_id = line["line_id"]

    # In fixture-ambiguous-apex, candidates are only SKU-WRAP-15 and SKU-WRAP-18
    # SKU-TAPE-02 is a valid master catalog SKU not in candidate_skus
    with _observer(seeded_db) as observer:
        tape_product = observer.get(CatalogProduct, "SKU-TAPE-02")
        assert tape_product is not None

    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-TAPE-02"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    updated_line = _extract_line_from_response(patch_resp.json(), line_id)
    assert updated_line["matched_sku"] == "SKU-TAPE-02"
    assert updated_line["sku_resolution_source"] == "OPERATOR_SELECTED"

    with _observer(seeded_db) as observer:
        persisted_line = observer.get(DraftLineItem, line_id)
        assert persisted_line.matched_sku == "SKU-TAPE-02"
        assert persisted_line.sku_resolution_source == "OPERATOR_SELECTED"


def test_select_sku_unknown_sku_fails_closed_without_persisting(client, seeded_db):
    """P2 SelectSKU: Selecting an unknown SKU fails closed and does not mutate line."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest").json()
    draft_id = draft["draft_id"]
    line = draft["line_items"][0]
    line_id = line["line_id"]

    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-DOES-NOT-EXIST"},
    )
    # The exact HTTP status for unknown catalog SKU is a documented minor contract gap (e.g. 404 vs 422); assert non-success
    assert not patch_resp.is_success, f"Expected non-success status, got {patch_resp.status_code}: {patch_resp.text}"
    assert patch_resp.status_code != 200

    with _observer(seeded_db) as observer:
        persisted_line = observer.get(DraftLineItem, line_id)
        assert persisted_line.matched_sku != "SKU-DOES-NOT-EXIST"
        assert persisted_line.matched_sku is None
        assert persisted_line.sku_resolution_source == "NONE"


def test_correct_field_forged_grounding_rejected_with_422(client, seeded_db):
    """P2 CorrectField: Forged grounding snippet differing from canonical raw_text returns 422."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_2 = _get_line_by_number(draft, 2)
    line_2_id = line_2["line_id"]

    # Line 2 canonical source at line 10, char_offset 43 contains "2", not "10"
    dishonest_payload = {
        "action": "CorrectField",
        "field": "extracted_quantity",
        "value": 10,
        "source_snippet": "10",
        "source_location": {
            "type": "txt",
            "line_number": 10,
            "char_offset": 43,
        },
    }
    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_2_id}",
        json=dishonest_payload,
    )
    _assert_error(patch_resp, 422, "SourceGroundingMismatchError")

    with _observer(seeded_db) as observer:
        persisted_line = observer.get(DraftLineItem, line_2_id)
        assert persisted_line.extracted_quantity == 2
        persisted_draft = observer.get(OrderDraft, draft_id)
        assert persisted_draft.status == "Needs Review"
        doc = observer.get(PurchaseOrderDocument, draft["document_id"])
        # Canonical raw_text at line 10 remains uncorrupted
        assert doc.raw_text.splitlines()[9][43] == "2"


def test_correct_field_failure_atomicity_leaves_durable_state_unmutated(client, seeded_db):
    """P2 CorrectField: Grounding validation failure leaves entire durable state unmutated."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_2_id = _get_line_by_number(draft, 2)["line_id"]

    with _observer(seeded_db) as observer:
        d_before = observer.get(OrderDraft, draft_id)
        status_before = d_before.status
        subtotal_before = d_before.calculated_subtotal_cents
        raw_text_before = observer.get(PurchaseOrderDocument, draft["document_id"]).raw_text
        lines_before = [
            (l.id, l.line_number, l.extracted_quantity, l.extracted_unit_price_cents,
             l.extracted_line_total_cents, l.matched_sku, l.status)
            for l in observer.scalars(select(DraftLineItem).where(DraftLineItem.draft_id == draft_id)).all()
        ]
        audit_count_before = len(observer.scalars(select(AuditEvent).where(AuditEvent.draft_id == draft_id)).all())

    dishonest_payload = {
        "action": "CorrectField",
        "field": "extracted_quantity",
        "value": 999,
        "source_snippet": "999",
        "source_location": {
            "type": "txt",
            "line_number": 1,
            "char_offset": 0,
        },
    }
    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_2_id}",
        json=dishonest_payload,
    )
    _assert_error(patch_resp, 422, "SourceGroundingMismatchError")

    with _observer(seeded_db) as observer:
        d_after = observer.get(OrderDraft, draft_id)
        assert d_after.status == status_before
        assert d_after.calculated_subtotal_cents == subtotal_before
        assert observer.get(PurchaseOrderDocument, draft["document_id"]).raw_text == raw_text_before
        lines_after = [
            (l.id, l.line_number, l.extracted_quantity, l.extracted_unit_price_cents,
             l.extracted_line_total_cents, l.matched_sku, l.status)
            for l in observer.scalars(select(DraftLineItem).where(DraftLineItem.draft_id == draft_id)).all()
        ]
        assert lines_after == lines_before
        audit_count_after = len(observer.scalars(select(AuditEvent).where(AuditEvent.draft_id == draft_id)).all())
        assert audit_count_after == audit_count_before
        assert observer.scalars(select(VerifiedOrderRecord).where(VerifiedOrderRecord.draft_id == draft_id)).all() == []


def test_remove_line_marks_status_removed_and_preserves_discrepancy_history(client, seeded_db):
    """P2 Line Removal: DELETE line sets status=Removed and marks discrepancies ResolvedByLineRemoval."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_1 = _get_line_by_number(draft, 1)
    line_1_id = line_1["line_id"]

    del_resp = client.delete(f"/api/v1/drafts/{draft_id}/lines/{line_1_id}")
    assert del_resp.status_code == 200, del_resp.text

    with _observer(seeded_db) as observer:
        persisted_line = observer.get(DraftLineItem, line_1_id)
        assert persisted_line.status == "Removed"

        # Discrepancies must NOT be deleted; any line 1 flags become ResolvedByLineRemoval
        line_1_flags = observer.scalars(
            select(DiscrepancyFlag).where(DiscrepancyFlag.line_item_id == line_1_id)
        ).all()
        for flag in line_1_flags:
            assert flag.resolution_state == "ResolvedByLineRemoval"


def test_remove_line_recalculates_subtotal_and_preserves_active_blockers(client, seeded_db):
    """P2 Line Removal: Recalculates subtotal excluding removed line; draft remains Needs Review if blockers persist."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_1 = _get_line_by_number(draft, 1)
    line_1_id = line_1["line_id"]

    del_resp = client.delete(f"/api/v1/drafts/{draft_id}/lines/{line_1_id}")
    assert del_resp.status_code == 200, del_resp.text

    inspected = client.get(f"/api/v1/drafts/{draft_id}").json()
    # Removing line 1 leaves line 2 with ambiguous SKU / quantity discrepancy
    assert inspected["status"] == "Needs Review"
    active_lines = [l for l in inspected["line_items"] if l["status"] == "Active"]
    assert len(active_lines) == 1
    assert active_lines[0]["line_number"] == 2

    with _observer(seeded_db) as observer:
        persisted_draft = observer.get(OrderDraft, draft_id)
        assert persisted_draft.status == "Needs Review"
        active_db_lines = observer.scalars(
            select(DraftLineItem).where(
                DraftLineItem.draft_id == draft_id,
                DraftLineItem.status == "Active",
            )
        ).all()
        expected_subtotal = sum(l.calculated_line_total_cents for l in active_db_lines)
        assert persisted_draft.calculated_subtotal_cents == expected_subtotal


def test_reject_draft_sets_rejected_status_persists_reason_and_records_audit_event(client, seeded_db):
    """P2 Reject: POST /reject transitions draft to Rejected, stores reason, and emits DraftRejected audit event."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]

    reject_payload = {
        "operator_id": "op-victor",
        "reason": "Commercial discrepancies require customer revision.",
    }
    reject_resp = client.post(f"/api/v1/drafts/{draft_id}/reject", json=reject_payload)
    assert reject_resp.status_code == 200, reject_resp.text
    rejected_body = reject_resp.json()
    assert rejected_body["status"] == "Rejected"
    assert rejected_body.get("rejection_reason") == "Commercial discrepancies require customer revision."

    with _observer(seeded_db) as observer:
        persisted = observer.get(OrderDraft, draft_id)
        assert persisted.status == "Rejected"
        assert persisted.rejection_reason == "Commercial discrepancies require customer revision."

        reject_events = observer.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "DraftRejected",
            )
        ).all()
        assert len(reject_events) == 1
        event = reject_events[0]
        assert "op-victor" in event.actor or "op-victor" in event.details_json


def test_reject_draft_requires_reason_and_leaves_durable_state_unchanged(client, seeded_db):
    """P2 Reject: Missing or blank rejection reason returns 422 and does not reject draft."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]

    # Missing reason field
    missing_resp = client.post(f"/api/v1/drafts/{draft_id}/reject", json={"operator_id": "op-victor"})
    assert missing_resp.status_code == 422, missing_resp.text

    # Blank / whitespace-only reason
    blank_resp = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"operator_id": "op-victor", "reason": "   "},
    )
    assert blank_resp.status_code == 422, blank_resp.text

    with _observer(seeded_db) as observer:
        persisted = observer.get(OrderDraft, draft_id)
        assert persisted.status != "Rejected"
        assert persisted.rejection_reason is None
        assert observer.scalars(
            select(AuditEvent).where(
                AuditEvent.draft_id == draft_id,
                AuditEvent.event_type == "DraftRejected",
            )
        ).all() == []


def test_rejected_draft_blocks_further_mutations_with_terminal_conflict(client, seeded_db):
    """P2 Terminal: Rejected draft returns 409 TerminalDraftConflictError on any subsequent mutation."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest").json()
    draft_id = draft["draft_id"]
    line_id = draft["line_items"][0]["line_id"]

    first_reject = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"operator_id": "op-victor", "reason": "Initial rejection"},
    )
    assert first_reject.status_code == 200, first_reject.text

    # 1. Attempt PATCH line
    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    _assert_error(patch_resp, 409, "TerminalDraftConflictError")

    # 2. Attempt DELETE line
    del_resp = client.delete(f"/api/v1/drafts/{draft_id}/lines/{line_id}")
    _assert_error(del_resp, 409, "TerminalDraftConflictError")

    # 3. Attempt repeated POST reject
    repeat_resp = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"operator_id": "op-other", "reason": "Second rejection"},
    )
    _assert_error(repeat_resp, 409, "TerminalDraftConflictError")

    # 4. Attempt POST approve
    approve_resp = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"operator_id": "op-other"},
    )
    _assert_error(approve_resp, 409, "TerminalDraftConflictError")

    with _observer(seeded_db) as observer:
        persisted = observer.get(OrderDraft, draft_id)
        assert persisted.status == "Rejected"
        assert persisted.rejection_reason == "Initial rejection"
        assert observer.scalars(select(VerifiedOrderRecord).where(VerifiedOrderRecord.draft_id == draft_id)).all() == []
        assert len(observer.scalars(select(AuditEvent).where(AuditEvent.draft_id == draft_id, AuditEvent.event_type == "DraftRejected")).all()) == 1
        assert observer.get(DraftLineItem, line_id).status == "Active"


def test_approved_draft_blocks_p2_mutations_with_terminal_conflict(client, seeded_db):
    """P2 Terminal: Approved draft returns 409 TerminalDraftConflictError on P2 mutation routes."""
    _require_p1_application()
    draft = client.post("/api/v1/fixtures/fixture-clean-acme/ingest").json()
    draft_id = draft["draft_id"]
    line_id = draft["line_items"][0]["line_id"]

    approve_resp = client.post(
        f"/api/v1/drafts/{draft_id}/approve",
        json={"operator_id": "op-sarah"},
    )
    assert approve_resp.status_code == 200, approve_resp.text

    with _observer(seeded_db) as observer:
        record = observer.scalars(select(VerifiedOrderRecord).where(VerifiedOrderRecord.draft_id == draft_id)).one()
        record_snapshot = (record.id, record.order_number, record.grand_total_cents, record.line_items_snapshot_json)

    # 1. Attempt PATCH line on approved draft
    patch_resp = client.patch(
        f"/api/v1/drafts/{draft_id}/lines/{line_id}",
        json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"},
    )
    _assert_error(patch_resp, 409, "TerminalDraftConflictError")

    # 2. Attempt DELETE line on approved draft
    del_resp = client.delete(f"/api/v1/drafts/{draft_id}/lines/{line_id}")
    _assert_error(del_resp, 409, "TerminalDraftConflictError")

    # 3. Attempt POST reject on approved draft
    reject_resp = client.post(
        f"/api/v1/drafts/{draft_id}/reject",
        json={"operator_id": "op-sarah", "reason": "Cannot reject approved"},
    )
    _assert_error(reject_resp, 409, "TerminalDraftConflictError")

    with _observer(seeded_db) as observer:
        persisted = observer.get(OrderDraft, draft_id)
        assert persisted.status == "Approved"
        rec = observer.scalars(select(VerifiedOrderRecord).where(VerifiedOrderRecord.draft_id == draft_id)).one()
        assert (rec.id, rec.order_number, rec.grand_total_cents, rec.line_items_snapshot_json) == record_snapshot
        assert observer.scalars(
            select(AuditEvent).where(AuditEvent.draft_id == draft_id, AuditEvent.event_type == "DraftRejected")
        ).all() == []
