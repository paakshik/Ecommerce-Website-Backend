"""Pytest configuration: isolated PostgreSQL test database via TEST_DATABASE_URL.

Tests run against a real PostgreSQL database. Migrations run once per session;
each test's data is truncated afterward for isolation.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["DATABASE_URL"] = "postgresql+psycopg://ecommerce:change-me@127.0.0.1:5434/ecommerce_test"
os.environ["TEST_DATABASE_URL"] = "postgresql+psycopg://ecommerce:change-me@127.0.0.1:5434/ecommerce_test"
os.environ["RATE_LIMIT_HMAC_KEY"] = "test-hmac-key-do-not-use-in-prod"
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["ENV"] = "testing"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
import pytest  # noqa: E402

from app.database import SessionLocal, engine, Base  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrate_test_db():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def truncate_tables():
    yield
    # Clean all data after each test for isolation.
    session = SessionLocal()
    try:
        tables = ", ".join(f'"{t}"' for t in reversed(Base.metadata.sorted_tables))
        session.execute(text(f"TRUNCATE {tables} CASCADE"))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[__import__("app.database", fromlist=["get_db"]).get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
