"""Rubric versions: rows are immutable, queued tasks follow the newest version."""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from panelist.models import Rubric, RubricCriterion, Task, TaskStatus
from panelist.services import audit


class RubricError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def publish(
    db: Session, previous: Rubric, criteria: list[dict], actor: str
) -> tuple[Rubric, int, int]:
    """Create the next version, retarget untouched queued tasks, count what stays behind."""
    if previous.superseded_at is not None:
        raise RubricError(
            409, f"rubric {previous.name} version {previous.version} is already superseded"
        )
    latest = db.scalar(select(func.max(Rubric.version)).where(Rubric.name == previous.name))
    rubric = Rubric(name=previous.name, version=int(latest) + 1)
    rubric.criteria = [RubricCriterion(position=i, **c) for i, c in enumerate(criteria)]
    db.add(rubric)
    db.flush()
    previous.superseded_at = datetime.now(UTC)
    previous.superseded_by_id = rubric.id
    migrated = db.execute(
        update(Task)
        .where(
            Task.rubric_id == previous.id,
            Task.status == TaskStatus.queued,
            Task.grades_received == 0,
        )
        .values(rubric_id=rubric.id)
    ).rowcount
    open_on_previous = int(
        db.scalar(
            select(func.count(Task.id)).where(
                Task.rubric_id == previous.id,
                Task.status.in_((TaskStatus.queued, TaskStatus.assigned)),
            )
        )
        or 0
    )
    audit.record(
        db,
        actor,
        "rubric.published",
        "rubric",
        rubric.id,
        {
            "previous": str(previous.id),
            "version": rubric.version,
            "migrated_queued": migrated,
            "open_on_previous": open_on_previous,
        },
    )
    return rubric, migrated, open_on_previous


def superseded_among(db: Session, rubric_ids: set) -> Rubric | None:
    return db.scalar(
        select(Rubric).where(Rubric.id.in_(rubric_ids), Rubric.superseded_at.is_not(None))
    )
