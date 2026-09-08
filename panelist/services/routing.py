"""Queue routing: claim tasks by expertise tag with row-level locking."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, func, select, text, update
from sqlalchemy.orm import Session

from panelist.config import get_settings
from panelist.metrics import CLAIMS, DOUBLE_ASSIGN_BLOCKED
from panelist.models import TIER_RANK, Expert, ExpertStatus, Grade, Task, TaskStatus, Tier
from panelist.services import audit


class ClaimError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC)


def _eligible_tiers(tier: Tier) -> list[Tier]:
    rank = TIER_RANK[tier]
    return [t for t, r in TIER_RANK.items() if r <= rank]


def _eligibility(expert: Expert):
    """Filter for tasks this expert may take: tag overlap, tier gate, not already graded."""
    already_graded = exists().where(and_(Grade.task_id == Task.id, Grade.expert_id == expert.id))
    return and_(
        Task.status == TaskStatus.queued,
        Task.required_tags.overlap(expert.tags),
        Task.min_tier.in_(_eligible_tiers(expert.tier)),
        ~already_graded,
    )


def reclaim_expired(db: Session) -> int:
    """Return leased tasks whose lease expired back to the queue."""
    result = db.execute(
        update(Task)
        .where(Task.status == TaskStatus.assigned, Task.lease_expires_at < _now())
        .values(
            status=TaskStatus.queued,
            assigned_expert_id=None,
            assigned_at=None,
            lease_expires_at=None,
            reclaim_count=Task.reclaim_count + 1,
        )
    )
    return result.rowcount


def _assign(db: Session, task: Task, expert: Expert) -> Task:
    settings = get_settings()
    now = _now()
    task.status = TaskStatus.assigned
    task.assigned_expert_id = expert.id
    task.assigned_at = now
    task.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
    expert.served_count = (expert.served_count or 0) + 1
    audit.record(
        db,
        f"expert:{expert.id}",
        "task.assigned",
        "task",
        task.id,
        {"lease_expires_at": task.lease_expires_at.isoformat()},
    )
    return task


def _pick(db: Session, expert: Expert, want_attention: bool | None):
    stmt = (
        select(Task)
        .where(_eligibility(expert))
        .order_by(
            Task.priority.desc(),
            Task.deadline.asc().nulls_last(),
            Task.created_at.asc(),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if want_attention is not None:
        stmt = stmt.where(Task.is_attention_check.is_(want_attention))
    return db.scalar(stmt)


def claim_next(db: Session, expert: Expert) -> Task | None:
    """Claim the highest-priority eligible task for this expert.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent claimants never
    receive the same row. Every Nth serve for an expert prefers an attention
    check (N = 1 / attention_fraction); the expert cannot tell the difference.
    """
    if expert.status != ExpertStatus.active:
        raise ClaimError(423, f"expert is {expert.status.value}")
    settings = get_settings()
    reclaim_expired(db)

    period = max(1, round(1 / settings.attention_fraction)) if settings.attention_fraction else 0
    prefer_attention = period > 0 and (expert.served_count + 1) % period == 0

    task = None
    if prefer_attention:
        task = _pick(db, expert, want_attention=True)
    if task is None:
        task = _pick(db, expert, want_attention=False)
    if task is None and not prefer_attention:
        task = _pick(db, expert, want_attention=True)
    if task is None:
        CLAIMS.labels(outcome="empty").inc()
        return None
    CLAIMS.labels(outcome="assigned").inc()
    return _assign(db, task, expert)


def claim_by_id(db: Session, expert: Expert, task_id) -> Task:
    """Claim a specific task. Rejected if another expert already holds it."""
    if expert.status != ExpertStatus.active:
        raise ClaimError(423, f"expert is {expert.status.value}")
    reclaim_expired(db)
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update(skip_locked=True))
    if task is None:
        exists_row = db.scalar(select(Task.id).where(Task.id == task_id))
        if exists_row is None:
            raise ClaimError(404, "task not found")
        DOUBLE_ASSIGN_BLOCKED.inc()
        raise ClaimError(409, "task is being claimed by another expert")
    if task.status != TaskStatus.queued:
        DOUBLE_ASSIGN_BLOCKED.inc()
        raise ClaimError(409, f"task is {task.status.value}")
    if not set(task.required_tags) & set(expert.tags):
        raise ClaimError(403, "task requires expertise the expert does not have")
    if TIER_RANK[task.min_tier] > TIER_RANK[expert.tier]:
        raise ClaimError(403, "task requires a higher tier")
    CLAIMS.labels(outcome="assigned").inc()
    return _assign(db, task, expert)


def release(db: Session, expert: Expert, task_id) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise ClaimError(404, "task not found")
    if task.status != TaskStatus.assigned or task.assigned_expert_id != expert.id:
        raise ClaimError(409, "task is not assigned to this expert")
    task.status = TaskStatus.queued
    task.assigned_expert_id = None
    task.assigned_at = None
    task.lease_expires_at = None
    audit.record(db, f"expert:{expert.id}", "task.released", "task", task.id)
    return task


def queue_depth_by_tag(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(func.unnest(Task.required_tags).label("tag"), func.count())
        .where(Task.status == TaskStatus.queued)
        .group_by(text("tag"))
    ).all()
    return {tag: int(n) for tag, n in rows}


def queue_summary(db: Session) -> dict[str, int]:
    rows = db.execute(select(Task.status, func.count()).group_by(Task.status)).all()
    out = {s.value: 0 for s in TaskStatus}
    for status, n in rows:
        out[status.value] = int(n)
    return out
