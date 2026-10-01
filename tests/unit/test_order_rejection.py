"""Unit tests for service-level atomic draft rejection (T032)."""

from copy import deepcopy
from datetime import datetime, timezone
import json
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
from app.models.schemas import AIExtractionPayload
from app.services.document_parser import parse_document
from app.services import order_service
from app.services.order_service import (
    DraftNotFoundError,
    TerminalDraftStateError,
    approve_order,
    ingest_order,
    reject_order,
)


@pytest.fixture(autouse=True)
def controlled_environment(no_external_network, monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 1, 10, 30, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(order_service, "datetime", FixedClock)


@pytest.fixture
def service_db(db_session):
    seed_baseline(db_session)
    db_session.commit()
    return db_session


@pytest.fixture
def clean_document(po_clean_acme_path):
    return parse_document(
        po_clean_acme_path.read_bytes(),
        filename=po_clean_acme_path.name,
        content_type="text/plain",
    )


@pytest.fixture
def discrepancy_document(po_discrepancy_apex_path):
    return parse_document(
        po_discrepancy_apex_path.read_bytes(),
        filename=po_discrepancy_apex_path.name,
        content_type="text/plain",
    )


@pytest.fixture
def ready_draft_id(service_db, clean_document, mock_ai_provider, clean_acme_fixture):
    mock_ai_provider.provider_name = "qwen"
    mock_ai_provider.model_name = "qwen3.8-flash"
    mock_ai_provider.is_replay_mode = False
    mock_ai_provider.default_payload = AIExtractionPayload.model_validate(
        deepcopy(clean_acme_fixture["extraction"])
    )
    draft = ingest_order(service_db, document=clean_document, provider=mock_ai_provider)
    assert draft.status == "Ready for Approval"
    return draft.id


@pytest.fixture
def needs_review_draft_id(service_db, discrepancy_document, mock_ai_provider, discrepancy_apex_fixture):
    mock_ai_provider.provider_name = "qwen"
    mock_ai_provider.model_name = "qwen3.8-flash"
    mock_ai_provider.is_replay_mode = False
    mock_ai_provider.default_payload = AIExtractionPayload.model_validate(
        deepcopy(discrepancy_apex_fixture["extraction"])
    )
    draft = ingest_order(service_db, document=discrepancy_document, provider=mock_ai_provider)
    assert draft.status == "Needs Review"
    return draft.id


def _observer(db: Session) -> Session:
    engine = db.get_bind()
    db.close()
    return Session(bind=engine)


def _rows(db: Session, entity):
    return db.scalars(select(entity)).all()


def test_needs_review_to_rejected(service_db, needs_review_draft_id):
    """Test 1: Needs Review -> Rejected transitions draft, persists reason and audit event."""
    reason = "Customer cancelled order due to pricing dispute"
    operator_id = "op-vladimir"

    result = reject_order(
        service_db,
        draft_id=needs_review_draft_id,
        operator_id=operator_id,
        reason=reason,
    )
    assert result.id == needs_review_draft_id
    assert result.status == "Rejected"
    assert result.rejection_reason == reason

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, needs_review_draft_id)
        assert draft.status == "Rejected"
        assert draft.rejection_reason == reason

        # Verify exactly one DraftRejected audit event
        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == needs_review_draft_id and e.event_type == "DraftRejected"]
        assert len(events) == 1
        event_record = events[0]
        assert event_record.actor == "Operator"
        details = json.loads(event_record.details_json)
        assert details == {"operator_id": operator_id, "reason": reason}


def test_ready_for_approval_to_rejected(service_db, ready_draft_id):
    """Test 2: Ready for Approval -> Rejected transitions draft with same guarantees."""
    reason = "Supplier stock shortage; order cancelled"
    operator_id = "op-sarah"

    result = reject_order(
        service_db,
        draft_id=ready_draft_id,
        operator_id=operator_id,
        reason=reason,
    )
    assert result.id == ready_draft_id
    assert result.status == "Rejected"
    assert result.rejection_reason == reason

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, ready_draft_id)
        assert draft.status == "Rejected"
        assert draft.rejection_reason == reason

        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert len(events) == 1
        assert events[0].actor == "Operator"
        details = json.loads(events[0].details_json)
        assert details == {"operator_id": operator_id, "reason": reason}


def test_audit_provenance_and_deterministic_json(service_db, ready_draft_id):
    """Test 3: Audit event provenance has event_type, actor, timestamp, deterministic JSON."""
    reason = "PO was duplicate of previously processed order"
    operator_id = "op-reviewer-42"

    reject_order(
        service_db,
        draft_id=ready_draft_id,
        operator_id=operator_id,
        reason=reason,
    )

    with _observer(service_db) as observer:
        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert len(events) == 1
        event_record = events[0]
        assert event_record.event_type == "DraftRejected"
        assert event_record.actor == "Operator"
        # Deterministic JSON check (sort_keys, separators)
        expected_json = json.dumps({"operator_id": operator_id, "reason": reason}, sort_keys=True, separators=(",", ":"))
        assert event_record.details_json == expected_json
        assert event_record.timestamp.replace(tzinfo=timezone.utc) == datetime(2026, 10, 1, 10, 30, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("blank_reason", ["", "   ", "\t\n  ", None, 12345])
def test_blank_or_invalid_reason_fails_atomically(service_db, ready_draft_id, blank_reason):
    """Test 4: Blank or non-string reason fails validation without mutating durable state."""
    with pytest.raises((ValueError, TypeError)):
        reject_order(
            service_db,
            draft_id=ready_draft_id,
            operator_id="op-sarah",
            reason=blank_reason,  # type: ignore
        )

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, ready_draft_id)
        assert draft.status == "Ready for Approval"
        assert draft.rejection_reason is None
        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert events == []


