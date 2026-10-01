"""Application-owned replay datasets, structurally independent of live intake."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.serialization import serialize_draft
from app.database import get_db
from app.services.ai_provider import FixtureAIProvider
from app.services.document_parser import parse_document
from app.services.order_service import ingest_order


router = APIRouter(prefix="/api/v1/fixtures", tags=["fixtures"])
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTERED_FILES = ("clean_acme.json", "discrepancy_apex.json", "ambiguous_apex.json")


def _fixture_registry() -> dict[str, dict]:
    """Read only the committed datasets; caller IDs never become paths."""
    registry = {}
    for filename in _REGISTERED_FILES:
        dataset = json.loads((_REPO_ROOT / "app" / "fixtures" / filename).read_text(encoding="utf-8"))
        metadata = {key: dataset[key] for key in ("fixture_id", "name", "description", "document_filename")}
        registry[metadata["fixture_id"]] = metadata
    return registry


@router.get("")
def list_fixtures() -> list[dict]:
    return list(_fixture_registry().values())


@router.post("/{fixture_id}/ingest", status_code=201)
def ingest_fixture(fixture_id: str, db: Session = Depends(get_db)) -> dict:
    metadata = _fixture_registry().get(fixture_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Unknown registered fixture")
    # Select the committed source by exact filename, never from a caller path.
    sources = {path.name: path for path in (_REPO_ROOT / "tests" / "fixtures").iterdir() if path.is_file()}
    source = sources[metadata["document_filename"]]
    document = parse_document(source.read_bytes(), filename=source.name)
    provider = FixtureAIProvider(fixture_id=fixture_id)
    return serialize_draft(ingest_order(db, document=document, provider=provider))
