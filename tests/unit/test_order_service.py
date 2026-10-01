"""Durable P1 service intake/approval checks with controlled persistence faults."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import io
import json
from unittest.mock import Mock

import httpx
from pydantic import ValidationError
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.config import Settings
from app.models.entities import (
    AuditEvent, CatalogProduct, DiscrepancyFlag, DraftLineItem, FieldProvenance,
    OrderDraft, PurchaseOrderDocument, VerifiedOrderRecord,
)
from app.models.schemas import AIExtractionPayload, decimal_to_cents
from app.services.ai_provider import (
    AIOutputValidationError, AIProviderConfigurationError, AIProviderUnavailableError,
    FixtureAIProvider, LiveAIProvider,
)
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


@pytest.mark.parametrize(("fixture_id", "expected_flags"), [
    ("fixture-ambiguous-apex", {(1, "CatalogMatchingMismatch")}),
    ("fixture-discrepancy-apex", {(1, "PriceMismatch"), (2, "CatalogMatchingMismatch")}),
])
def test_nonclean_fixture_preserves_candidates_and_creates_expected_p2_flags(
    service_db, fixtures_dir, app_fixtures_dir, fixture_id, expected_flags,
):
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
            assert line.matched_sku == original["matched_sku"]
        flags = _rows(observer, DiscrepancyFlag)
        line_numbers = {line.id: line.line_number for line in draft.line_items}
        actual_flags = {
            (line_numbers[flag.line_item_id] if flag.line_item_id is not None else None, flag.discrepancy_type)
            for flag in flags
        }
        assert actual_flags == expected_flags
        assert len(flags) == len(expected_flags)
        assert all(flag.severity == "Blocking" for flag in flags)
        assert all(flag.resolution_state == "Unresolved" for flag in flags)


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


def test_fix1_qwen_owned_client_requires_endpoint_before_any_request(monkeypatch):
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    request_spy = Mock(side_effect=AssertionError("Configuration failure must precede HTTP"))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", request_spy)
    with pytest.raises(AIProviderConfigurationError, match="explicit workspace-specific") as captured:
        LiveAIProvider(settings=Settings(llm_provider="qwen", llm_api_key=""))
    assert isinstance(captured.value, AIProviderUnavailableError)
    request_spy.assert_not_called()


@pytest.mark.parametrize("source", ["argument", "environment"])
@pytest.mark.parametrize("base", [
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "http://training.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    "https://training.ap-southeast-1.maas.aliyuncs.com:444/compatible-mode/v1",
    "https://training.ap-southeast-1.maas.aliyuncs.com/wrong-path",
    "https://training.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1?value=unit-test-only",
    "https://training.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1#unit-test-only",
    "https://unit-test-only:unit-test-only@training.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    "https://training.eu-central-1.maas.aliyuncs.com/compatible-mode/v1",
    "",
])
def test_fix1_qwen_invalid_runtime_base_is_rejected_without_alternative_attempt(monkeypatch, source, base):
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    if source == "environment":
        monkeypatch.setenv("QWEN_BASE_URL", base)
    request_spy = Mock(side_effect=AssertionError("No endpoint probing or fallback permitted"))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", request_spy)
    with pytest.raises(AIProviderConfigurationError) as captured:
        LiveAIProvider(
            settings=Settings(llm_provider="qwen", llm_api_key=""),
            qwen_base_url=base if source == "argument" else None,
        )
    if base:
        assert base not in str(captured.value)
    assert "unit-test-only" not in str(captured.value)
    request_spy.assert_not_called()


@pytest.mark.parametrize("source,base", [
    ("argument", "https://training.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"),
    ("environment", "https://training.ap-southeast-1.maas.aliyuncs.com:443/compatible-mode/v1"),
])
def test_fix1_qwen_owned_client_uses_explicit_workspace_once(monkeypatch, clean_acme_fixture, source, base):
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    if source == "environment":
        monkeypatch.setenv("QWEN_BASE_URL", base)
    calls = []

    def controlled_transport(transport, request):
        calls.append(request)
        assert request.url.host == "training.ap-southeast-1.maas.aliyuncs.com"
        assert request.url.path == "/compatible-mode/v1/chat/completions"
        assert json.loads(request.content)["model"] == "qwen3.8-flash"
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(clean_acme_fixture["extraction"]),
        }}]})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", controlled_transport)
    provider = LiveAIProvider(
        settings=Settings(llm_provider="qwen", llm_api_key=""),
        qwen_base_url=base if source == "argument" else None,
    )
    assert provider.extract(raw_text="controlled source", catalog=[]) == AIExtractionPayload.model_validate(
        clean_acme_fixture["extraction"],
    )
    assert len(calls) == 1


def test_fix1_qwen_injected_mock_supports_frozen_seam_without_endpoint(monkeypatch, clean_acme_fixture):
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    calls = []

    def controlled_transport(request):
        calls.append(request)
        assert request.url.host == "test.ap-southeast-1.maas.aliyuncs.com"
        assert request.url.path == "/compatible-mode/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(clean_acme_fixture["extraction"]),
        }}]})

    with httpx.Client(transport=httpx.MockTransport(controlled_transport), trust_env=False) as client:
        provider = LiveAIProvider(settings=Settings(llm_provider="qwen", llm_api_key=""), client=client)
        provider.extract(raw_text="controlled source", catalog=[])
    assert len(calls) == 1


@pytest.mark.parametrize("mounted", [False, True])
def test_fix1_qwen_dummy_endpoint_cannot_use_injected_real_transport(monkeypatch, mounted):
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    request_spy = Mock(side_effect=AssertionError("Dummy endpoint must never reach a real transport"))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", request_spy)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("No mock request expected")) if mounted else None,
        mounts={"all://test.ap-southeast-1.maas.aliyuncs.com": httpx.HTTPTransport()} if mounted else None,
        trust_env=False,
    ) as client:
        with pytest.raises(AIProviderConfigurationError):
            LiveAIProvider(settings=Settings(llm_provider="qwen", llm_api_key=""), client=client)
    request_spy.assert_not_called()


def test_fix1_qwen_dummy_endpoint_rechecks_transport_before_send(monkeypatch):
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    request_spy = Mock(side_effect=AssertionError("Changed transport must be rejected before sending"))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", request_spy)
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("No request expected"))) as client:
        provider = LiveAIProvider(settings=Settings(llm_provider="qwen", llm_api_key=""), client=client)
        client._transport = httpx.HTTPTransport()
        with pytest.raises(AIProviderConfigurationError):
            provider.extract(raw_text="controlled source", catalog=[])
    request_spy.assert_not_called()


def test_fix1_qwen_owned_provider_failure_has_no_endpoint_retry_or_substitution(monkeypatch):
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    calls = []

    def controlled_failure(transport, request):
        calls.append(request)
        return httpx.Response(503)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", controlled_failure)
    fixture_spy = Mock(side_effect=AssertionError("No replay substitution permitted"))
    monkeypatch.setattr(FixtureAIProvider, "extract", fixture_spy)
    provider = LiveAIProvider(
        settings=Settings(llm_provider="qwen", llm_api_key=""),
        qwen_base_url="https://training.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    )
    with pytest.raises(AIProviderUnavailableError, match="HTTP 503"):
        provider.extract(raw_text="controlled source", catalog=[])
    assert len(calls) == 1
    assert calls[0].url.host == "training.ap-southeast-1.maas.aliyuncs.com"
    fixture_spy.assert_not_called()


def _assert_grounding_failure_before_inserts(db, document, provider):
    inserts = []

    def record_insert(mapper, connection, target):
        inserts.append(type(target).__name__)

    entities = (PurchaseOrderDocument, OrderDraft, DraftLineItem, FieldProvenance, AuditEvent)
    for entity in entities:
        event.listen(entity, "before_insert", record_insert)
    try:
        with pytest.raises(AIOutputValidationError, match="not grounded"):
            ingest_order(db, document=document, provider=provider)
    finally:
        for entity in entities:
            event.remove(entity, "before_insert", record_insert)
    assert inserts == []
    _assert_no_intake_state(db)


@pytest.mark.parametrize("group,field", [
    ("header", "customer_name"), ("header", "po_number"),
    *((line, field) for line in (0, 1) for field in (
        "customer_description", "extracted_quantity", "extracted_unit_price", "extracted_line_total",
    )),
])
def test_fix1_grounding_checks_each_of_ten_txt_records_before_persistence(service_db, document, live_provider, group, field):
    payload = live_provider.default_payload.model_dump(mode="python")
    evidence = (payload["header_provenance"] if group == "header" else
                payload["line_items"][group]["field_provenance"])[field]
    evidence["location"]["char_offset"] += 1
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)
    _assert_grounding_failure_before_inserts(service_db, document, live_provider)


@pytest.mark.parametrize("defect", ["wrong-existing-line", "missing-line", "past-line-end", "wrong-snippet", "cross-line"])
def test_fix1_grounding_rejects_txt_location_and_snippet_defects(service_db, document, live_provider, defect):
    payload = live_provider.default_payload.model_dump(mode="python")
    evidence = payload["header_provenance"]["customer_name"]
    if defect == "wrong-existing-line":
        evidence["location"]["line_number"] = 3
    elif defect == "missing-line":
        evidence["location"]["line_number"] = 999
    elif defect == "past-line-end":
        evidence["location"]["char_offset"] = len(document.line_spans[1].text)
    elif defect == "wrong-snippet":
        evidence["verbatim_snippet"] = "Acme Industrial SupplieX"
    else:
        # The canonical raw substring exists, but spans two TXT lines.
        evidence["verbatim_snippet"] += "\nAccount: CUST-ACME"
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)
    _assert_grounding_failure_before_inserts(service_db, document, live_provider)


def test_fix1_grounding_does_not_search_for_a_different_occurrence(service_db, document, live_provider):
    payload = live_provider.default_payload.model_dump(mode="python")
    # The name remains present at its original valid location elsewhere.
    payload["header_provenance"]["customer_name"]["location"]["line_number"] = 4
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)
    _assert_grounding_failure_before_inserts(service_db, document, live_provider)


@pytest.fixture
def pdf_grounding_case(document, clean_acme_fixture):
    """Parse a selectable-text PDF with a blank page and two actual text pages.

    Skipping the blank page makes page numbers differ from page_spans indices.
    Evidence positions derive from known source lines, without modifying fixtures.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    source_lines = document.raw_text.splitlines()
    for lines in (source_lines[:6], source_lines[6:]):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        commands = ["BT /F1 12 Tf 72 720 Td"]
        for index, line in enumerate(lines):
            if index:
                commands.append("0 -16 Td")
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj")
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = stream
    buffer = io.BytesIO()
    writer.write(buffer)
    parsed = parse_document(buffer.getvalue(), filename="grounding.pdf", content_type="application/pdf")
    assert [page.page_number for page in parsed.page_spans] == [2, 3]
    payload = deepcopy(clean_acme_fixture["extraction"])
    groups = [payload["header_provenance"], *(item["field_provenance"] for item in payload["line_items"])]
    for group in groups:
        for evidence in group.values():
            original = evidence["location"]
            source_line = source_lines[original["line_number"] - 1]
            page_number = 2 if original["line_number"] <= 6 else 3
            line_location = parsed.locate_snippet(source_line, page_number=page_number)
            assert line_location is not None
            start = line_location["char_start"] + original["char_offset"]
            evidence["location"] = {
                "type": "pdf", "page_number": page_number,
                "char_start": start, "char_end": start + len(evidence["verbatim_snippet"]),
            }
    return parsed, AIExtractionPayload.model_validate(payload)


