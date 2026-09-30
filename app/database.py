"""Database connection and session management for OrderShield."""

from typing import Generator
import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import settings

# Engine configuration:
# For SQLite, check_same_thread: False allows multi-threaded requests (e.g. FastAPI / tests) to share connections safely.
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enforce foreign key constraints on every SQLite connection."""
    if isinstance(dbapi_connection, sqlite3.Connection) or type(dbapi_connection).__module__.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session with guaranteed closure."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
