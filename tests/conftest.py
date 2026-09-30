"""Pytest test harness, in-memory SQLite fixtures, FastAPI TestClient, and mock provider helpers."""

from __future__ import annotations

import importlib.util
import importlib
import json
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Generator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Directory helpers
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
TEST_FIXTURES_DIR = TESTS_DIR / "fixtures"
APP_FIXTURES_DIR = REPO_ROOT / "app" / "fixtures"


# -----------------------------------------------------------------------------
# Path & Raw Document Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the tests/fixtures directory."""
    return TEST_FIXTURES_DIR


@pytest.fixture
def app_fixtures_dir() -> Path:
    """Path to the app/fixtures runtime directory."""
    return APP_FIXTURES_DIR


@pytest.fixture
def po_clean_acme_path(fixtures_dir: Path) -> Path:
    """Path to the clean Acme PO text fixture."""
    return fixtures_dir / "po_clean_acme.txt"


@pytest.fixture
def po_clean_acme_text(po_clean_acme_path: Path) -> str:
    """Raw text content of the clean Acme PO document."""
    return po_clean_acme_path.read_text(encoding="utf-8")


@pytest.fixture
def po_discrepancy_apex_path(fixtures_dir: Path) -> Path:
    """Path to the discrepancy Apex PO text fixture."""
    return fixtures_dir / "po_discrepancy_apex.txt"


@pytest.fixture
def po_discrepancy_apex_text(po_discrepancy_apex_path: Path) -> str:
    """Raw text content of the discrepancy Apex PO document."""
    return po_discrepancy_apex_path.read_text(encoding="utf-8")


@pytest.fixture
def po_ambiguous_apex_path(fixtures_dir: Path) -> Path:
    """Path to the ambiguous Apex PO text fixture."""
    return fixtures_dir / "po_ambiguous_apex.txt"


@pytest.fixture
def po_ambiguous_apex_text(po_ambiguous_apex_path: Path) -> str:
    """Raw text content of the ambiguous Apex PO document."""
    return po_ambiguous_apex_path.read_text(encoding="utf-8")


@pytest.fixture
def po_unextractable_pdf_path(fixtures_dir: Path) -> Path:
    """Path to the unextractable PDF fixture."""
    return fixtures_dir / "po_unextractable.pdf"


@pytest.fixture
def po_unextractable_pdf_bytes(po_unextractable_pdf_path: Path) -> bytes:
    """Binary bytes of the unextractable PDF document."""
    return po_unextractable_pdf_path.read_bytes()


# -----------------------------------------------------------------------------
# Runtime Fixture JSON Datasets
# -----------------------------------------------------------------------------


@pytest.fixture
def clean_acme_fixture(app_fixtures_dir: Path) -> dict[str, Any]:
    """Pre-verified runtime fixture for clean Acme PO."""
    path = app_fixtures_dir / "clean_acme.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def discrepancy_apex_fixture(app_fixtures_dir: Path) -> dict[str, Any]:
    """Pre-verified runtime fixture for discrepancy Apex PO."""
    path = app_fixtures_dir / "discrepancy_apex.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def ambiguous_apex_fixture(app_fixtures_dir: Path) -> dict[str, Any]:
    """Pre-verified runtime fixture for ambiguous Apex PO."""
    path = app_fixtures_dir / "ambiguous_apex.json"
    return json.loads(path.read_text(encoding="utf-8"))


