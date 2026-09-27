"""Grade submission and review decisions."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from panelist.metrics import GRADES
from panelist.models import (
    Expert,
    Grade,
    GradeScore,
    Review,
    ReviewDecision,
    Rubric,
    Task,
    TaskStatus,
)
from panelist.services import attention, audit, calibration, consensus, payouts
from panelist.services.errors import ServiceError


class GradingError(ServiceError):
    pass


def submit(
    db: Session,
    expert: Expert,
    task_id,
    scores: dict[str, float],
    rationale: str,
    time_spent_seconds: int,
    rubric_id=None,
) -> Grade:
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise GradingError(404, "task not found")
    if task.status != TaskStatus.assigned or task.assigned_expert_id != expert.id:
        raise GradingError(409, "task is not assigned to this expert")

    rubric = task.pinned_rubric or task.rubric
    if rubric_id is not None and rubric_id != rubric.id:
        raise GradingError(409, _pin_conflict(db.get(Rubric, rubric_id), rubric))
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
    # Attention checks are reusable: they go back to the queue for other experts.
    if task.is_attention_check or task.grades_received < task.required_grades:
        task.status = TaskStatus.queued
    else:
        task.status = TaskStatus.submitted
        consensus.evaluate(db, task)
    audit.record(db, f"expert:{expert.id}", "grade.submitted", "grade", grade.id)

    result = attention.evaluate(db, task, grade)
    if result is not None:
        if not result.passed:
            attention.enforce(db, expert)
        calibration.update(db, expert)
    return grade


def _requeue(db: Session, task: Task, rejected: Grade, actor: str) -> None:
    """A rejected single-grader task goes back to the queue for a different expert.

    The rejected grade stays on record and keeps its author from being served the task
    again; `grades_received` is put back so the next grade completes the task.
    """
    task.status = TaskStatus.queued
    task.grades_received = max(0, task.grades_received - 1)
    audit.record(
        db,
        actor,
        "task.requeued",
        "task",
        task.id,
        {"rejected_grade_id": str(rejected.id), "rejections": rejections(db, task.id)},
    )


def rejections(db: Session, task_id) -> int:
    """How many grades on the task a reviewer has rejected."""
    return int(
        db.scalar(
            select(func.count(Review.id))
            .join(Grade, Grade.id == Review.grade_id)
            .where(Grade.task_id == task_id, Review.decision == ReviewDecision.reject)
        )
        or 0
    )


def _pin_conflict(given: Rubric | None, pinned: Rubric) -> str:
    if given is None:
        return f"unknown rubric; task is pinned to version {pinned.version}"
    state = "superseded" if given.superseded_at else "not the pinned version"
    return f"rubric version {given.version} is {state}; task is pinned to version {pinned.version}"


def _reject_if_consensus_pending(task: Task) -> None:
    if task.is_attention_check or task.required_grades < 2:
        return
    if task.grades_received < task.required_grades:
        raise GradingError(409, f"task is waiting for {task.required_grades} grades")
    if task.status == TaskStatus.adjudication:
        raise GradingError(409, "task is awaiting adjudication")


def review(db: Session, reviewer_key_id, grade_id, decision: ReviewDecision, reason, actor: str):
    grade = db.scalar(select(Grade).where(Grade.id == grade_id).with_for_update())
    if grade is None:
        raise GradingError(404, "grade not found")
    if grade.review is not None:
        raise GradingError(409, "grade already reviewed")
    _reject_if_consensus_pending(grade.task)
    rec = Review(
        grade_id=grade.id, reviewer_key_id=reviewer_key_id, decision=decision, reason=reason
    )
    db.add(rec)
    db.flush()
    task = db.scalar(select(Task).where(Task.id == grade.task_id).with_for_update())
    payout = None
    if decision == ReviewDecision.approve:
        payout = payouts.create_for_grade(db, grade, task, actor)
    if not task.is_attention_check:
        if decision == ReviewDecision.approve:
            task.status = TaskStatus.approved
        elif task.required_grades == 1:
            _requeue(db, task, grade, actor)
        elif task.status != TaskStatus.approved:
            task.status = TaskStatus.rejected
    if decision == ReviewDecision.reject:
        consensus.redeliver(db, task, grade, actor)
    audit.record(db, actor, f"grade.{decision.value}", "grade", grade.id, {"reason": reason})
    calibration.update(db, grade.expert, actor)
    return rec, payout
