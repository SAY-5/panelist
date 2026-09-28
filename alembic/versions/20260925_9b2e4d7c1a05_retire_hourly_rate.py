"""retire hourly_rate_cents without dropping it

Revision ID: 9b2e4d7c1a05
Revises: 7c41a0b52e63
Create Date: 2026-09-25 17:05:00.000000

The models no longer map experts.hourly_rate_cents, so 6.0.0 neither reads nor writes it. 5.0.0
selects and inserts it whenever it loads or creates an expert, and a deploy runs
`alembic upgrade head` in a new task while 5.0.0 tasks keep serving, so the column stays,
nullable as the initial schema made it, until a 7.0.0 migration drops it.

"""

from collections.abc import Sequence

revision: str = "9b2e4d7c1a05"
down_revision: str | None = "7c41a0b52e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