# -----------------------------------------------------------------------------
# Database Fixtures (In-Memory SQLite with Foreign Key Pragma)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def in_memory_db_engine():
    """Create an isolated in-memory SQLite engine with PRAGMA foreign_keys = ON."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    yield engine
    engine.dispose()


@pytest.fixture
def db_session(in_memory_db_engine) -> Generator[Session, None, None]:
    """Provide a transactional database session rolled back after every test.

    Binds ORM models from `app.models.entities` if that module is implemented.
    Errors inside an existing entities module are raised rather than swallowed.
    """
    base_class = None
    if importlib.util.find_spec("app.models.entities") is not None:
        from app.models.entities import Base  # type: ignore

        base_class = Base

    if base_class is not None:
        base_class.metadata.create_all(bind=in_memory_db_engine)

    testing_session_local = sessionmaker(
        autocommit=False, autoflush=False, bind=in_memory_db_engine
    )
    session = testing_session_local()

    try:
        yield session
    finally:
        session.rollback()
        session.close()
        if base_class is not None:
            base_class.metadata.drop_all(bind=in_memory_db_engine)


# -----------------------------------------------------------------------------
# FastAPI TestClient Fixture
# -----------------------------------------------------------------------------


@pytest.fixture
def app_instance() -> FastAPI:
    """Return the FastAPI application instance or a fallback test app if not implemented."""
    if importlib.util.find_spec("app.main") is None:
        # Fallback placeholder only when app/main.py is genuinely absent
        return FastAPI(title="OrderShield Test Fallback")

    from app.main import app  # type: ignore

    return app


@pytest.fixture
def client(app_instance: FastAPI, db_session: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with database session dependency override applied."""
    get_db_fn = None
    if importlib.util.find_spec("app.database") is not None:
        from app.database import get_db  # type: ignore

        get_db_fn = get_db
        app_instance.dependency_overrides[get_db_fn] = lambda: db_session

    try:
        with TestClient(app_instance) as test_client:
            yield test_client
    finally:
        if get_db_fn is not None:
            app_instance.dependency_overrides.pop(get_db_fn, None)


# -----------------------------------------------------------------------------
# Mock / Replaceable AI Provider Helpers
# -----------------------------------------------------------------------------


class MockAIProvider:
    """Configurable mock AI provider for testing boundary, timeout, and error handling.

    Enables testing:
    - Bounded client timeout abortion (simulating >15s delay);
    - Immediate failure handling (simulating target <=5s transport/auth/quota errors);
    - Untrusted schema validation errors (incomplete or unexpected fields);
    - Deterministic extraction payload return without network dependency;
    - Zero automatic failover assertion.
    """

    def __init__(
        self,
        default_payload: dict[str, Any] | None = None,
        provider_name: str = "mock-provider",
        model_name: str = "mock-model-v1",
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.default_payload = default_payload or {}
        self.call_count = 0
        self.last_call_args: tuple[Any, ...] = ()
        self.last_call_kwargs: dict[str, Any] = {}

        # Error simulation flags
        self.simulate_timeout: bool = False
        self.simulate_auth_error: bool = False
        self.simulate_connection_error: bool = False
        self.simulate_schema_error: bool = False

    def extract(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Simulate document extraction obeying configured failure modes."""
        self.call_count += 1
        self.last_call_args = args
        self.last_call_kwargs = kwargs

        if self.simulate_timeout:
            raise TimeoutError("Inference exceeded bounded 15.0s client deadline")

        if self.simulate_auth_error:
            raise ConnectionError("Authentication failed: invalid API key / 401 Unauthorized")

        if self.simulate_connection_error:
            raise ConnectionError("Connection refused: upstream AI provider unreachable")

        if self.simulate_schema_error:
            # Missing mandatory fields / schema-invalid payload
            return {"unexpected_field": 123}

        return self.default_payload


@pytest.fixture
def mock_ai_provider() -> MockAIProvider:
    """Fixture providing a configurable MockAIProvider instance."""
    return MockAIProvider()


# -----------------------------------------------------------------------------
# P1 Tests-First Infrastructure (No Production Stubs)
# -----------------------------------------------------------------------------


@pytest.fixture
def no_external_network(monkeypatch):
    """Fail closed if a controlled P1 test attempts an actual network connection."""
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    internal_pipe = threading.local()

    def forbidden_connection(*args, **kwargs):
        raise AssertionError("P1 contract tests must not make external network calls")

    def guarded_connect(sock, address):
        if getattr(internal_pipe, "active", False):
            return original_connect(sock, address)
        return forbidden_connection()

    def event_loop_socketpair(*args, **kwargs):
        # Windows implements socketpair using loopback sockets. Permit only that
        # standard-library operation on its own thread, never provider traffic.
        internal_pipe.active = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            internal_pipe.active = False

    monkeypatch.setattr(socket, "socketpair", event_loop_socketpair)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_connection)
    monkeypatch.setattr(socket, "create_connection", forbidden_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_connection)


@pytest.fixture
def require_ai_provider():
    """Defer missing T017 detection to the test body, keeping collection valid."""
    def load_boundary():
        module_name = "app.services.ai_provider"
        if importlib.util.find_spec(module_name) is None:
            pytest.fail(
                "T017 boundary missing: app/services/ai_provider.py is not implemented",
                pytrace=False,
            )
        # Errors *within* an existing module must remain visible.
        return importlib.import_module(module_name)

    return load_boundary
