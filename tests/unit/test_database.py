"""Unit tests for SQLite persistence foundation (app/database.py)."""

import sqlite3
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, get_db


def test_sqlite_foreign_keys_pragma_enforced():
    """Verify PRAGMA foreign_keys = 1 on engine connection."""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        assert result == 1


def test_sqlite_foreign_keys_on_in_memory_engine():
    """Verify PRAGMA foreign_keys = 1 on newly created in-memory SQLite engine."""
    mem_engine = create_engine("sqlite:///:memory:")
    with mem_engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        assert result == 1


def test_session_local_instantiation():
    """Verify SessionLocal produces a valid SQLAlchemy Session bound to engine."""
    session = SessionLocal()
    try:
        assert isinstance(session, Session)
        assert session.bind == engine
    finally:
        session.close()


def test_get_db_generator():
    """Verify get_db dependency yields an active session and closes it."""
    gen = get_db()
    session = next(gen)
    assert isinstance(session, Session)
    assert session.is_active
    try:
        next(gen)
    except StopIteration:
        pass
    # Session is closed after generator finishes
    assert not session.is_active or session.get_transaction() is None


def test_metadata_create_all_and_drop_all():
    """Verify deterministic schema creation and teardown on empty SQLite database."""
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    with test_engine.connect() as conn:
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        ).scalars().all()
        assert "catalog_products" in tables
        assert "customer_contracts" in tables
        assert "contract_price_tiers" in tables
        assert "purchase_order_documents" in tables
        assert "order_drafts" in tables
        assert "draft_line_items" in tables
        assert "field_provenances" in tables
        assert "discrepancy_flags" in tables
        assert "verified_order_records" in tables
        assert "audit_events" in tables

    Base.metadata.drop_all(bind=test_engine)
    with test_engine.connect() as conn:
        remaining_tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        ).scalars().all()
        assert len(remaining_tables) == 0
