"""The engine outlives migrations: a pooled connection keeps working after the schema under it
is dropped and recreated, as it is when a benchmark resets the database under a running server
or a new task's migrate container runs beside the tasks still serving."""

from alembic.config import Config
from sqlalchemy import insert, select

from alembic import command
from panelist.auth import hash_key
from panelist.db import get_engine, reset_engine
from panelist.models import ApiKey, Role


def test_key_lookup_survives_a_schema_reset_on_a_pooled_connection():
    # The key lookup runs on every request, so a long-lived worker repeats it on each pooled
    # connection far more often than the five runs after which psycopg would prepare it.
    lookup = select(ApiKey).where(ApiKey.key_hash == hash_key("pk_admin_reset"))
    cfg = Config("alembic.ini")
    try:
        with get_engine().connect() as conn:
            for _ in range(10):
                conn.execute(lookup).all()
            conn.commit()
            command.downgrade(cfg, "base")
            command.upgrade(cfg, "head")
            conn.execute(
                insert(ApiKey).values(key_hash=hash_key("pk_admin_reset"), role=Role.admin)
            )
            assert conn.execute(lookup).one().role is Role.admin
            conn.commit()
    finally:
        reset_engine()
