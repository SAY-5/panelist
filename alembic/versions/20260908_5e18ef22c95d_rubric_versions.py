"""rubric versions

Revision ID: 5e18ef22c95d
Revises: 3dfe09ab9cdc
Create Date: 2026-09-08 16:29:21.128938

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5e18ef22c95d"
down_revision: str | None = "3dfe09ab9cdc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rubrics", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("rubrics", sa.Column("superseded_by_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_rubrics_superseded_by", "rubrics", "rubrics", ["superseded_by_id"], ["id"]
    )
    op.add_column("tasks", sa.Column("pinned_rubric_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_pinned_rubric", "tasks", "rubrics", ["pinned_rubric_id"], ["id"]
    )
    op.execute("UPDATE tasks SET pinned_rubric_id = rubric_id WHERE status = 'assigned'")


def downgrade() -> None:
    op.drop_constraint("fk_tasks_pinned_rubric", "tasks", type_="foreignkey")
    op.drop_column("tasks", "pinned_rubric_id")
    op.drop_constraint("fk_rubrics_superseded_by", "rubrics", type_="foreignkey")
    op.drop_column("rubrics", "superseded_by_id")
    op.drop_column("rubrics", "superseded_at")
