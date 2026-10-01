"""P1 FastAPI assembly: API boundaries and committed local static assets."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app.api import routes_catalog, routes_drafts, routes_fixtures, routes_orders
from app.models.schemas import ErrorResponse
from app.services.ai_provider import AIOutputValidationError, AIProviderUnavailableError
from app.services.document_parser import DocumentParserError
from app.services.order_service import (
    DraftNotFoundError, DraftNotReadyForApprovalError, TerminalDraftStateError,
)
from app.services.reconciliation import (
    LineMutationValidationError, LineNotFoundError, SourceGroundingMismatchError,
    TerminalDraftMutationError,
)


def _error(status: int, name: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(error=name, message=message).model_dump())


async def _parser_error(request: Request, exc: DocumentParserError) -> JSONResponse:
    # Parser diagnostics describe local input errors, never provider responses.
    return _error(400, type(exc).__name__, str(exc) or "Document parsing failed")


async def _ai_output_error(request: Request, exc: AIOutputValidationError) -> JSONResponse:
    return _error(502, "AIOutputValidationError", "AI extraction output failed validation")


async def _provider_error(request: Request, exc: AIProviderUnavailableError) -> JSONResponse:
    # Subclasses (including configuration errors) share the public contract.
    # Do not reflect arbitrary exception text or upstream secrets into HTTP.
    return _error(503, "AIProviderUnavailableError", "Configured live AI provider is unavailable or misconfigured")


async def _not_ready_error(request: Request, exc: DraftNotReadyForApprovalError) -> JSONResponse:
    return _error(409, "DraftNotReadyForApprovalError", "Draft is not Ready for Approval")


async def _terminal_error(request: Request, exc: Exception) -> JSONResponse:
    return _error(409, "TerminalDraftConflictError", "Cannot modify an Approved or Rejected draft")


async def _source_grounding_error(request: Request, exc: SourceGroundingMismatchError) -> JSONResponse:
    return _error(422, "SourceGroundingMismatchError", str(exc) or "Source grounding validation failed")


async def _line_mutation_validation_error(request: Request, exc: LineMutationValidationError) -> JSONResponse:
    return _error(422, "LineMutationValidationError", str(exc) or "Line mutation validation failed")


async def _missing_line_error(request: Request, exc: LineNotFoundError) -> JSONResponse:
    return _error(404, "LineNotFoundError", str(exc) or "Line item does not exist")


async def _missing_draft_error(request: Request, exc: DraftNotFoundError) -> JSONResponse:
    return _error(404, "DraftNotFoundError", "Draft does not exist")


async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return _error(exc.status_code, "HTTPException", str(exc.detail) or "HTTP request failed")


async def _request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(422, "RequestValidationError", "Request payload failed validation")


def create_app() -> FastAPI:
    """Build without DB initialization, live provider creation or network access."""
    # Keep OpenAPI JSON, without the default CDN-backed documentation pages.
    application = FastAPI(title="OrderShield", docs_url=None, redoc_url=None)
    for error, handler in (
        (DocumentParserError, _parser_error),
        (AIOutputValidationError, _ai_output_error),
        (AIProviderUnavailableError, _provider_error),
        (DraftNotReadyForApprovalError, _not_ready_error),
        (TerminalDraftStateError, _terminal_error),
        (TerminalDraftMutationError, _terminal_error),
        (SourceGroundingMismatchError, _source_grounding_error),
        (LineMutationValidationError, _line_mutation_validation_error),
        (LineNotFoundError, _missing_line_error),
        (DraftNotFoundError, _missing_draft_error),
        (HTTPException, _http_error),
        (RequestValidationError, _request_validation_error),
    ):
        application.add_exception_handler(error, handler)
    application.include_router(routes_orders.router)
    application.include_router(routes_fixtures.router)
    application.include_router(routes_drafts.router)
    application.include_router(routes_catalog.router)
    application.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")

    @application.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/static/index.html", status_code=307)

    return application


app = create_app()
