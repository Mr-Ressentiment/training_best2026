"""Read-only projections of persisted drafts and immutable verified orders."""

from datetime import timezone
import json
from typing import Iterable

from app.models.entities import (
    DiscrepancyFlag, DraftLineItem, FieldProvenance, OrderDraft, VerifiedOrderRecord,
)
from app.models.schemas import cents_to_decimal


def _money(cents: int | None) -> str | None:
    return format(cents_to_decimal(cents), ".2f") if cents is not None else None


def _provenance(records: Iterable[FieldProvenance], fields: tuple[str, ...]) -> dict:
    persisted = {record.field_name: record for record in records}
    return {
        field: {
            "field_name": record.field_name,
            "verbatim_snippet": record.verbatim_snippet,
            "location": json.loads(record.location_data_json),
        } if (record := persisted.get(field)) is not None else None
        for field in fields
    }


def _discrepancy(flag: DiscrepancyFlag) -> dict:
    return {
        "flag_id": flag.id,
        "discrepancy_type": flag.discrepancy_type,
        "severity": flag.severity,
        "expected_value": flag.expected_value,
        "requested_value": flag.requested_value,
        "explanation": flag.explanation,
        "resolution_state": flag.resolution_state,
    }


def _line(line: DraftLineItem) -> dict:
    return {
        "line_id": line.id,
        "line_number": line.line_number,
        "customer_description": line.customer_description,
        "extracted_quantity": line.extracted_quantity,
        "extracted_unit_price": _money(line.extracted_unit_price_cents),
        "extracted_line_total": _money(line.extracted_line_total_cents),
        "matched_sku": line.matched_sku,
        "sku_name": line.product.name if line.product is not None else None,
        "sku_confidence": line.sku_confidence,
        "sku_resolution_source": line.sku_resolution_source,
        "candidate_skus": json.loads(line.candidate_skus_json) if line.candidate_skus_json is not None else [],
        "matching_rationale": line.matching_rationale,
        "contract_price": _money(line.contract_price_cents),
        "calculated_line_total": _money(line.calculated_line_total_cents),
        "status": line.status,
        "field_provenance": _provenance(line.provenance_records, (
            "customer_description", "extracted_quantity", "extracted_unit_price", "extracted_line_total",
        )),
        "discrepancies": [_discrepancy(flag) for flag in sorted(line.discrepancy_flags, key=lambda flag: flag.id)],
    }


def serialize_draft(draft: OrderDraft) -> dict:
    """Expose stored values without pricing, AI calls, flushes or commits."""
    return {
        "draft_id": draft.id,
        "document_id": draft.document_id,
        "customer_id": draft.customer_id,
        "customer_name_extracted": draft.customer_name_extracted,
        "po_number_extracted": draft.po_number_extracted,
        "status": draft.status,
        "is_replay_mode": draft.is_replay_mode,
        "calculated_subtotal": _money(draft.calculated_subtotal_cents),
        "header_provenance": _provenance(
            (record for record in draft.provenance_records if record.line_item_id is None),
            ("customer_name", "po_number"),
        ),
        "line_items": [_line(line) for line in sorted(draft.line_items, key=lambda line: (line.line_number, line.id))],
    }


def serialize_verified_order(record: VerifiedOrderRecord) -> dict:
    """Count the immutable snapshot and normalize SQLite's UTC timestamp."""
    approved_at = record.approved_at
    if approved_at.tzinfo is None:
        approved_at = approved_at.replace(tzinfo=timezone.utc)
    return {
        "order_id": record.id,
        "draft_id": record.draft_id,
        "order_number": record.order_number,
        "customer_id": record.customer_id,
        "po_number": record.po_number,
        "grand_total": _money(record.grand_total_cents),
        "approved_by": record.approved_by,
        "approved_at": approved_at.astimezone(timezone.utc).isoformat(),
        "is_replay_mode": record.is_replay_mode,
        "line_items_count": len(json.loads(record.line_items_snapshot_json)),
    }