@pytest.mark.parametrize("blank_operator", ["", "   ", "\t\n  ", None, 999])
def test_blank_or_invalid_operator_fails_atomically(service_db, ready_draft_id, blank_operator):
    """Test 5: Blank or non-string operator fails validation without mutating durable state."""
    with pytest.raises((ValueError, TypeError)):
        reject_order(
            service_db,
            draft_id=ready_draft_id,
            operator_id=blank_operator,  # type: ignore
            reason="Valid rejection reason",
        )

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, ready_draft_id)
        assert draft.status == "Ready for Approval"
        assert draft.rejection_reason is None
        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert events == []


def test_missing_draft_raises_draft_not_found(service_db):
    """Test 6: Missing draft_id raises DraftNotFoundError with no side effects."""
    with pytest.raises(DraftNotFoundError):
        reject_order(
            service_db,
            draft_id="non-existent-draft-id",
            operator_id="op-sarah",
            reason="Order rejected",
        )

    with _observer(service_db) as observer:
        events = [e for e in _rows(observer, AuditEvent) if e.event_type == "DraftRejected"]
        assert events == []


def test_repeated_rejection_fails_closed(service_db, ready_draft_id):
    """Test 7: Repeated rejection raises TerminalDraftStateError without mutating state."""
    initial_reason = "First valid rejection reason"
    initial_operator = "op-initial"

    reject_order(
        service_db,
        draft_id=ready_draft_id,
        operator_id=initial_operator,
        reason=initial_reason,
    )

    with _observer(service_db) as observer:
        initial_events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert len(initial_events) == 1
        initial_event_id = initial_events[0].id

    # Attempt rejection a second time
    with pytest.raises(TerminalDraftStateError):
        reject_order(
            service_db,
            draft_id=ready_draft_id,
            operator_id="op-second-attempt",
            reason="Second different reason",
        )

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, ready_draft_id)
        assert draft.status == "Rejected"
        assert draft.rejection_reason == initial_reason

        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert len(events) == 1
        assert events[0].id == initial_event_id
        details = json.loads(events[0].details_json)
        assert details == {"operator_id": initial_operator, "reason": initial_reason}


def test_approved_draft_cannot_be_rejected(service_db, ready_draft_id):
    """Test 8: Approved draft with VerifiedOrderRecord raises TerminalDraftStateError and is not mutated."""
    record = approve_order(service_db, draft_id=ready_draft_id, operator_id="op-approver")
    order_number = record.order_number

    with _observer(service_db) as observer:
        existing_record = observer.get(VerifiedOrderRecord, record.id)
        before_snapshot = (
            existing_record.id,
            existing_record.order_number,
            existing_record.approved_by,
            existing_record.approved_at,
            existing_record.grand_total_cents,
            existing_record.line_items_snapshot_json,
        )

    # Attempt rejection on Approved draft
    with pytest.raises(TerminalDraftStateError):
        reject_order(
            service_db,
            draft_id=ready_draft_id,
            operator_id="op-rejecter",
            reason="Late rejection attempt",
        )

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, ready_draft_id)
        assert draft.status == "Approved"
        assert draft.rejection_reason is None

        # VerifiedOrderRecord remains unchanged
        verified_records = _rows(observer, VerifiedOrderRecord)
        assert len(verified_records) == 1
        current_record = verified_records[0]
        after_snapshot = (
            current_record.id,
            current_record.order_number,
            current_record.approved_by,
            current_record.approved_at,
            current_record.grand_total_cents,
            current_record.line_items_snapshot_json,
        )
        assert after_snapshot == before_snapshot

        # No DraftRejected audit event was appended
        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert events == []


def test_ingested_draft_can_be_rejected(service_db, clean_document, mock_ai_provider, clean_acme_fixture):
    """Test: Ingested draft can also be rejected."""
    mock_ai_provider.provider_name = "qwen"
    mock_ai_provider.model_name = "qwen3.8-flash"
    mock_ai_provider.is_replay_mode = False
    mock_ai_provider.default_payload = AIExtractionPayload.model_validate(
        deepcopy(clean_acme_fixture["extraction"])
    )
    draft = ingest_order(service_db, document=clean_document, provider=mock_ai_provider)
    # Manually revert status to Ingested to test non-terminal Ingested state explicitly
    draft.status = "Ingested"
    service_db.commit()

    draft_id = draft.id
    reject_order(
        service_db,
        draft_id=draft_id,
        operator_id="op-triage",
        reason="Rejected during initial triage",
    )

    with _observer(service_db) as observer:
        reloaded = observer.get(OrderDraft, draft_id)
        assert reloaded.status == "Rejected"
        assert reloaded.rejection_reason == "Rejected during initial triage"