def test_fix1_grounding_valid_pdf_persists_ten_exact_page_citations(service_db, live_provider, pdf_grounding_case):
    parsed, extraction = pdf_grounding_case
    live_provider.default_payload = extraction
    draft_id = ingest_order(service_db, document=parsed, provider=live_provider).id
    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, draft_id)
        assert draft.status == "Ready for Approval"
        assert draft.calculated_subtotal_cents == 35000
        assert draft.document.raw_text == parsed.raw_text
        assert len(draft.provenance_records) == 10
        for evidence in draft.provenance_records:
            location = json.loads(evidence.location_data_json)
            assert evidence.location_type == "pdf"
            page = next(span for span in parsed.page_spans if span.page_number == location["page_number"])
            assert page.char_start <= location["char_start"] < location["char_end"] <= page.char_end
            assert parsed.raw_text[location["char_start"]:location["char_end"]] == evidence.verbatim_snippet


@pytest.mark.parametrize("defect", [
    "missing-page", "blank-page", "wrong-existing-page", "before-page", "after-page",
    "wrong-offset", "wrong-snippet", "cross-page",
])
def test_fix1_grounding_rejects_pdf_page_span_and_snippet_defects(service_db, live_provider, pdf_grounding_case, defect):
    parsed, extraction = pdf_grounding_case
    payload = extraction.model_dump(mode="python")
    evidence = payload["line_items"][0]["field_provenance"]["customer_description"]
    location = evidence["location"]
    if defect in ("missing-page", "blank-page", "wrong-existing-page"):
        location["page_number"] = {"missing-page": 99, "blank-page": 1, "wrong-existing-page": 2}[defect]
    elif defect == "before-page":
        location["char_start"] = parsed.page_spans[1].char_start - 1
        location["char_end"] = location["char_start"] + len(evidence["verbatim_snippet"])
    elif defect == "after-page":
        location["char_end"] = parsed.page_spans[1].char_end + 1
    elif defect == "wrong-offset":
        location["char_start"] += 1
        location["char_end"] += 1
    elif defect == "wrong-snippet":
        evidence["verbatim_snippet"] = "unsubstantiated PDF evidence"
    else:
        location.update(page_number=2, char_start=parsed.page_spans[0].char_end - 1, char_end=parsed.page_spans[1].char_start + 1)
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)
    _assert_grounding_failure_before_inserts(service_db, parsed, live_provider)


