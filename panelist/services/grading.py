"""Grade submission and review decisions."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from panelist.metrics import GRADES
from panelist.models import (
    Expert,
    Grade,
    GradeScore,
    Review,
    ReviewDecision,
    Task,
    TaskStatus,
)
from panelist.services import attention, audit, payouts


class GradingError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def submit(
    db: Session,
    expert: Expert,
    task_id,
    scores: dict[str, float],
    rationale: str,
    time_spent_seconds: int,
) -> Grade:
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise GradingError(404, "task not found")
    if task.status != TaskStatus.assigned or task.assigned_expert_id != expert.id:
        raise GradingError(409, "task is not assigned to this expert")

    rubric = task.rubric
    by_key = {c.key: c for c in rubric.criteria}
    missing = sorted(set(by_key) - set(scores))
    unknown = sorted(set(scores) - set(by_key))
    if missing or unknown:
        raise GradingError(422, f"scores mismatch rubric: missing={missing} unknown={unknown}")
    for key, value in scores.items():
        c = by_key[key]
        if not (c.scale_min <= value <= c.scale_max):
            raise GradingError(
                422, f"score for {key} must be within [{c.scale_min}, {c.scale_max}]"
            )

    total_weight = sum(c.weight for c in rubric.criteria) or 1.0
    weighted = sum(scores[c.key] * c.weight for c in rubric.criteria) / total_weight
    grade = Grade(
        task_id=task.id,
        expert_id=expert.id,
        rubric_id=rubric.id,
        scores_snapshot={k: float(v) for k, v in scores.items()},
        rationale=rationale,
        time_spent_seconds=time_spent_seconds,
        weighted_score=weighted,
    )
    grade.scores = [
        GradeScore(criterion_id=by_key[k].id, score=float(v)) for k, v in scores.items()
    ]
    db.add(grade)
    db.flush()
    GRADES.inc()

    task.grades_received += 1
    task.assigned_expert_id = None
    task.assigned_at = None
    task.lease_expires_at = None
    task.status = (
        TaskStatus.submitted if task.grades_received >= task.required_grades else TaskStatus.queued
    )
    audit.record(db, f"expert:{expert.id}", "grade.submitted", "grade", grade.id)

    result = attention.evaluate(db, task, grade)
    if result is not None and not result.passed:
        attention.enforce(db, expert)
    return grade


def review(db: Session, reviewer_key_id, grade_id, decision: ReviewDecision, reason, actor: str):
    grade = db.scalar(select(Grade).where(Grade.id == grade_id).with_for_update())
    if grade is None:
        raise GradingError(404, "grade not found")
    if grade.review is not None:
        raise GradingError(409, "grade already reviewed")
    rec = Review(
        grade_id=grade.id, reviewer_key_id=reviewer_key_id, decision=decision, reason=reason
    )
    db.add(rec)
    db.flush()
    task = db.scalar(select(Task).where(Task.id == grade.task_id).with_for_update())
    payout = None
    if decision == ReviewDecision.approve:
        task.status = TaskStatus.approved
        payout = payouts.create_for_grade(db, grade, task, actor)
    elif task.status not in (TaskStatus.approved,):
        task.status = TaskStatus.rejected
    audit.record(db, actor, f"grade.{decision.value}", "grade", grade.id, {"reason": reason})
    return rec, payout
