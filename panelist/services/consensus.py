"""Consensus over k graders, with adjudication by a senior reviewer when they disagree."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from panelist.config import get_settings
from panelist.metrics import CONSENSUS
from panelist.models import (
    Consensus,
    ConsensusStatus,
    Grade,
    Payout,
    Review,
    ReviewDecision,
    Task,
    TaskStatus,
)
from panelist.services import audit, calibration, payouts


class ConsensusError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class Decision:
    round: Consensus
    delivered: Payout | None
    outvoted: list[tuple[Grade, Payout | None]]


def grades_for(db: Session, task_id) -> list[Grade]:
    return list(
        db.scalars(
            select(Grade).where(Grade.task_id == task_id).order_by(Grade.submitted_at, Grade.id)
        ).all()
    )


def _closest_to_mean(grades: list[Grade]) -> Grade:
    mean = sum(g.weighted_score for g in grades) / len(grades)
    return min(grades, key=lambda g: (abs(g.weighted_score - mean), g.submitted_at, g.id))


def evaluate(db: Session, task: Task) -> Consensus | None:
    """Score the completed grades on a task: agree within tolerance, or open an adjudication."""
    settings = get_settings()
    grades = grades_for(db, task.id)
    if len(grades) < 2:
        return None
    scores = [g.weighted_score for g in grades]
    spread = max(scores) - min(scores)
    agreed = spread <= settings.consensus_tolerance
    row = Consensus(
        task_id=task.id,
        status=ConsensusStatus.agreed if agreed else ConsensusStatus.adjudicating,
        grade_count=len(grades),
        spread=spread,
        tolerance=settings.consensus_tolerance,
        delivered_grade_id=_closest_to_mean(grades).id if agreed else None,
        resolved_at=datetime.now(UTC) if agreed else None,
    )
    if not agreed:
        task.status = TaskStatus.adjudication
    db.add(row)
    db.flush()
    CONSENSUS.labels(outcome=row.status.value).inc()
    audit.record(
        db,
        "system",
        f"consensus.{row.status.value}",
        "task",
        task.id,
        {"spread": spread, "grades": len(grades)},
    )
    return row


def pending(db: Session, limit: int = 200) -> list[Consensus]:
    return list(
        db.scalars(
            select(Consensus)
            .where(Consensus.status == ConsensusStatus.adjudicating)
            .order_by(Consensus.created_at, Consensus.id)
            .limit(limit)
        ).all()
    )


def backlog(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(Consensus.id)).where(Consensus.status == ConsensusStatus.adjudicating)
        )
        or 0
    )


def _outvoted_payout(db: Session, grade: Grade, task: Task, actor: str):
    settings = get_settings()
    rule = settings.consensus_outvoted_payout
    if rule == "none":
        return None
    fraction = 1.0 if rule == "full" else settings.consensus_outvoted_rate
    return payouts.create_for_grade(db, grade, task, actor, fraction=fraction)


def resolve(db: Session, task_id, delivered_grade_id, reason: str, key_id, actor: str) -> Decision:
    """A senior reviewer picks the delivered grade; the others are recorded as outvoted."""
    row = db.scalar(select(Consensus).where(Consensus.task_id == task_id).with_for_update())
    if row is None:
        raise ConsensusError(404, "no consensus round for this task")
    if row.status != ConsensusStatus.adjudicating:
        raise ConsensusError(409, f"consensus is already {row.status.value}")
    grades = grades_for(db, task_id)
    delivered = next((g for g in grades if g.id == delivered_grade_id), None)
    if delivered is None:
        raise ConsensusError(422, "delivered grade is not one of the grades on this task")

    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    delivered_payout = None
    outvoted: list[tuple[Grade, Payout | None]] = []
    for grade in grades:
        chosen = grade.id == delivered.id
        db.add(
            Review(
                grade_id=grade.id,
                reviewer_key_id=key_id,
                decision=ReviewDecision.approve if chosen else ReviewDecision.reject,
                reason=reason if chosen else "outvoted in adjudication",
            )
        )
        db.flush()
        if chosen:
            delivered_payout = payouts.create_for_grade(db, grade, task, actor)
        else:
            outvoted.append((grade, _outvoted_payout(db, grade, task, actor)))
        calibration.update(db, grade.expert, actor)

    task.status = TaskStatus.approved
    row.status = ConsensusStatus.adjudicated
    row.delivered_grade_id = delivered.id
    row.adjudicator_key_id = key_id
    row.reason = reason
    row.resolved_at = datetime.now(UTC)
    CONSENSUS.labels(outcome=row.status.value).inc()
    audit.record(
        db,
        actor,
        "consensus.adjudicated",
        "task",
        task.id,
        {"delivered_grade_id": str(delivered.id), "outvoted": len(grades) - 1},
    )
    return Decision(round=row, delivered=delivered_payout, outvoted=outvoted)
