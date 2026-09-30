"""Durable P1 service intake/approval checks with controlled persistence faults."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.config import Settings
from app.models.entities import (
    AuditEvent, CatalogProduct, DiscrepancyFlag, DraftLineItem, FieldProvenance,
    OrderDraft, PurchaseOrderDocument, VerifiedOrderRecord,
)
from app.models.schemas import AIExtractionPayload
from app.services.ai_provider import AIOutputValidationError, AIProviderUnavailableError, FixtureAIProvider, LiveAIProvider
from app.services.document_parser import parse_document
from app.services import order_service
from app.services.order_service import (
    DraftNotFoundError, DraftNotReadyForApprovalError, TerminalDraftStateError,
    approve_order, ingest_order,
)
from app.services.reconciliation import evaluate_clean_draft


@pytest.fixture(autouse=True)
def controlled_environment(no_external_network, monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(order_service, "datetime", FixedClock)


@pytest.fixture
def service_db(db_session):
    seed_baseline(db_session)
    db_session.commit()
    return db_session


@pytest.fixture
def document(po_clean_acme_path):
    return parse_document(po_clean_acme_path.read_bytes(), filename=po_clean_acme_path.name, content_type="text/plain")


@pytest.fixture
def live_provider(mock_ai_provider, clean_acme_fixture):
    mock_ai_provider.provider_name = "qwen"
    mock_ai_provider.model_name = "qwen3.8-flash"
    mock_ai_provider.is_replay_mode = False
    mock_ai_provider.default_payload = AIExtractionPayload.model_validate(deepcopy(clean_acme_fixture["extraction"]))
    return mock_ai_provider


def _observer(db):
    engine = db.get_bind()
    db.close()  # Roll back any uncommitted state before observing durability.
    return Session(bind=engine)


def _rows(db, entity):
    return db.scalars(select(entity)).all()


def _assert_no_intake_state(db):
    with _observer(db) as observer:
        for entity in (PurchaseOrderDocument, OrderDraft, DraftLineItem, FieldProvenance, AuditEvent, VerifiedOrderRecord):
            assert _rows(observer, entity) == [], entity.__name__
        assert len(_rows(observer, CatalogProduct)) == 12


@pytest.mark.parametrize("name,model", [("qwen", "qwen3.8-flash"), ("gemini", "gemini-3.5-flash-lite")])
def test_clean_live_intake_durably_persists_exact_fields_and_actual_provider(service_db, document, live_provider, name, model):
    live_provider.provider_name, live_provider.model_name = name, model
    draft_id = ingest_order(service_db, document=document, provider=live_provider).id

    assert live_provider.call_count == 1
    assert live_provider.last_call_kwargs["raw_text"] == document.raw_text
    assert len(live_provider.last_call_kwargs["catalog"]) == 12
    assert all("base_price_cents" not in product for product in live_provider.last_call_kwargs["catalog"])
    with _observer(service_db) as observer:
        assert len(_rows(observer, PurchaseOrderDocument)) == 1
        draft = observer.get(OrderDraft, draft_id)
        assert len(_rows(observer, OrderDraft)) == 1
        assert draft.document.raw_text == document.raw_text
        assert (draft.document.filename, draft.document.content_type, draft.document.status) == (
            "po_clean_acme.txt", "text/plain", "Ingested",
        )
        assert (draft.customer_id, draft.customer_name_extracted, draft.po_number_extracted) == (
            "CUST-ACME", "Acme Industrial Supplies", "PO-10023",
        )
        assert draft.is_replay_mode is False
        assert draft.status == "Ready for Approval"
        assert draft.calculated_subtotal_cents == 35000
        lines = sorted(draft.line_items, key=lambda line: line.line_number)
        assert len(lines) == 2
        assert [(line.extracted_unit_price_cents, line.extracted_line_total_cents,
                 line.contract_price_cents, line.calculated_line_total_cents) for line in lines] == [
            (2500, 25000, 2500, 25000), (2000, 10000, 2000, 10000),
        ]
        assert all(line.status == "Active" and line.sku_resolution_source == "AI_HIGH_CONFIDENCE" for line in lines)
        extraction_event = next(e for e in draft.audit_events if e.event_type == "AIExtractionCompleted")
        assert extraction_event.actor == "AIProvider"
        assert json.loads(extraction_event.details_json) == {"provider": name, "model": model}
        assert sorted(e.event_type for e in draft.audit_events) == ["AIExtractionCompleted", "DocumentIngested"]
        assert _rows(observer, DiscrepancyFlag) == []


def test_explicit_fixture_replay_persists_ten_unmodified_provenance_records(service_db, document, clean_acme_fixture):
    provider = FixtureAIProvider(fixture_id="fixture-clean-acme")
    draft_id = ingest_order(service_db, document=document, provider=provider).id
    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, draft_id)
        assert draft.is_replay_mode is True
        assert draft.status == "Ready for Approval"
        assert draft.calculated_subtotal_cents == 35000
        evidence = _rows(observer, FieldProvenance)
        assert len(evidence) == 10
        expected = clean_acme_fixture["extraction"]
        by_number = {line.id: line.line_number for line in draft.line_items}
        for row in evidence:
            original = (expected["header_provenance"] if row.line_item_id is None else
                        expected["line_items"][by_number[row.line_item_id] - 1]["field_provenance"])[row.field_name]
            assert row.draft_id == draft_id
            assert row.verbatim_snippet == original["verbatim_snippet"]
            assert row.location_type == original["location"]["type"]
            assert json.loads(row.location_data_json) == original["location"]
        audit = next(e for e in draft.audit_events if e.event_type == "AIExtractionCompleted")
        assert json.loads(audit.details_json) == {"provider": "fixture", "model": "pre-verified-dataset"}


def test_intake_preserves_canonical_crlf_text_without_renormalizing(service_db, document, live_provider):
    canonical = document.raw_text.replace("\n", "\r\n") + "\r\n"
    parsed = parse_document(canonical.encode("utf-8"), filename="canonical.txt", content_type="text/plain")
    draft_id = ingest_order(service_db, document=parsed, provider=live_provider).id
    with _observer(service_db) as observer:
        assert observer.get(OrderDraft, draft_id).document.raw_text == canonical
    assert live_provider.last_call_kwargs["raw_text"] == canonical


@pytest.mark.parametrize("fixture_id", ["fixture-ambiguous-apex", "fixture-discrepancy-apex"])
def test_nonclean_fixture_keeps_candidates_as_valid_json_without_p2_flags(service_db, fixtures_dir, app_fixtures_dir, fixture_id):
    filename = "ambiguous_apex.json" if "ambiguous" in fixture_id else "discrepancy_apex.json"
    dataset = json.loads((app_fixtures_dir / filename).read_text(encoding="utf-8"))
    parsed = parse_document(fixtures_dir / dataset["document_filename"])
    draft_id = ingest_order(service_db, document=parsed, provider=FixtureAIProvider(fixture_id=fixture_id)).id
    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, draft_id)
        assert draft.status == "Needs Review"
        for line in draft.line_items:
            original = dataset["extraction"]["line_items"][line.line_number - 1]
            assert json.loads(line.candidate_skus_json) == original["candidate_skus"]
            assert line.matching_rationale == original["matching_rationale"]
        assert _rows(observer, DiscrepancyFlag) == []


@pytest.mark.parametrize("error_type", [AIProviderUnavailableError, AIOutputValidationError])
def test_provider_failure_propagates_without_partial_state_or_replay(service_db, document, live_provider, error_type, monkeypatch):
    failure = error_type("Controlled provider failure")
    live_provider.extract = Mock(side_effect=failure)
    fixture_spy = Mock(side_effect=AssertionError("No fixture substitution permitted"))
    monkeypatch.setattr(FixtureAIProvider, "extract", fixture_spy)
    with pytest.raises(error_type) as captured:
        ingest_order(service_db, document=document, provider=live_provider)
    assert captured.value is failure
    live_provider.extract.assert_called_once()
    fixture_spy.assert_not_called()
    _assert_no_intake_state(service_db)


@pytest.mark.parametrize("kind", ["invalid-dict", "unvalidated-model"])
def test_invalid_extraction_cannot_start_persistence(service_db, document, live_provider, kind):
    live_provider.default_payload = (
        {"unexpected_field": 123} if kind == "invalid-dict" else
        AIExtractionPayload.model_construct(customer_name="unvalidated")
    )
    with pytest.raises(AIOutputValidationError):
        ingest_order(service_db, document=document, provider=live_provider)
    assert live_provider.call_count == 1
    _assert_no_intake_state(service_db)


@pytest.mark.parametrize("entity", [PurchaseOrderDocument, DraftLineItem, FieldProvenance, AuditEvent])
def test_intake_persistence_failure_rolls_back_all_entities_and_allows_retry(service_db, document, live_provider, entity):
    calls = []

    def fail_insert(mapper, connection, target):
        calls.append(target)
        raise RuntimeError("Controlled intake write failure")

    event.listen(entity, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="Controlled intake write failure"):
            ingest_order(service_db, document=document, provider=live_provider)
    finally:
        event.remove(entity, "before_insert", fail_insert)
    assert calls
    _assert_no_intake_state(service_db)
    draft_id = ingest_order(service_db, document=document, provider=live_provider).id
    with _observer(service_db) as observer:
        assert observer.get(OrderDraft, draft_id).status == "Ready for Approval"
        assert len(_rows(observer, OrderDraft)) == 1
        assert len(_rows(observer, FieldProvenance)) == 10


@pytest.mark.parametrize("replay", [False, True])
def test_ready_approval_creates_one_durable_record_status_audit_and_commercial_snapshot(service_db, document, live_provider, replay):
    provider = FixtureAIProvider(fixture_id="fixture-clean-acme") if replay else live_provider
    draft_id = ingest_order(service_db, document=document, provider=provider).id
    order_id = approve_order(service_db, draft_id=draft_id, operator_id="op-sarah").id

    with _observer(service_db) as observer:
        assert observer.get(OrderDraft, draft_id).status == "Approved"
        assert len(_rows(observer, VerifiedOrderRecord)) == 1
        record = observer.get(VerifiedOrderRecord, order_id)
        assert record.draft_id == draft_id
        assert record.order_number == "VO-2026-0001"
        assert (record.customer_id, record.po_number, record.grand_total_cents, record.approved_by) == (
            "CUST-ACME", "PO-10023", 35000, "op-sarah",
        )
        assert record.approved_at.replace(tzinfo=timezone.utc) == datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
        assert record.is_replay_mode is replay
        assert json.loads(record.line_items_snapshot_json) == [
            {"line_number": 1, "sku": "SKU-WRAP-18", "quantity": 10, "contract_price_cents": 2500,
             "calculated_line_total_cents": 25000, "sku_resolution_source": "AI_HIGH_CONFIDENCE"},
            {"line_number": 2, "sku": "SKU-WRAP-15", "quantity": 5, "contract_price_cents": 2000,
             "calculated_line_total_cents": 10000, "sku_resolution_source": "AI_HIGH_CONFIDENCE"},
        ]
        approvals = [e for e in _rows(observer, AuditEvent) if e.event_type == "OrderApproved"]
        assert len(approvals) == 1
        assert approvals[0].draft_id == draft_id
        assert approvals[0].actor == "Operator"
        assert json.loads(approvals[0].details_json) == {"operator_id": "op-sarah", "order_number": "VO-2026-0001"}
    if not replay:
        assert live_provider.call_count == 1  # Approval never invokes AI.


def test_repeated_approval_rejects_without_mutating_record_or_audit(service_db, document, live_provider):
    draft_id = ingest_order(service_db, document=document, provider=live_provider).id
    approve_order(service_db, draft_id=draft_id, operator_id="op-sarah")
    with _observer(service_db) as observer:
        record = _rows(observer, VerifiedOrderRecord)[0]
        before = (record.id, record.order_number, record.approved_by, record.approved_at,
                  record.grand_total_cents, record.line_items_snapshot_json)
    with pytest.raises(TerminalDraftStateError):
        approve_order(service_db, draft_id=draft_id, operator_id="op-other")
    with _observer(service_db) as observer:
        records = _rows(observer, VerifiedOrderRecord)
        assert len(records) == 1
        record = records[0]
        assert (record.id, record.order_number, record.approved_by, record.approved_at,
                record.grand_total_cents, record.line_items_snapshot_json) == before
        assert len([e for e in _rows(observer, AuditEvent) if e.event_type == "OrderApproved"]) == 1
        assert observer.get(OrderDraft, draft_id).status == "Approved"


@pytest.mark.parametrize("status,error", [
    ("Ingested", DraftNotReadyForApprovalError), ("Needs Review", DraftNotReadyForApprovalError),
    ("Approved", TerminalDraftStateError), ("Rejected", TerminalDraftStateError),
])
def test_approval_preconditions_do_not_mutate_draft(service_db, document, live_provider, status, error):
    draft = ingest_order(service_db, document=document, provider=live_provider)
    draft.status = status
    service_db.commit()
    draft_id = draft.id
    with pytest.raises(error):
        approve_order(service_db, draft_id=draft_id, operator_id="op-sarah")
    with _observer(service_db) as observer:
        assert observer.get(OrderDraft, draft_id).status == status
        assert _rows(observer, VerifiedOrderRecord) == []
        assert [e for e in _rows(observer, AuditEvent) if e.event_type == "OrderApproved"] == []


@pytest.mark.parametrize("entity", [VerifiedOrderRecord, AuditEvent])
def test_approval_insert_failure_rolls_back_and_retry_uses_first_number(service_db, document, live_provider, entity):
    draft_id = ingest_order(service_db, document=document, provider=live_provider).id
    calls = []

    def fail_insert(mapper, connection, target):
        if isinstance(target, VerifiedOrderRecord) or target.event_type == "OrderApproved":
            calls.append(target)
            raise RuntimeError("Controlled approval write failure")

    event.listen(entity, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="Controlled approval write failure"):
            approve_order(service_db, draft_id=draft_id, operator_id="op-sarah")
    finally:
        event.remove(entity, "before_insert", fail_insert)
    assert calls
    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, draft_id)
        assert draft.status == "Ready for Approval"
        assert draft.calculated_subtotal_cents == 35000
        assert len(draft.line_items) == 2
        assert _rows(observer, VerifiedOrderRecord) == []
        assert [e for e in _rows(observer, AuditEvent) if e.event_type == "OrderApproved"] == []
    record_id = approve_order(service_db, draft_id=draft_id, operator_id="op-sarah").id
    with _observer(service_db) as observer:
        assert observer.get(VerifiedOrderRecord, record_id).order_number == "VO-2026-0001"
        assert observer.get(OrderDraft, draft_id).status == "Approved"


@pytest.mark.parametrize("operation", ["intake", "approval"])
def test_commit_failure_after_flush_rolls_back_all_operation_writes(service_db, document, live_provider, monkeypatch, operation):
    draft_id = None
    if operation == "approval":
        draft_id = ingest_order(service_db, document=document, provider=live_provider).id
    with monkeypatch.context() as patch:
        patch.setattr(service_db, "commit", Mock(side_effect=RuntimeError("Controlled commit failure")))
        with pytest.raises(RuntimeError, match="Controlled commit failure"):
            if operation == "intake":
                ingest_order(service_db, document=document, provider=live_provider)
            else:
                approve_order(service_db, draft_id=draft_id, operator_id="op-sarah")
    if operation == "intake":
        _assert_no_intake_state(service_db)
    else:
        with _observer(service_db) as observer:
            assert observer.get(OrderDraft, draft_id).status == "Ready for Approval"
            assert _rows(observer, VerifiedOrderRecord) == []
            assert [e for e in _rows(observer, AuditEvent) if e.event_type == "OrderApproved"] == []
        assert approve_order(service_db, draft_id=draft_id, operator_id="op-sarah").order_number == "VO-2026-0001"


def test_successive_approvals_have_unique_deterministic_order_numbers(service_db, document, live_provider):
    numbers = []
    for _ in range(3):
        draft_id = ingest_order(service_db, document=document, provider=live_provider).id
        numbers.append(approve_order(service_db, draft_id=draft_id, operator_id="op-sarah").order_number)
    assert numbers == ["VO-2026-0001", "VO-2026-0002", "VO-2026-0003"]
    with _observer(service_db) as observer:
        assert len(_rows(observer, VerifiedOrderRecord)) == 3


def test_snapshot_sorts_lines_even_when_extraction_is_reversed(service_db, document, live_provider):
    payload = live_provider.default_payload.model_dump(mode="python")
    payload["line_items"].reverse()
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)
    draft_id = ingest_order(service_db, document=document, provider=live_provider).id
    record = approve_order(service_db, draft_id=draft_id, operator_id="op-sarah")
    assert [line["line_number"] for line in json.loads(record.line_items_snapshot_json)] == [1, 2]


def test_snapshot_excludes_removed_lines(service_db, document, live_provider):
    draft = ingest_order(service_db, document=document, provider=live_provider)
    draft.line_items[1].status = "Removed"
    evaluate_clean_draft(service_db, draft)
    service_db.commit()
    record = approve_order(service_db, draft_id=draft.id, operator_id="op-sarah")
    assert record.grand_total_cents == 25000
    assert [line["line_number"] for line in json.loads(record.line_items_snapshot_json)] == [1]


def test_missing_draft_is_an_explicit_lookup_error(service_db):
    with pytest.raises(DraftNotFoundError):
        approve_order(service_db, draft_id="missing", operator_id="op-sarah")


@pytest.mark.parametrize("name", ["qwen", "gemini"])
def test_controlled_live_transport_intake_uses_governed_payload_and_bounded_request(service_db, document, clean_acme_fixture, name):
    calls = []

    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        if name == "qwen":
            assert request.url.path == "/compatible-mode/v1/chat/completions"
            assert request.url.host == "training.ap-southeast-1.maas.aliyuncs.com"
            assert body["model"] == "qwen3.8-flash"
            assert body["enable_thinking"] is False
            assert body["stream"] is False
            assert body["response_format"] == {"type": "json_object"}
            prompt = body["messages"][1]["content"]
            envelope = {"choices": [{"message": {"content": json.dumps(clean_acme_fixture["extraction"])}}]}
        else:
            assert request.url.path == "/v1beta/models/gemini-3.5-flash-lite:generateContent"
            assert request.url.host == "generativelanguage.googleapis.com"
            config = body["generationConfig"]
            assert config["thinkingConfig"] == {"thinkingLevel": "minimal"}
            assert config["responseFormat"]["text"] == {
                "mimeType": "APPLICATION_JSON", "schema": AIExtractionPayload.model_json_schema(),
            }
            prompt = body["contents"][0]["parts"][0]["text"]
            envelope = {"candidates": [{"content": {"parts": [{"text": json.dumps(clean_acme_fixture["extraction"])}]}}]}
        assert document.raw_text in prompt
        assert "base_price" not in prompt and "min_order_quantity" not in prompt
        assert all(0 < timeout <= 15 for timeout in request.extensions["timeout"].values())
        return httpx.Response(200, json=envelope)

    with httpx.Client(transport=httpx.MockTransport(respond), timeout=None, follow_redirects=True) as client:
        provider = LiveAIProvider(
            settings=Settings(llm_provider=name, llm_api_key="", live_inference_timeout=20), client=client,
            qwen_base_url="https://training.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        )
        draft_id = ingest_order(service_db, document=document, provider=provider).id
    assert len(calls) == 1
    with _observer(service_db) as observer:
        assert observer.get(OrderDraft, draft_id).status == "Ready for Approval"


def test_redirect_failure_is_single_attempt_and_diagnostics_do_not_include_key(service_db, document):
    calls = []
    synthetic_key = "unit-test-only"

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://example.invalid/redirect"}, text=synthetic_key)

    with httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
        provider = LiveAIProvider(settings=Settings(llm_provider="gemini", llm_api_key=synthetic_key), client=client)
        with pytest.raises(AIProviderUnavailableError, match="HTTP 302") as captured:
            ingest_order(service_db, document=document, provider=provider)
    assert synthetic_key not in str(captured.value)
    assert len(calls) == 1
    _assert_no_intake_state(service_db)


def test_malformed_provider_envelope_is_validation_failure_without_persistence(service_db, document):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"candidates": [{"content": {"parts": ["not an object"]}}]},
    ))) as client:
        provider = LiveAIProvider(settings=Settings(llm_provider="gemini", llm_api_key=""), client=client)
        with pytest.raises(AIOutputValidationError):
            ingest_order(service_db, document=document, provider=provider)
    _assert_no_intake_state(service_db)
