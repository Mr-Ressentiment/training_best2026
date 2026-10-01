"""Live-only document intake; business and transaction rules belong to T019."""

from datetime import timezone
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.multipart import UploadedDocument, parse_upload
from app.api.serialization import _money, _provenance, serialize_draft
from app.config import settings
from app.database import get_db
from app.models.entities import AuditEvent, CatalogProduct, VerifiedOrderRecord
from app.services.ai_provider import LiveAIProvider, OrderShieldAIProvider
from app.services.document_parser import ParsedDocument, parse_document
from app.services.order_service import ingest_order


router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


async def _get_upload(request: Request) -> UploadedDocument:
    return parse_upload(request.headers.get("content-type"), await request.body())


def _get_parsed_document(upload: UploadedDocument = Depends(_get_upload)) -> ParsedDocument:
    return parse_document(upload.content, filename=upload.filename, content_type=upload.content_type)


def get_live_ai_provider() -> OrderShieldAIProvider:
    """Overridable T016 seam; construct only the explicitly configured provider."""
    return LiveAIProvider(settings=settings)


@router.post("/ingest", status_code=201, dependencies=[Depends(_get_parsed_document)])
def ingest_live_order(
    document: ParsedDocument = Depends(_get_parsed_document),
    db: Session = Depends(get_db),
    provider: OrderShieldAIProvider = Depends(get_live_ai_provider),
) -> dict:
    # The cached route dependency parses first, even before provider construction.
    return serialize_draft(ingest_order(db, document=document, provider=provider))


@router.get("/{order_id}")
def get_verified_order(
    order_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Retrieve an approved verified order record with immutable line snapshot, provenance, and audit trail."""
    record = db.get(VerifiedOrderRecord, order_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Order not found")

    draft = record.draft

    # 1. Header provenance from draft
    header_provenance = _provenance(
        (r for r in draft.provenance_records if r.line_item_id is None),
        ("customer_name", "po_number"),
    )

    # 2. Immutable line snapshot
    snapshot_lines = json.loads(record.line_items_snapshot_json)
    draft_lines_by_num = {line.line_number: line for line in draft.line_items}

    line_items = []
    for item in snapshot_lines:
        sku = item["sku"]
        line_num = item["line_number"]
        draft_line = draft_lines_by_num.get(line_num)

        # SKU display name from catalog lookup by exact immutable snapshot SKU
        prod = db.get(CatalogProduct, sku)
        sku_name = prod.name if prod is not None else None

        # Line provenance from associated draft line
        if draft_line is not None:
            field_provenance = _provenance(
                draft_line.provenance_records,
                (
                    "customer_description",
                    "extracted_quantity",
                    "extracted_unit_price",
                    "extracted_line_total",
                ),
            )
        else:
            field_provenance = {
                field: None
                for field in (
                    "customer_description",
                    "extracted_quantity",
                    "extracted_unit_price",
                    "extracted_line_total",
                )
            }

        line_items.append({
            "line_number": line_num,
            "sku": sku,
            "sku_name": sku_name,
            "sku_resolution_source": item["sku_resolution_source"],
            "quantity": item["quantity"],
            "unit_price": _money(item.get("contract_price_cents")),
            "line_total": _money(item.get("calculated_line_total_cents")),
            "field_provenance": field_provenance,
        })

    # 3. Chronological audit trail (timestamp ASC, id ASC)
    audit_stmt = (
        select(AuditEvent)
        .where(AuditEvent.draft_id == record.draft_id)
        .order_by(AuditEvent.timestamp.asc(), AuditEvent.id.asc())
    )
    events = db.scalars(audit_stmt).all()
    audit_trail = []
    for event in events:
        ts = event.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        audit_trail.append({
            "event_type": event.event_type,
            "actor": event.actor,
            "timestamp": ts.astimezone(timezone.utc).isoformat(),
            "details": json.loads(event.details_json),
        })

    # 4. Committed order fields
    approved_at = record.approved_at
    if approved_at.tzinfo is None:
        approved_at = approved_at.replace(tzinfo=timezone.utc)

    return {
        "order_id": record.id,
        "order_number": record.order_number,
        "customer_id": record.customer_id,
        "po_number": record.po_number,
        "grand_total": _money(record.grand_total_cents),
        "approved_by": record.approved_by,
        "approved_at": approved_at.astimezone(timezone.utc).isoformat(),
        "is_replay_mode": record.is_replay_mode,
        "header_provenance": header_provenance,
        "line_items": line_items,
        "audit_trail": audit_trail,
    }
