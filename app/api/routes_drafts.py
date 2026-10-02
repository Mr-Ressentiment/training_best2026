"""Persisted draft inspection, T019 atomic approval, and P2 operator mutations."""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.serialization import _money, serialize_draft, serialize_verified_order
from app.database import get_db
from app.models.advisory import DraftAdvisoryResponse
from app.models.entities import AuditEvent, DraftLineItem, OrderDraft
from app.models.schemas import ErrorResponse, LocationDataSchema, NonEmptyText
from app.services.advisory import CANONICAL_SECTIONS, build_draft_advisory
from app.services.order_service import (
    DraftNotFoundError, approve_order, reject_order,
)
from app.services.reconciliation import (
    _CORRECTABLE_FIELDS, LineNotFoundError, TerminalDraftMutationError,
    correct_line_field, remove_line, select_line_sku,
)


router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operator_id: NonEmptyText


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operator_id: NonEmptyText
    reason: NonEmptyText


class SelectSKURequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["SelectSKU"]
    matched_sku: NonEmptyText


class CorrectFieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["CorrectField"]
    field: NonEmptyText
    value: Any
    source_snippet: NonEmptyText
    source_location: LocationDataSchema


PatchLineRequest = Annotated[
    SelectSKURequest | CorrectFieldRequest,
    Field(discriminator="action"),
]


@router.get("/{draft_id}")
def get_draft(draft_id: str, db: Session = Depends(get_db)) -> dict:
    draft = db.get(OrderDraft, draft_id)
    if draft is None:
        raise DraftNotFoundError("Draft does not exist")
    return serialize_draft(draft)


@router.get(
    "/{draft_id}/advisory",
    response_model=DraftAdvisoryResponse,
    response_model_exclude_none=False,
)
def get_draft_advisory(
    draft_id: str,
    response: Response,
    sections: list[str] | None = Query(
        default=None,
        description="Subset of sections to compute and return (baseline, counterfactuals, review_priority, sku_confidence, trace). Supports repeated parameter or comma-separated values.",
    ),
    max_depth: int = Query(
        default=2,
        ge=1,
        le=3,
        description="Maximum depth of operator action sequence search (1..3).",
    ),
    max_scenarios: int = Query(
        default=24,
        ge=1,
        le=50,
        description="Maximum number of candidate action plans to simulate (1..50).",
    ),
    db: Session = Depends(get_db),
) -> Any:
    response.headers["Cache-Control"] = "no-store"

    if sections is None:
        effective_sections = list(CANONICAL_SECTIONS)
    else:
        parsed_tokens: list[str] = []
        for item in sections:
            for part in item.split(","):
                cleaned = part.strip()
                if cleaned:
                    parsed_tokens.append(cleaned)
        if not parsed_tokens or any(s not in CANONICAL_SECTIONS for s in parsed_tokens):
            raise RequestValidationError(
                errors=[
                    {
                        "type": "value_error",
                        "loc": ("query", "sections"),
                        "msg": f"sections must be a non-empty subset of {list(CANONICAL_SECTIONS)}",
                        "input": sections,
                    }
                ]
            )
        norm_set = set(parsed_tokens)
        effective_sections = [s for s in CANONICAL_SECTIONS if s in norm_set]

    try:
        return build_draft_advisory(
            db,
            draft_id,
            sections=effective_sections,
            max_depth=max_depth,
            max_scenarios=max_scenarios,
        )
    except DraftNotFoundError:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="InternalSimulationError",
                message="Internal error during advisory evaluation",
            ).model_dump(),
            headers={"Cache-Control": "no-store"},
        )



@router.post("/{draft_id}/approve")
def approve_draft(draft_id: str, body: ApprovalRequest, db: Session = Depends(get_db)) -> dict:
    return serialize_verified_order(approve_order(db, draft_id=draft_id, operator_id=body.operator_id))


