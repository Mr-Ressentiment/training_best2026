"""Live-only document intake; business and transaction rules belong to T019."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.multipart import UploadedDocument, parse_upload
from app.api.serialization import serialize_draft
from app.config import settings
from app.database import get_db
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
