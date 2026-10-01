"""Focused P1 HTTP adapter checks beyond the frozen T016 lifecycle tests."""

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.models.entities import (
    AuditEvent, DiscrepancyFlag, DraftLineItem, FieldProvenance, OrderDraft,
    PurchaseOrderDocument, VerifiedOrderRecord,
)
from app.models.schemas import ErrorResponse


pytestmark = pytest.mark.usefixtures("no_external_network")
LIVE_URL = "/api/v1/orders/ingest"
REPLAY_URL = "/api/v1/fixtures/fixture-clean-acme/ingest"
REGISTERED_IDS = {
    "fixture-clean-acme", "fixture-discrepancy-apex", "fixture-ambiguous-apex",
}


@pytest.fixture
def seeded_db(db_session):
    seed_baseline(db_session)
    db_session.commit()
    return db_session


def _assert_empty_intake(db):
    engine = db.get_bind()
    db.close()
    with Session(engine) as observer:
        for entity in (
            PurchaseOrderDocument, OrderDraft, DraftLineItem, FieldProvenance,
            DiscrepancyFlag, AuditEvent, VerifiedOrderRecord,
        ):
            assert observer.scalars(select(entity)).all() == []


def _error(response, status, name=None):
    assert response.status_code == status, response.text
    error = ErrorResponse.model_validate(response.json())
    assert error.message.strip()
    if name is not None:
        assert error.error == name


@pytest.fixture
def forbid_live_provider(app_instance):
    from app.api.routes_orders import get_live_ai_provider

    forbidden = Mock(side_effect=AssertionError("Live provider must not be constructed"))
    app_instance.dependency_overrides[get_live_ai_provider] = forbidden
    try:
        yield forbidden
    finally:
        app_instance.dependency_overrides.pop(get_live_ai_provider, None)


def test_fixture_list_is_safe_and_replay_never_constructs_live_provider(
    client, seeded_db, forbid_live_provider, monkeypatch,
):
    from app.services.ai_provider import LiveAIProvider

    constructor = Mock(side_effect=AssertionError("Replay must not construct LiveAIProvider"))
    monkeypatch.setattr(LiveAIProvider, "__init__", constructor)
    response = client.get("/api/v1/fixtures")
    assert response.status_code == 200
    assert {item["fixture_id"] for item in response.json()} == REGISTERED_IDS
    for item in response.json():
        assert set(item) == {"fixture_id", "name", "description", "document_filename"}
    assert client.post(REPLAY_URL).json()["is_replay_mode"] is True
    constructor.assert_not_called()
    forbid_live_provider.assert_not_called()


def test_unknown_fixture_returns_404_without_persistence(client, seeded_db, forbid_live_provider):
    _error(client.post("/api/v1/fixtures/not-registered/ingest"), 404)
    _assert_empty_intake(seeded_db)
    forbid_live_provider.assert_not_called()


def _multipart(part):
    return b"--upload\r\n" + part + b"\r\n--upload--\r\n"


_FILE_PART = (
    b'Content-Disposition: form-data; name="file"; filename="po.txt"\r\n'
    b"Content-Type: text/plain\r\n\r\nPurchase order"
)


@pytest.mark.parametrize("content_type,body", [
    (None, b""),
    ("application/json", b"{}"),
    ("multipart/form-data", _multipart(_FILE_PART)),
    ("multipart/form-data; boundary=upload", b""),
    ("multipart/form-data; boundary=upload", b"not a MIME payload"),
    ("multipart/form-data; boundary=upload", b"--upload\r\n" + _FILE_PART),
    ("multipart/form-data; boundary=upload", _multipart(b'Content-Disposition: form-data; name="note"\r\n\r\nhello')),
    ("multipart/form-data; boundary=upload", _multipart(b'Content-Disposition: form-data; name="file"\r\n\r\nhello')),
    ("multipart/form-data; boundary=upload", _multipart(_FILE_PART + b"\r\n--upload\r\n" + _FILE_PART)),
    ("multipart/form-data; boundary=upload", _multipart(
        b'Content-Disposition: form-data; name="file"; filename="po.txt"\r\n'
        b'Content-Type: multipart/mixed; boundary=inner\r\n\r\n--inner\r\n'
        + _FILE_PART + b"\r\n--inner--"
    )),
], ids=["no-content-type", "wrong-content-type", "no-boundary", "empty-body", "invalid-body",
        "unclosed-body", "no-file", "no-filename", "multiple-files", "nested-multipart"])
def test_malformed_upload_returns_400_before_provider_or_persistence(
    client, seeded_db, forbid_live_provider, content_type, body,
):
    headers = {"content-type": content_type} if content_type is not None else {}
    _error(client.post(LIVE_URL, content=body, headers=headers), 400)
    forbid_live_provider.assert_not_called()
    _assert_empty_intake(seeded_db)