@router.post("/{draft_id}/reject")
def reject_draft(draft_id: str, body: RejectRequest, db: Session = Depends(get_db)) -> dict:
    rejected_draft = reject_order(
        db,
        draft_id=draft_id,
        operator_id=body.operator_id,
        reason=body.reason,
    )
    return serialize_draft(rejected_draft)


@router.patch("/{draft_id}/lines/{line_id}")
def patch_line(
    draft_id: str,
    line_id: str,
    body: PatchLineRequest,
    db: Session = Depends(get_db),
) -> dict:
    draft = db.get(OrderDraft, draft_id)
    if draft is None:
        raise DraftNotFoundError("Draft does not exist")
    if draft.status in ("Approved", "Rejected"):
        raise TerminalDraftMutationError(f"Draft is {draft.status} and cannot be modified")

    line = db.get(DraftLineItem, line_id)
    if line is None or line.draft_id != draft_id:
        raise LineNotFoundError("Line item does not exist")

    try:
        if isinstance(body, SelectSKURequest):
            previous_sku = line.matched_sku
            line_number = line.line_number
            updated_draft = select_line_sku(db, draft, line, matched_sku=body.matched_sku)
            db.add(
                AuditEvent(
                    draft=updated_draft,
                    event_type="SKUSelected",
                    actor="Operator",
                    details_json=_json({
                        "line_number": line_number,
                        "previous_sku": previous_sku,
                        "resolution_source": "OPERATOR_SELECTED",
                        "selected_sku": body.matched_sku,
                    }),
                )
            )
        elif isinstance(body, CorrectFieldRequest):
            col = _CORRECTABLE_FIELDS.get(body.field)
            if col and col.endswith("_cents"):
                prev_val = _money(getattr(line, col))
            elif col:
                prev_val = getattr(line, col)
            else:
                prev_val = None

            line_number = line.line_number
            updated_draft = correct_line_field(
                db,
                draft,
                line,
                field=body.field,
                value=body.value,
                source_snippet=body.source_snippet,
                source_location=body.source_location,
            )
            if col and col.endswith("_cents"):
                new_val = _money(getattr(line, col))
            elif col:
                new_val = getattr(line, col)
            else:
                new_val = body.value

            db.add(
                AuditEvent(
                    draft=updated_draft,
                    event_type="FieldCorrected",
                    actor="Operator",
                    details_json=_json({
                        "field": body.field,
                        "line_number": line_number,
                        "new_value": new_val,
                        "previous_value": prev_val,
                        "source_location": body.source_location.model_dump(mode="json"),
                        "source_snippet": body.source_snippet,
                    }),
                )
            )
        db.flush()
        db.commit()
        return serialize_draft(updated_draft)
    except Exception:
        db.rollback()
        raise


@router.delete("/{draft_id}/lines/{line_id}")
def delete_line(
    draft_id: str,
    line_id: str,
    db: Session = Depends(get_db),
) -> dict:
    draft = db.get(OrderDraft, draft_id)
    if draft is None:
        raise DraftNotFoundError("Draft does not exist")
    if draft.status in ("Approved", "Rejected"):
        raise TerminalDraftMutationError(f"Draft is {draft.status} and cannot be modified")

    line = db.get(DraftLineItem, line_id)
    if line is None or line.draft_id != draft_id:
        raise LineNotFoundError("Line item does not exist")

    try:
        line_number = line.line_number
        matched_sku = line.matched_sku
        was_active = (line.status == "Active")
        updated_draft = remove_line(db, draft, line)
        if was_active:
            db.add(
                AuditEvent(
                    draft=updated_draft,
                    event_type="LineRemoved",
                    actor="Operator",
                    details_json=_json({
                        "line_number": line_number,
                        "matched_sku": matched_sku,
                    }),
                )
            )
        db.flush()
        db.commit()
        return serialize_draft(updated_draft)
    except Exception:
        db.rollback()
        raise
