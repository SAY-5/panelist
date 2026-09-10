"""consensus and adjudication

Revision ID: 7c41a0b52e63
Revises: 3d8dba8d69dd
Create Date: 2026-09-10 02:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c41a0b52e63"
down_revision: str | None = "3d8dba8d69dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_STATUS_BEFORE = ("queued", "assigned", "submitted", "approved", "rejected")
ROLE_BEFORE = ("expert", "reviewer", "admin")


def _shrink_enum(name: str, values: Sequence[str], table: str, column: str) -> None:
    quoted = ", ".join(f"'{v}'" for v in values)
    op.execute(f"ALTER TYPE {name} RENAME TO {name}_old")
    op.execute(f"CREATE TYPE {name} AS ENUM ({quoted})")
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {name} USING {column}::text::{name}"
    )
    op.execute(f"DROP TYPE {name}_old")


def upgrade() -> None:
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'adjudication' AFTER 'submitted'")
    op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'senior_reviewer' AFTER 'reviewer'")
    op.create_table(
        "consensus",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("agreed", "adjudicating", "adjudicated", name="consensus_status"),
            nullable=False,
        ),
        sa.Column("grade_count", sa.Integer(), nullable=False),
        sa.Column("spread", sa.Float(), nullable=False),
        sa.Column("tolerance", sa.Float(), nullable=False),
        sa.Column("delivered_grade_id", sa.UUID(), nullable=True),
        sa.Column("adjudicator_key_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["delivered_grade_id"], ["grades.id"]),
        sa.ForeignKeyConstraint(["adjudicator_key_id"], ["api_keys.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_consensus_status_created", "consensus", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_consensus_status_created", table_name="consensus")
    op.drop_table("consensus")
    op.execute("DROP TYPE IF EXISTS consensus_status")
    op.execute("UPDATE tasks SET status = 'submitted' WHERE status = 'adjudication'")
    _shrink_enum("task_status", TASK_STATUS_BEFORE, "tasks", "status")
    op.execute("UPDATE api_keys SET role = 'reviewer' WHERE role = 'senior_reviewer'")
    _shrink_enum("role", ROLE_BEFORE, "api_keys", "role")
