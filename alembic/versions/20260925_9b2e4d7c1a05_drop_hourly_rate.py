"""drop the unused hourly_rate_cents column

Revision ID: 9b2e4d7c1a05
Revises: 7c41a0b52e63
Create Date: 2026-09-25 17:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9b2e4d7c1a05"
down_revision: str | None = "7c41a0b52e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No service ever read the column; payouts use task_rate_cents and the rate card.
    op.drop_column("experts", "hourly_rate_cents")


def downgrade() -> None:
    op.add_column("experts", sa.Column("hourly_rate_cents", sa.Integer(), nullable=True))
