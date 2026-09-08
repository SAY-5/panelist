"""initial schema

Revision ID: 3dfe09ab9cdc
Revises:
Create Date: 2026-09-08 15:25:50.531985

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "3dfe09ab9cdc"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "deliveries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("location", sa.String(length=512), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    op.create_table(
        "experts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("tier", sa.Enum("junior", "senior", "lead", name="tier"), nullable=False),
        sa.Column("task_rate_cents", sa.Integer(), nullable=True),
        sa.Column("hourly_rate_cents", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.Enum("active", "paused", "inactive", name="expert_status"), nullable=False
        ),
        sa.Column("served_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experts_tags", "experts", ["tags"], unique=False, postgresql_using="gin")
    op.create_table(
        "payout_periods",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column(
            "closed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label"),
    )
    op.create_table(
        "rate_cards",
        sa.Column("tier", sa.Enum("junior", "senior", "lead", name="tier"), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("rate_cents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("tier", "task_type"),
    )
    op.create_table(
        "rubrics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_rubric_name_version"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("role", sa.Enum("expert", "reviewer", "admin", name="role"), nullable=False),
        sa.Column("expert_id", sa.UUID(), nullable=True),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["expert_id"],
            ["experts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_table(
        "rubric_criteria",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("rubric_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("scale_min", sa.Integer(), nullable=False),
        sa.Column("scale_max", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["rubric_id"], ["rubrics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rubric_id", "key", name="uq_criterion_key"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("responses", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("min_tier", sa.Enum("junior", "senior", "lead", name="tier"), nullable=False),
        sa.Column("rubric_id", sa.UUID(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum("queued", "assigned", "submitted", "approved", "rejected", name="task_status"),
            nullable=False,
        ),
        sa.Column("required_grades", sa.Integer(), nullable=False),
        sa.Column("grades_received", sa.Integer(), nullable=False),
        sa.Column("is_attention_check", sa.Boolean(), nullable=False),
        sa.Column("expected_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("assigned_expert_id", sa.UUID(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reclaim_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_expert_id"],
            ["experts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["rubric_id"],
            ["rubrics.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
    )
    op.create_index("ix_tasks_lease", "tasks", ["status", "lease_expires_at"], unique=False)
    op.create_index(
        "ix_tasks_queue", "tasks", ["status", "priority", "deadline", "seq"], unique=False
    )
    op.create_index(
        "ix_tasks_required_tags", "tasks", ["required_tags"], unique=False, postgresql_using="gin"
    )
    op.create_table(
        "grades",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("expert_id", sa.UUID(), nullable=False),
        sa.Column("rubric_id", sa.UUID(), nullable=False),
        sa.Column("scores_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("time_spent_seconds", sa.Integer(), nullable=False),
        sa.Column("weighted_score", sa.Float(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["expert_id"],
            ["experts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["rubric_id"],
            ["rubrics.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "expert_id", name="uq_grade_task_expert"),
    )
    op.create_table(
        "attention_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("expert_id", sa.UUID(), nullable=False),
        sa.Column("grade_id", sa.UUID(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("max_deviation", sa.Float(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["grade_id"],
            ["grades.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_attention_expert_created",
        "attention_results",
        ["expert_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "grade_scores",
        sa.Column("grade_id", sa.UUID(), nullable=False),
        sa.Column("criterion_id", sa.UUID(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["criterion_id"],
            ["rubric_criteria.id"],
        ),
        sa.ForeignKeyConstraint(["grade_id"], ["grades.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("grade_id", "criterion_id"),
    )
    op.create_table(
        "payouts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("expert_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("grade_id", sa.UUID(), nullable=False),
        sa.Column("tier", sa.Enum("junior", "senior", "lead", name="tier"), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.Enum("pending", "withheld", "paid", name="payout_status"), nullable=False
        ),
        sa.Column("period_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["expert_id"],
            ["experts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["grade_id"],
            ["grades.id"],
        ),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["payout_periods.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grade_id"),
        sa.UniqueConstraint("task_id", "expert_id", name="uq_payout_task_expert"),
    )
    op.create_index("ix_payouts_expert_status", "payouts", ["expert_id", "status"], unique=False)
    op.create_table(
        "reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grade_id", sa.UUID(), nullable=False),
        sa.Column("reviewer_key_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.Enum("approve", "reject", name="review_decision"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["grade_id"],
            ["grades.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_key_id"],
            ["api_keys.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grade_id"),
    )


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_index("ix_payouts_expert_status", table_name="payouts")
    op.drop_table("payouts")
    op.drop_table("grade_scores")
    op.drop_index("ix_attention_expert_created", table_name="attention_results")
    op.drop_table("attention_results")
    op.drop_table("grades")
    op.drop_index("ix_tasks_required_tags", table_name="tasks", postgresql_using="gin")
    op.drop_index("ix_tasks_queue", table_name="tasks")
    op.drop_index("ix_tasks_lease", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("rubric_criteria")
    op.drop_table("api_keys")
    op.drop_table("rubrics")
    op.drop_table("rate_cards")
    op.drop_table("payout_periods")
    op.drop_index("ix_experts_tags", table_name="experts", postgresql_using="gin")
    op.drop_table("experts")
    op.drop_table("deliveries")
    op.drop_table("audit_events")
    for enum_name in (
        "tier",
        "expert_status",
        "task_status",
        "review_decision",
        "payout_status",
        "role",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
