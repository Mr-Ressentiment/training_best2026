"""Persisted draft inspection and T019-owned atomic approval."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.serialization import serialize_draft, serialize_verified_order
from app.database import get_db
from app.models.entities import OrderDraft
from app.models.schemas import NonEmptyText
from app.services.order_service import DraftNotFoundError, approve_order


router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operator_id: NonEmptyText


@router.get("/{draft_id}")
def get_draft(draft_id: str, db: Session = Depends(get_db)) -> dict:
    draft = db.get(OrderDraft, draft_id)
    if draft is None:
        raise DraftNotFoundError("Draft does not exist")
    return serialize_draft(draft)


@router.post("/{draft_id}/approve")
def approve_draft(draft_id: str, body: ApprovalRequest, db: Session = Depends(get_db)) -> dict:
    return serialize_verified_order(approve_order(db, draft_id=draft_id, operator_id=body.operator_id))
