"""Operational views: one overview of the running system and a scheduler tick."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from panelist.config import get_settings
from panelist.models import (
    Delivery,
    Expert,
    ExpertStatus,
    Grade,
    Payout,
    PayoutPeriod,
    PayoutStatus,
    Review,
    ReviewDecision,
    Task,
    TaskStatus,
)
from panelist.services import audit, calibration, consensus, routing


def _paused_experts(db: Session) -> list[Expert]:
    return list(
        db.scalars(
            select(Expert).where(Expert.status == ExpertStatus.paused).order_by(Expert.name)
        ).all()
    )


def _expired_leases(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(Task.id)).where(
                Task.status == TaskStatus.assigned, Task.lease_expires_at < datetime.now(UTC)
            )
        )
        or 0
    )


def _rejected_twice(db: Session) -> int:
    """Single-grader tasks a reviewer has sent back to the queue at least twice."""
    per_task = (
        select(Grade.task_id)
        .join(Review, Review.grade_id == Grade.id)
        .join(Task, Task.id == Grade.task_id)
        .where(
            Review.decision == ReviewDecision.reject,
            Task.required_grades == 1,
            Task.is_attention_check.is_(False),
        )
        .group_by(Grade.task_id)
        .having(func.count(Review.id) >= 2)
        .subquery()
    )
    return int(db.scalar(select(func.count()).select_from(per_task)) or 0)


def _period_status(db: Session) -> dict:
    last = db.scalar(select(PayoutPeriod).order_by(PayoutPeriod.closed_at.desc()).limit(1))
    rows = db.execute(
        select(
            Payout.status, func.count(Payout.id), func.coalesce(func.sum(Payout.amount_cents), 0)
        )
        .where(Payout.period_id.is_(None))
        .group_by(Payout.status)
    ).all()
    by_status = {status: (int(n), int(cents)) for status, n, cents in rows}
    pending = by_status.get(PayoutStatus.pending, (0, 0))
    withheld = by_status.get(PayoutStatus.withheld, (0, 0))
    return {
        "last_label": last.label if last else None,
        "closed_at": last.closed_at if last else None,
        "unbilled_payouts": pending[0],
        "unbilled_cents": pending[1],
        "withheld_cents": withheld[1],
    }


def overview(db: Session) -> dict:
    return {
        "generated_at": datetime.now(UTC),
        "queued_by_tag": routing.queue_depth_by_tag(db),
        "tasks_by_status": routing.queue_summary(db),
        "expired_leases": _expired_leases(db),
        "paused_experts": [
            {"id": e.id, "name": e.name, "tier": e.tier, "calibration_score": e.calibration_score}
            for e in _paused_experts(db)
        ],
        "adjudication_backlog": consensus.backlog(db),
        "period": _period_status(db),
        "last_delivery": db.scalar(select(Delivery).order_by(Delivery.version.desc()).limit(1)),
    }


def _days_since(moment: datetime | None) -> float | None:
    if moment is None:
        return None
    return (datetime.now(UTC) - moment).total_seconds() / 86400


def _reminders(
    settings, uncalibrated: int, period: dict, backlog: int, rejected_twice: int
) -> list[str]:
    age = _days_since(period["closed_at"])
    out = []
    if uncalibrated:
        out.append(
            f"{uncalibrated} experts have fewer than "
            f"{settings.calibration_min_samples} calibration signals"
        )
    if period["unbilled_payouts"]:
        since = "never closed" if age is None else f"closed {age:.1f} days ago"
        out.append(
            f"{period['unbilled_payouts']} payouts worth {period['unbilled_cents']} cents "
            f"are not in a statement; last period {since}"
        )
    if backlog:
        out.append(f"{backlog} tasks are waiting for adjudication")
    if rejected_twice:
        out.append(f"{rejected_twice} tasks have been rejected twice and are back in the queue")
    return out


def tick(db: Session, actor: str = "system:tick") -> dict:
    """Reclaim expired leases, refresh calibration, and report what needs attention."""
    settings = get_settings()
    reclaimed = routing.reclaim_expired(db)
    experts = list(db.scalars(select(Expert).order_by(Expert.name)).all())
    moves = []
    uncalibrated = 0
    for expert in experts:
        change = calibration.update(db, expert, actor)
        if change is not None:
            moves.append(
                {
                    "expert": expert.name,
                    "from_tier": change.from_tier.value,
                    "to_tier": change.to_tier.value,
                }
            )
        if expert.calibration_samples < settings.calibration_min_samples:
            uncalibrated += 1

    period = _period_status(db)
    backlog = consensus.backlog(db)
    rejected_twice = _rejected_twice(db)
    audit.record(
        db,
        actor,
        "ops.tick",
        "system",
        "tick",
        {"reclaimed": reclaimed, "tier_changes": len(moves)},
    )
    age = _days_since(period["closed_at"])
    return {
        "reclaimed": reclaimed,
        "experts_scored": len(experts),
        "tier_changes": moves,
        "uncalibrated_experts": uncalibrated,
        "adjudication_backlog": backlog,
        "unbilled_payouts": period["unbilled_payouts"],
        "unbilled_cents": period["unbilled_cents"],
        "days_since_period_close": None if age is None else round(age, 2),
        "rejected_twice": rejected_twice,
        "reminders": _reminders(settings, uncalibrated, period, backlog, rejected_twice),
    }
