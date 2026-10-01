"""P1 intake persistence and atomic approval, independent of HTTP.

Each public operation owns the supplied Session's transaction: one commit on
success, rollback on failure. Use a dedicated request/service Session without
unrelated pending writes. An existing implicit read transaction is supported.
Provider extraction and canonical provenance grounding are validated before
any intake entities are added.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    AuditEvent, CatalogProduct, DraftLineItem, FieldProvenance, OrderDraft,
    PurchaseOrderDocument, VerifiedOrderRecord,
)
from app.models.schemas import AIExtractionPayload, FieldProvenanceSchema, decimal_to_cents
from app.services.ai_provider import AIOutputValidationError, OrderShieldAIProvider, validate_extraction
from app.services.document_parser import ParsedDocument
from app.services.reconciliation import evaluate_clean_draft


class DraftNotReadyForApprovalError(Exception):
    """Approval requires the persisted Ready for Approval state."""


class TerminalDraftStateError(Exception):
    """An Approved or Rejected draft cannot be approved again."""


class DraftNotFoundError(LookupError):
    """The requested draft does not exist."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_provenance_grounding(
    document: ParsedDocument, evidence: FieldProvenanceSchema | None,
) -> None:
    """Verify the exact claimed location; never search or repair source evidence."""
    if evidence is None:
        return
    location = evidence.location.root
    snippet = evidence.verbatim_snippet
    grounded = False
    if location.type == "txt" and document.content_type == "text/plain":
        line = next((span for span in document.line_spans if span.line_number == location.line_number), None)
        if line is not None:
            start = location.char_offset
            end = start + len(snippet)
            grounded = (
                0 <= start < end <= len(line.text)
                and line.text[start:end] == snippet
                and line.char_start + end <= line.char_end
                and document.raw_text[line.char_start + start:line.char_start + end] == snippet
            )
    elif location.type == "pdf" and document.content_type == "application/pdf":
        page = next((span for span in document.page_spans if span.page_number == location.page_number), None)
        if page is not None:
            start, end = location.char_start, location.char_end
            grounded = (
                page.char_start <= start < end <= page.char_end
                and document.raw_text[start:end] == snippet
            )
    if not grounded:
        raise AIOutputValidationError(f"AI provenance for {evidence.field_name} is not grounded at its claimed location")


def _validate_extraction_grounding(document: ParsedDocument, extraction: AIExtractionPayload) -> None:
    """Check every non-null header/line citation before any ORM object is added."""
    for group in [extraction.header_provenance, *(item.field_provenance for item in extraction.line_items)]:
        for field in type(group).model_fields:
            _validate_provenance_grounding(document, getattr(group, field))


def _add_provenance(
    db: Session, draft: OrderDraft, evidence: FieldProvenanceSchema | None,
    line: DraftLineItem | None = None,
) -> None:
    if evidence is not None:
        db.add(FieldProvenance(
            draft=draft, line_item=line, field_name=evidence.field_name,
            verbatim_snippet=evidence.verbatim_snippet, location_type=evidence.location.root.type,
            location_data_json=_json(evidence.location.model_dump(mode="json")),
        ))


def ingest_order(
    db: Session, *, document: ParsedDocument, provider: OrderShieldAIProvider,
) -> OrderDraft:
    """Persist a parsed canonical document and its validated extraction atomically.

    Canonical raw_text is passed through unchanged. Provider errors propagate;
    this operation never instantiates a replacement provider or replay fixture.
    Replay and audit identity come exclusively from the producing instance.
    """
    try:
        if not isinstance(document.filename, str) or not document.filename.strip():
            raise ValueError("Intake requires the parsed document filename")
        catalog = [
            {"sku": product.sku, "name": product.name, "category": product.category,
             "unit_of_measure": product.unit_of_measure}
            for product in db.scalars(select(CatalogProduct).order_by(CatalogProduct.sku))
        ]
        extraction = validate_extraction(provider.extract(raw_text=document.raw_text, catalog=catalog))
        _validate_extraction_grounding(document, extraction)
        stored_document = PurchaseOrderDocument(
            filename=document.filename, content_type=document.content_type,
            raw_text=document.raw_text, status="Ingested",
        )
        draft = OrderDraft(
            document=stored_document, customer_id=extraction.customer_id,
            customer_name_extracted=extraction.customer_name, po_number_extracted=extraction.po_number,
            extracted_order_total_cents=(
                decimal_to_cents(extraction.extracted_order_total)
                if extraction.extracted_order_total is not None else None
            ),
            status="Ingested", is_replay_mode=provider.is_replay_mode,
        )
        db.add(draft)
        for field in type(extraction.header_provenance).model_fields:
            _add_provenance(db, draft, getattr(extraction.header_provenance, field))
        for item in extraction.line_items:
            line = DraftLineItem(
                draft=draft, line_number=item.line_number, customer_description=item.customer_description,
                extracted_quantity=item.extracted_quantity,
                extracted_unit_price_cents=(
                    decimal_to_cents(item.extracted_unit_price) if item.extracted_unit_price is not None else None
                ),
                extracted_line_total_cents=(
                    decimal_to_cents(item.extracted_line_total) if item.extracted_line_total is not None else None
                ),
                matched_sku=item.matched_sku, sku_confidence=item.sku_confidence,
                sku_resolution_source=item.sku_resolution_source,
                candidate_skus_json=_json([candidate.model_dump(mode="json") for candidate in item.candidate_skus]),
                matching_rationale=item.matching_rationale, status="Active",
            )
            db.add(line)
            for field in type(item.field_provenance).model_fields:
                _add_provenance(db, draft, getattr(item.field_provenance, field), line)
        db.add_all([
            AuditEvent(
                draft=draft, event_type="DocumentIngested", actor="System",
                details_json=_json({"filename": document.filename, "content_type": document.content_type}),
            ),
            AuditEvent(
                draft=draft, event_type="AIExtractionCompleted", actor="AIProvider",
                details_json=_json({"provider": provider.provider_name, "model": provider.model_name}),
            ),
        ])
        evaluate_clean_draft(db, draft)
        db.flush()
        db.commit()
        return draft
    except Exception:
        db.rollback()
        raise