@pytest.mark.parametrize("filename,content,content_type", [
    ("file.exe", b"unsupported", "application/octet-stream"),
    ("po.txt", b"\xff", "text/plain"),
    ("po.txt", b"", "text/plain"),
    ("po.txt", b" \r\n\t", "text/plain"),
], ids=["unsupported", "invalid-utf8", "empty-document", "blank-document"])
def test_parser_failures_return_400_before_provider_or_persistence(
    client, seeded_db, forbid_live_provider, filename, content, content_type,
):
    _error(client.post(LIVE_URL, files={"file": (filename, content, content_type)}), 400)
    forbid_live_provider.assert_not_called()
    _assert_empty_intake(seeded_db)


def test_unextractable_pdf_returns_400_before_provider_or_persistence(
    client, seeded_db, forbid_live_provider, po_unextractable_pdf_path,
):
    path = po_unextractable_pdf_path
    _error(client.post(LIVE_URL, files={"file": (path.name, path.read_bytes(), "application/pdf")}),
           400, "UnextractableTextError")
    forbid_live_provider.assert_not_called()
    _assert_empty_intake(seeded_db)


def test_missing_qwen_endpoint_returns_503_without_fallback(
    client, seeded_db, monkeypatch, po_clean_acme_path,
):
    from app.api import routes_orders
    from app.config import Settings
    from app.services.ai_provider import FixtureAIProvider

    monkeypatch.setattr(routes_orders, "settings", Settings(llm_provider="qwen", llm_api_key=""))
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    fixture_call = Mock(side_effect=AssertionError("No automatic fixture substitution"))
    monkeypatch.setattr(FixtureAIProvider, "extract", fixture_call)
    path = po_clean_acme_path
    _error(client.post(LIVE_URL, files={"file": (path.name, path.read_bytes(), "text/plain")}),
           503, "AIProviderUnavailableError")
    fixture_call.assert_not_called()
    _assert_empty_intake(seeded_db)


def test_needs_review_approval_returns_409_without_order(client, seeded_db):
    draft = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest").json()
    assert draft["status"] == "Needs Review"
    assert draft["line_items"][0]["sku_name"] is None
    assert len(draft["line_items"][0]["candidate_skus"]) == 2
    _error(client.post(f"/api/v1/drafts/{draft['draft_id']}/approve", json={"operator_id": "op-sarah"}),
           409, "DraftNotReadyForApprovalError")
    assert seeded_db.scalars(select(VerifiedOrderRecord)).all() == []
    assert seeded_db.get(OrderDraft, draft["draft_id"]).status == "Needs Review"


def test_missing_draft_get_and_approval_return_404(client, seeded_db):
    _error(client.get("/api/v1/drafts/missing"), 404, "DraftNotFoundError")
    _error(client.post("/api/v1/drafts/missing/approve", json={"operator_id": "op-sarah"}),
           404, "DraftNotFoundError")
    _assert_empty_intake(seeded_db)


@pytest.mark.parametrize("body", [{}, {"operator_id": ""}, {"operator_id": "  "}, {"operator_id": 42}])
def test_invalid_operator_is_rejected_before_approval(client, seeded_db, body):
    draft = client.post(REPLAY_URL).json()
    _error(client.post(f"/api/v1/drafts/{draft['draft_id']}/approve", json=body),
           422, "RequestValidationError")
    assert seeded_db.get(OrderDraft, draft["draft_id"]).status == "Ready for Approval"
    assert seeded_db.scalars(select(VerifiedOrderRecord)).all() == []


@pytest.mark.parametrize("error_name,status", [("AIProviderUnavailableError", 503), ("AIOutputValidationError", 502)])
def test_provider_diagnostics_do_not_reflect_upstream_secrets(
    client, seeded_db, app_instance, po_clean_acme_path, error_name, status,
):
    from app.api.routes_orders import get_live_ai_provider
    from app.services import ai_provider

    provider = Mock(provider_name="qwen", model_name="qwen3.8-flash", is_replay_mode=False)
    provider.extract.side_effect = getattr(ai_provider, error_name)("PRIVATE_ENDPOINT PRIVATE_KEY PRIVATE_RESPONSE")
    app_instance.dependency_overrides[get_live_ai_provider] = lambda: provider
    try:
        path = po_clean_acme_path
        response = client.post(LIVE_URL, files={"file": (path.name, path.read_bytes(), "text/plain")})
        _error(response, status, error_name)
        assert "PRIVATE_" not in response.text
        provider.extract.assert_called_once()
        _assert_empty_intake(seeded_db)
    finally:
        app_instance.dependency_overrides.pop(get_live_ai_provider, None)