@pytest.mark.parametrize("direction", ["pdf-location-on-txt", "txt-location-on-pdf"])
def test_fix1_grounding_rejects_both_cross_type_directions(service_db, document, live_provider, pdf_grounding_case, direction):
    parsed, extraction = pdf_grounding_case
    if direction == "pdf-location-on-txt":
        payload = live_provider.default_payload.model_dump(mode="python")
        payload["header_provenance"]["customer_name"]["location"] = (
            extraction.header_provenance.customer_name.location.model_dump(mode="json")
        )
        source = document
    else:
        payload = extraction.model_dump(mode="python")
        payload["header_provenance"]["customer_name"]["location"] = {
            "type": "txt", "line_number": 2, "char_offset": 10,
        }
        source = parsed
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)
    _assert_grounding_failure_before_inserts(service_db, source, live_provider)


@pytest.mark.parametrize("missing", ["customer_name", "extracted_quantity"])
def test_fix1_grounding_preserves_null_field_and_null_provenance_semantics(service_db, document, live_provider, missing):
    payload = live_provider.default_payload.model_dump(mode="python")
    if missing == "customer_name":
        payload[missing] = None
        payload["header_provenance"][missing] = None
    else:
        payload["line_items"][0][missing] = None
        payload["line_items"][0]["field_provenance"][missing] = None
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)
    draft_id = ingest_order(service_db, document=document, provider=live_provider).id
    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, draft_id)
        assert draft.status == "Needs Review"
        assert len(draft.provenance_records) == 9


