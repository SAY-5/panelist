import os
import tempfile
from collections.abc import Iterator

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from alembic import command
from panelist.auth import hash_key
from panelist.config import get_settings
from panelist.models import ApiKey, Role


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session", autouse=True)
def _settings(db_url: str) -> Iterator[None]:
    delivery_dir = tempfile.mkdtemp(prefix="panelist-deliveries-")
    os.environ["DATABASE_URL"] = db_url
    os.environ["DELIVERY_DIR"] = delivery_dir
    os.environ["DELIVERY_S3_BUCKET"] = ""
    os.environ["LEASE_SECONDS"] = "900"
    get_settings.cache_clear()
    from panelist.db import reset_engine

    reset_engine()
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield
    reset_engine()


@pytest.fixture
def settings():
    s = get_settings()
    snapshot = s.model_dump()
    yield s
    for k, v in snapshot.items():
        setattr(s, k, v)


@pytest.fixture
def db() -> Iterator[Session]:
    from panelist.db import session_factory

    with session_factory()() as s:
        tables = s.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename <> 'alembic_version'"
            )
        ).scalars()
        s.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))
        s.commit()
        yield s


@pytest.fixture
def client(db) -> Iterator[TestClient]:
    from panelist.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_key(db: Session) -> str:
    raw = "pk_admin_test"
    db.add(ApiKey(key_hash=hash_key(raw), role=Role.admin, label="test-admin"))
    db.commit()
    return raw


@pytest.fixture
def reviewer_key(db: Session) -> str:
    raw = "pk_reviewer_test"
    db.add(ApiKey(key_hash=hash_key(raw), role=Role.reviewer, label="test-reviewer"))
    db.commit()
    return raw


@pytest.fixture
def senior_key(db: Session) -> str:
    raw = "pk_senior_test"
    db.add(ApiKey(key_hash=hash_key(raw), role=Role.senior_reviewer, label="test-senior"))
    db.commit()
    return raw
