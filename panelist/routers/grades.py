import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, current_expert, require_scopes
from panelist.db import get_db
from panelist.models import Expert, Grade, Review, Task, TaskStatus
from panelist.services import grading

router = APIRouter(tags=["grades"])


@router.post("/grades", response_model=schemas.GradeOut, status_code=201)
def submit_grade(
    body: schemas.GradeCreate,
    db: Session = Depends(get_db),
    expert: Expert = Depends(current_expert),
):
    grade = grading.submit(
        db,
        expert,
        body.task_id,
        body.scores,
        body.rationale,
        body.time_spent_seconds,
        rubric_id=body.rubric_id,
    )
    db.commit()
    return grade


@router.get("/grades/{grade_id}", response_model=schemas.GradeOut)
def get_grade(
    grade_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("grades:read")),
):
    grade = db.get(Grade, grade_id)
    if grade is None:
        raise HTTPException(404, "grade not found")
    return grade


@router.get("/grades", response_model=list[schemas.GradeOut])
def list_unreviewed(
    limit: int = 200,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("grades:read")),
):
    stmt = (
        select(Grade)
        .outerjoin(Review, Review.grade_id == Grade.id)
        .join(Task, Task.id == Grade.task_id)
        .where(
            Review.id.is_(None),
            Task.status != TaskStatus.adjudication,
            Task.grades_received >= Task.required_grades,
        )
        .order_by(Grade.submitted_at)
        .limit(min(limit, 1000))
    )
    return db.scalars(stmt).all()


@router.post("/reviews", response_model=schemas.ReviewOut, status_code=201)
def create_review(
    body: schemas.ReviewCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("reviews:write")),
):
    rec, payout = grading.review(
        db,
        uuid.UUID(principal.key_id),
        body.grade_id,
        body.decision,
        body.reason,
        principal.actor,
    )
    db.commit()
    out = schemas.ReviewOut.model_validate(rec)
    if payout is not None:
        out.payout_id = payout.id
        out.payout_status = payout.status
    return out