# -----------------------------------------------------------------------------
# HG-P2-01 / VLD-P2-HG01R: Source-backed Order Total Contract & Persistence Tests
# -----------------------------------------------------------------------------

def test_extracted_order_total_schema_acceptance_valid(clean_acme_fixture):
    """Schema acceptance: present value + valid provenance, and absent value + absent provenance."""
    base_payload = clean_acme_fixture["extraction"]

    # 1. Valid: value present + valid provenance
    payload_present = deepcopy(base_payload)
    payload_present["extracted_order_total"] = "350.00"
    payload_present["header_provenance"]["extracted_order_total"] = {
        "field_name": "extracted_order_total",
        "verbatim_snippet": "$350.00",
        "location": {"type": "txt", "line_number": 13, "char_offset": 7},
    }
    model_present = AIExtractionPayload.model_validate(payload_present)
    assert model_present.extracted_order_total == Decimal("350.00")
    assert decimal_to_cents(model_present.extracted_order_total) == 35000
    assert model_present.header_provenance.extracted_order_total.field_name == "extracted_order_total"
    assert model_present.header_provenance.extracted_order_total.verbatim_snippet == "$350.00"
    # Serialized json string money
    dumped = json.loads(model_present.model_dump_json())
    assert dumped["extracted_order_total"] == "350.00"

    # 2. Valid: value absent + provenance absent (explicit None)
    payload_absent = deepcopy(base_payload)
    payload_absent["extracted_order_total"] = None
    payload_absent["header_provenance"]["extracted_order_total"] = None
    model_absent = AIExtractionPayload.model_validate(payload_absent)
    assert model_absent.extracted_order_total is None
    assert model_absent.header_provenance.extracted_order_total is None

    # 3. Valid: keys completely omitted (backward compatible with existing payloads)
    payload_omitted = deepcopy(base_payload)
    payload_omitted.pop("extracted_order_total", None)
    payload_omitted["header_provenance"].pop("extracted_order_total", None)
    model_omitted = AIExtractionPayload.model_validate(payload_omitted)
    assert model_omitted.extracted_order_total is None
    assert model_omitted.header_provenance.extracted_order_total is None


