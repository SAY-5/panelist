"""calibration and tier changes

Revision ID: 3d8dba8d69dd
Revises: 5e18ef22c95d
Create Date: 2026-09-08 16:32:23.368740

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "3d8dba8d69dd"
down_revision: str | None = "5e18ef22c95d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tier_changes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("expert_id", sa.UUID(), nullable=False),
        sa.Column(
            "from_tier",
            postgresql.ENUM("junior", "senior", "lead", name="tier", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "to_tier",
            postgresql.ENUM("junior", "senior", "lead", name="tier", create_type=False),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["expert_id"],
            ["experts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tier_changes_expert_created", "tier_changes", ["expert_id", "created_at"], unique=False
    )
    op.add_column("experts", sa.Column("calibration_score", sa.Float(), nullable=True))
    op.add_column(
        "experts",
        sa.Column("calibration_samples", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "experts", sa.Column("tier_updated_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("experts", "tier_updated_at")
    op.drop_column("experts", "calibration_samples")
    op.drop_column("experts", "calibration_score")
    op.drop_index("ix_tier_changes_expert_created", table_name="tier_changes")
    op.drop_table("tier_changes")