def test_draft_inspection_projects_persisted_state_without_recalculation(
    client, seeded_db, clean_acme_fixture, monkeypatch,
):
    from app.services import order_service, reconciliation

    draft_id = client.post(REPLAY_URL).json()["draft_id"]
    draft = seeded_db.get(OrderDraft, draft_id)
    draft.calculated_subtotal_cents = 12345
    lines = sorted(draft.line_items, key=lambda item: item.line_number)
    lines[0].line_number, lines[1].line_number = 2, 1
    lines[0].contract_price_cents = 0
    lines[0].extracted_quantity = None
    citation = next(item for item in lines[0].provenance_records if item.field_name == "extracted_quantity")
    seeded_db.delete(citation)
    seeded_db.add_all([
        DiscrepancyFlag(id=flag_id, draft_id=draft_id, line_item_id=lines[0].id,
                        discrepancy_type="PriceMismatch", severity="Blocking", expected_value="25.00",
                        requested_value="0.00", explanation="Persisted test discrepancy", resolution_state="Unresolved")
        for flag_id in ("flag-b", "flag-a")
    ])
    seeded_db.commit()
    seeded_db.expire_all()
    forbidden = Mock(side_effect=AssertionError("GET must not recalculate or persist"))
    monkeypatch.setattr(order_service, "ingest_order", forbidden)
    monkeypatch.setattr(reconciliation, "evaluate_clean_draft", forbidden)
    monkeypatch.setattr(seeded_db, "commit", forbidden)
    monkeypatch.setattr(seeded_db, "flush", forbidden)
    response = client.get(f"/api/v1/drafts/{draft_id}")
    assert response.status_code == 200
    inspected = response.json()
    assert inspected["calculated_subtotal"] == "123.45"
    assert inspected["status"] == "Ready for Approval"
    assert inspected["header_provenance"] == clean_acme_fixture["extraction"]["header_provenance"]
    assert [line["line_number"] for line in inspected["line_items"]] == [1, 2]
    line = inspected["line_items"][1]
    assert line["contract_price"] == "0.00"
    assert line["extracted_quantity"] is None
    assert line["field_provenance"]["extracted_quantity"] is None
    assert line["sku_name"] == "Industrial Stretch Film 18in 80ga"
    assert [flag["flag_id"] for flag in line["discrepancies"]] == ["flag-a", "flag-b"]
    assert not any(key.endswith("_cents") for key in line)
    forbidden.assert_not_called()


def test_verified_serializer_uses_snapshot_and_explicit_utc(client, seeded_db):
    from app.api.serialization import serialize_verified_order

    draft = client.post(REPLAY_URL).json()
    approved = client.post(f"/api/v1/drafts/{draft['draft_id']}/approve", json={"operator_id": "op-sarah"})
    record = seeded_db.get(VerifiedOrderRecord, approved.json()["order_id"])
    record.line_items_snapshot_json = json.dumps([{}])
    for timestamp in (
        datetime(2026, 10, 1, 12),
        datetime(2026, 10, 1, 15, tzinfo=timezone(timedelta(hours=3))),
    ):
        record.approved_at = timestamp
        result = serialize_verified_order(record)
        assert result["approved_at"] == "2026-10-01T12:00:00+00:00"
        assert result["line_items_count"] == 1
        assert result["grand_total"] == "350.00"
    seeded_db.rollback()


def test_application_factory_mounts_existing_static_tree_without_network():
    from app.main import create_app
    from fastapi.testclient import TestClient
    from starlette.routing import Mount

    app = create_app()
    mounts = [route for route in app.routes if isinstance(route, Mount)]
    assert [mount.path for mount in mounts] == ["/static"]
    assert app.user_middleware == []
    with TestClient(app) as client:
        assert client.get("/static/css/.gitkeep").status_code == 200
        assert client.get("/static/missing.css").status_code == 404
        # Root route redirects to SPA index.html via HTTP 307
        root_resp = client.get("/", follow_redirects=False)
        assert root_resp.status_code == 307
        assert root_resp.headers["location"] == "/static/index.html"
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404


def test_root_redirect_to_spa_index():
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        # GET / returns 307 redirect to /static/index.html
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/static/index.html"

        # Following redirect serves static SPA index.html with 200 OK
        followed = client.get("/", follow_redirects=True)
        assert followed.status_code == 200
        assert "OrderShield" in followed.text

        # /static/index.html remains directly available
        direct = client.get("/static/index.html")
        assert direct.status_code == 200
        assert "OrderShield" in direct.text
