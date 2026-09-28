"""An upgrade keeps every column the previous release has.

A deploy runs `alembic upgrade head` in a new task while tasks of the previous release keep
serving, and those select and insert every column their models map, so a release may add columns
and stop using them but not drop one the previous release still reads.
"""

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from panelist.db import get_engine, reset_engine

# The head migration of the previous release, 5.0.0.
PREVIOUS_RELEASE_HEAD = "7c41a0b52e63"


def _columns() -> set[tuple[str, str]]:
    inspector = inspect(get_engine())
    return {
        (table, column["name"])
        for table in inspector.get_table_names()
        for column in inspector.get_columns(table)
    }


def test_upgrade_keeps_every_column_of_the_previous_release():
    cfg = Config("alembic.ini")
    try:
        command.downgrade(cfg, PREVIOUS_RELEASE_HEAD)
        previous = _columns()
        command.upgrade(cfg, "head")
        assert previous - _columns() == set()
    finally:
        command.upgrade(cfg, "head")
        reset_engine()