def test_extracted_order_total_schema_rejection_invalid(clean_acme_fixture):
    """Schema rejection: value without provenance, provenance without value, and invalid money."""
    base_payload = clean_acme_fixture["extraction"]

    # 1. Invalid: value present + provenance absent (None)
    payload_val_only = deepcopy(base_payload)
    payload_val_only["extracted_order_total"] = "350.00"
    payload_val_only["header_provenance"]["extracted_order_total"] = None
    with pytest.raises(ValidationError):
        AIExtractionPayload.model_validate(payload_val_only)

    # 2. Invalid: value present + provenance key completely omitted
    payload_val_no_prov_key = deepcopy(base_payload)
    payload_val_no_prov_key["extracted_order_total"] = "350.00"
    payload_val_no_prov_key["header_provenance"].pop("extracted_order_total", None)
    with pytest.raises(ValidationError):
        AIExtractionPayload.model_validate(payload_val_no_prov_key)

    # 3. Invalid: value absent + provenance present
    payload_prov_only = deepcopy(base_payload)
    payload_prov_only["extracted_order_total"] = None
    payload_prov_only["header_provenance"]["extracted_order_total"] = {
        "field_name": "extracted_order_total",
        "verbatim_snippet": "$350.00",
        "location": {"type": "txt", "line_number": 13, "char_offset": 7},
    }
    with pytest.raises(ValidationError):
        AIExtractionPayload.model_validate(payload_prov_only)

    # 4. Invalid money: float or non-exact
    for invalid_val in [350.0, "350.001", "-350.00", "invalid"]:
        payload_invalid_money = deepcopy(base_payload)
        payload_invalid_money["extracted_order_total"] = invalid_val
        payload_invalid_money["header_provenance"]["extracted_order_total"] = {
            "field_name": "extracted_order_total",
            "verbatim_snippet": "$350.00",
            "location": {"type": "txt", "line_number": 13, "char_offset": 7},
        }
        with pytest.raises(ValidationError):
            AIExtractionPayload.model_validate(payload_invalid_money)


def test_grounded_order_total_intake_persists_cents_and_header_provenance(service_db, document, live_provider):
    """Intake persists extracted_order_total_cents == 35000 and 1 header-level provenance record."""
    payload = live_provider.default_payload.model_dump(mode="python")
    payload["extracted_order_total"] = "350.00"
    payload["header_provenance"]["extracted_order_total"] = {
        "field_name": "extracted_order_total",
        "verbatim_snippet": "$350.00",
        "location": {"type": "txt", "line_number": 13, "char_offset": 7},
    }
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)

    draft_id = ingest_order(service_db, document=document, provider=live_provider).id

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, draft_id)
        assert draft.extracted_order_total_cents == 35000
        assert draft.calculated_subtotal_cents == 35000
        order_total_provenances = [
            p for p in draft.provenance_records if p.field_name == "extracted_order_total"
        ]
        assert len(order_total_provenances) == 1
        record = order_total_provenances[0]
        assert record.line_item_id is None
        assert record.verbatim_snippet == "$350.00"
        assert record.location_type == "txt"
        assert json.loads(record.location_data_json) == {"type": "txt", "line_number": 13, "char_offset": 7}
        # 3 header + 8 line = 11 total provenance records
        assert len(draft.provenance_records) == 11


def test_order_without_order_total_persists_none_cents_and_no_order_total_provenance(service_db, document, live_provider):
    """Extraction without order total persists extracted_order_total_cents = None and 0 order-total provenance records."""
    draft_id = ingest_order(service_db, document=document, provider=live_provider).id

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, draft_id)
        assert draft.extracted_order_total_cents is None
        assert draft.calculated_subtotal_cents == 35000
        order_total_provenances = [
            p for p in draft.provenance_records if p.field_name == "extracted_order_total"
        ]
        assert order_total_provenances == []
        # 2 header + 8 line = 10 total provenance records
        assert len(draft.provenance_records) == 10


def test_ungrounded_order_total_provenance_is_rejected_before_persistence(service_db, document, live_provider):
    """Ungrounded order total provenance fails intake validation before creating any DB records."""
    payload = live_provider.default_payload.model_dump(mode="python")
    payload["extracted_order_total"] = "350.00"
    payload["header_provenance"]["extracted_order_total"] = {
        "field_name": "extracted_order_total",
        "verbatim_snippet": "$350.00",
        "location": {"type": "txt", "line_number": 13, "char_offset": 99},  # invalid offset
    }
    live_provider.default_payload = AIExtractionPayload.model_validate(payload)

    with pytest.raises(AIOutputValidationError) as excinfo:
        ingest_order(service_db, document=document, provider=live_provider)

    assert "extracted_order_total is not grounded" in str(excinfo.value)
    _assert_no_intake_state(service_db)
