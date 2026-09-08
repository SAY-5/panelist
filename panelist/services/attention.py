"""Attention checks: golden tasks with hidden expected scores."""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from panelist.config import get_settings
from panelist.metrics import ATTENTION_CHECKS, ATTENTION_PASS_RATE, EXPERTS_PAUSED
from panelist.models import (
    AttentionResult,
    Expert,
    ExpertStatus,
    Grade,
    Payout,
    PayoutStatus,
    Task,
)
from panelist.services import audit


def evaluate(db: Session, task: Task, grade: Grade) -> AttentionResult | None:
    """Compare a grade against the task's hidden expected scores."""
    if not task.is_attention_check or not task.expected_scores:
        return None
    settings = get_settings()
    deviations = [
        abs(float(grade.scores_snapshot.get(key, 0)) - float(expected))
        for key, expected in task.expected_scores.items()
    ]
    max_dev = max(deviations) if deviations else 0.0
    passed = max_dev <= settings.attention_tolerance
    result = AttentionResult(
        task_id=task.id,
        expert_id=grade.expert_id,
        grade_id=grade.id,
        passed=passed,
        max_deviation=max_dev,
    )
    db.add(result)
    db.flush()
    ATTENTION_CHECKS.labels(result="pass" if passed else "fail").inc()
    _refresh_global_rate(db)
    return result


def rolling_pass_rate(db: Session, expert_id) -> tuple[float | None, int, int]:
    """Pass rate over the most recent `attention_window` checks: (rate, passed, total)."""
    window = get_settings().attention_window
    recent = db.scalars(
        select(AttentionResult.passed)
        .where(AttentionResult.expert_id == expert_id)
        .order_by(AttentionResult.created_at.desc(), AttentionResult.id.desc())
        .limit(window)
    ).all()
    if not recent:
        return None, 0, 0
    passed = sum(1 for p in recent if p)
    return passed / len(recent), passed, len(recent)


def enforce(db: Session, expert: Expert) -> bool:
    """Pause the expert and withhold pending payouts when the rolling rate is too low.

    Returns True when the expert was paused by this call.
    """
    settings = get_settings()
    rate, _, total = rolling_pass_rate(db, expert.id)
    if rate is None or total < settings.attention_min_checks or rate >= settings.attention_threshold:
        return False
    if expert.status == ExpertStatus.paused:
        return False
    expert.status = ExpertStatus.paused
    db.execute(
        update(Payout)
        .where(Payout.expert_id == expert.id, Payout.status == PayoutStatus.pending)
        .values(status=PayoutStatus.withheld)
    )
    EXPERTS_PAUSED.inc()
    audit.record(
        db,
        "system",
        "expert.paused",
        "expert",
        expert.id,
        {"rolling_pass_rate": rate, "checks": total},
    )
    return True


def lifetime_counts(db: Session, expert_id) -> tuple[int, int]:
    row = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(func.cast(AttentionResult.passed, __import__("sqlalchemy").Integer)), 0),
        ).where(AttentionResult.expert_id == expert_id)
    ).one()
    return int(row[0]), int(row[1])


def _refresh_global_rate(db: Session) -> None:
    total, passed = _global_counts(db)
    if total:
        ATTENTION_PASS_RATE.set(passed / total)


def _global_counts(db: Session) -> tuple[int, int]:
    from sqlalchemy import Integer, cast

    row = db.execute(
        select(func.count(), func.coalesce(func.sum(cast(AttentionResult.passed, Integer)), 0))
    ).one()
    return int(row[0]), int(row[1])
