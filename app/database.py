"""Database session and engine management (PostgreSQL only, psycopg 3)."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

# SQLAlchemy 2.0 declarative base
Base = declarative_base()

# Create the engine. pool_pre_ping helps survive stale connections.
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

# Test engine factory (overridden in tests via TEST_DATABASE_URL)
_test_engine = None


def set_test_database_url(url: str) -> None:
    """Point the engine at an isolated test database (used by tests)."""
    global engine, _test_engine, SessionLocal
    _test_engine = create_engine(url, pool_pre_ping=True, future=True)
    engine = _test_engine
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy Session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables (used by seed/dev tooling; Alembic is authoritative)."""
    Base.metadata.create_all(bind=engine)