def test_rejection_never_creates_verified_order_record(service_db, ready_draft_id):
    """Test: Successful rejection creates zero VerifiedOrderRecord rows."""
    reject_order(
        service_db,
        draft_id=ready_draft_id,
        operator_id="op-sarah",
        reason="No verified order should ever exist for this",
    )

    with _observer(service_db) as observer:
        records = observer.scalars(
            select(VerifiedOrderRecord).where(VerifiedOrderRecord.draft_id == ready_draft_id)
        ).all()
        assert records == []


def test_rejection_does_not_modify_discrepancy_flags(service_db, needs_review_draft_id):
    """Test: Discrepancy flags are preserved as historical evidence and NOT modified or resolved."""
    with _observer(service_db) as observer:
        flags_before = [
            (f.id, f.discrepancy_type, f.severity, f.expected_value, f.requested_value, f.resolution_state)
            for f in _rows(observer, DiscrepancyFlag)
            if f.draft_id == needs_review_draft_id
        ]
        assert len(flags_before) > 0

    reject_order(
        service_db,
        draft_id=needs_review_draft_id,
        operator_id="op-auditor",
        reason="Rejected without resolving discrepancies",
    )

    with _observer(service_db) as observer:
        flags_after = [
            (f.id, f.discrepancy_type, f.severity, f.expected_value, f.requested_value, f.resolution_state)
            for f in _rows(observer, DiscrepancyFlag)
            if f.draft_id == needs_review_draft_id
        ]
        assert flags_after == flags_before
        for flag in _rows(observer, DiscrepancyFlag):
            if flag.draft_id == needs_review_draft_id:
                assert flag.resolution_state == "Unresolved"


def test_rejection_insert_failure_rolls_back_atomically(service_db, ready_draft_id):
    """Test: Database failure during rejection rollback leaves draft non-terminal without audit event."""
    calls = []

    def fail_insert(mapper, connection, target):
        if isinstance(target, AuditEvent) and target.event_type == "DraftRejected":
            calls.append(target)
            raise RuntimeError("Controlled rejection write failure")

    event.listen(AuditEvent, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="Controlled rejection write failure"):
            reject_order(
                service_db,
                draft_id=ready_draft_id,
                operator_id="op-sarah",
                reason="Should fail on audit insert",
            )
    finally:
        event.remove(AuditEvent, "before_insert", fail_insert)

    assert len(calls) == 1

    # Verify atomic rollback
    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, ready_draft_id)
        assert draft.status == "Ready for Approval"
        assert draft.rejection_reason is None
        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert events == []

    # Verify that after fault removal, rejection succeeds normally
    reject_order(
        service_db,
        draft_id=ready_draft_id,
        operator_id="op-sarah",
        reason="Retry succeeds",
    )

    with _observer(service_db) as observer:
        draft = observer.get(OrderDraft, ready_draft_id)
        assert draft.status == "Rejected"
        assert draft.rejection_reason == "Retry succeeds"
        events = [e for e in _rows(observer, AuditEvent) if e.draft_id == ready_draft_id and e.event_type == "DraftRejected"]
        assert len(events) == 1


def test_rejection_does_not_mutate_line_items_or_provenance(service_db, ready_draft_id):
    """Test: Line items, prices, totals, and field provenance are unaffected by rejection."""
    with _observer(service_db) as observer:
        draft_before = observer.get(OrderDraft, ready_draft_id)
        subtotal_before = draft_before.calculated_subtotal_cents
        extracted_total_before = draft_before.extracted_order_total_cents
        lines_before = [
            (
                line.id,
                line.line_number,
                line.matched_sku,
                line.extracted_quantity,
                line.contract_price_cents,
                line.calculated_line_total_cents,
                line.status,
            )
            for line in draft_before.line_items
        ]
        provenance_before = [
            (p.id, p.field_name, p.verbatim_snippet, p.location_type, p.location_data_json)
            for p in draft_before.provenance_records
        ]
        assert len(lines_before) > 0
        assert len(provenance_before) > 0

    reject_order(
        service_db,
        draft_id=ready_draft_id,
        operator_id="op-sarah",
        reason="Preserve line items intact",
    )

    with _observer(service_db) as observer:
        draft_after = observer.get(OrderDraft, ready_draft_id)
        assert draft_after.calculated_subtotal_cents == subtotal_before
        assert draft_after.extracted_order_total_cents == extracted_total_before
        lines_after = [
            (
                line.id,
                line.line_number,
                line.matched_sku,
                line.extracted_quantity,
                line.contract_price_cents,
                line.calculated_line_total_cents,
                line.status,
            )
            for line in draft_after.line_items
        ]
        assert lines_after == lines_before
        provenance_after = [
            (p.id, p.field_name, p.verbatim_snippet, p.location_type, p.location_data_json)
            for p in draft_after.provenance_records
        ]
        assert provenance_after == provenance_before