def _next_order_number(db: Session, year: int) -> str:
    """Transaction-scoped SQLite sequence; database uniqueness is the backstop.

    Use the maximum persisted suffix so gaps cannot reuse an order number.
    Concurrent collisions fail and roll back; a caller may explicitly retry.
    """
    prefix = f"VO-{year}-"
    numbers = db.scalars(select(VerifiedOrderRecord.order_number).where(
        VerifiedOrderRecord.order_number.startswith(prefix),
    ))
    sequence = max((int(number[len(prefix):]) for number in numbers), default=0) + 1
    return f"{prefix}{sequence:04d}"


def approve_order(db: Session, *, draft_id: str, operator_id: str) -> VerifiedOrderRecord:
    """Atomically snapshot persisted commercial values and approve a ready draft.

    No AI extraction or pricing recomputation occurs during approval. Terminal
    state is read afresh even if this Session already cached an older draft.
    """
    try:
        draft = db.scalar(select(OrderDraft).where(OrderDraft.id == draft_id).execution_options(
            populate_existing=True,
        ))
        if draft is None:
            raise DraftNotFoundError("Draft does not exist")
        if draft.status in ("Approved", "Rejected"):
            raise TerminalDraftStateError(f"Cannot approve a terminal {draft.status} draft")
        if draft.status != "Ready for Approval":
            raise DraftNotReadyForApprovalError("Draft is not Ready for Approval")
        if not isinstance(operator_id, str) or not operator_id.strip():
            raise ValueError("Approval requires a nonblank operator identity")
        lines = sorted(
            (line for line in draft.line_items if line.status == "Active"),
            key=lambda line: line.line_number,
        )
        snapshot = [{
            "line_number": line.line_number, "sku": line.matched_sku,
            "quantity": line.extracted_quantity, "contract_price_cents": line.contract_price_cents,
            "calculated_line_total_cents": line.calculated_line_total_cents,
            "sku_resolution_source": line.sku_resolution_source,
        } for line in lines]
        approved_at = datetime.now(timezone.utc)
        record = VerifiedOrderRecord(
            draft=draft, order_number=_next_order_number(db, approved_at.year),
            customer_id=draft.customer_id, po_number=draft.po_number_extracted,
            grand_total_cents=draft.calculated_subtotal_cents, approved_by=operator_id,
            approved_at=approved_at, is_replay_mode=draft.is_replay_mode,
            line_items_snapshot_json=_json(snapshot),
        )
        db.add(record)
        draft.status = "Approved"
        db.add(AuditEvent(
            draft=draft, event_type="OrderApproved", actor="Operator",
            details_json=_json({"operator_id": operator_id, "order_number": record.order_number}),
            timestamp=approved_at,
        ))
        db.flush()
        db.commit()
        return record
    except Exception:
        db.rollback()
        raise


def reject_order(
    db: Session,
    *,
    draft_id: str,
    operator_id: str,
    reason: str,
) -> OrderDraft:
    """Atomically transition a non-terminal order draft to Rejected.

    Persists the mandatory rejection reason and records a single DraftRejected
    audit event. Does not create a VerifiedOrderRecord or modify discrepancy
    flags. Owned by the supplied Session's transaction.
    """
    try:
        draft = db.scalar(
            select(OrderDraft).where(OrderDraft.id == draft_id).execution_options(
                populate_existing=True,
            )
        )
        if draft is None:
            raise DraftNotFoundError("Draft does not exist")
        if draft.status in ("Approved", "Rejected"):
            raise TerminalDraftStateError(f"Cannot reject a terminal {draft.status} draft")
        if not isinstance(operator_id, str) or not operator_id.strip():
            raise ValueError("Rejection requires a nonblank operator identity")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Rejection requires a nonblank reason")

        draft.status = "Rejected"
        draft.rejection_reason = reason
        db.add(
            AuditEvent(
                draft=draft,
                event_type="DraftRejected",
                actor="Operator",
                details_json=_json({"operator_id": operator_id, "reason": reason}),
                timestamp=datetime.now(timezone.utc),
            )
        )
        db.flush()
        db.commit()
        return draft
    except Exception:
        db.rollback()
        raise
