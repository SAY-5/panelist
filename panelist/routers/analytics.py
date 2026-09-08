import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, require_scopes
from panelist.db import get_db
from panelist.services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/criteria", response_model=list[schemas.CriterionStat])
def criteria(
    rubric_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("analytics:read")),
):
    return analytics.criterion_means(db, rubric_id)


@router.get("/agreement", response_model=schemas.AgreementOut)
def agreement(
    expert_a: uuid.UUID,
    expert_b: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("analytics:read")),
):
    return analytics.pair_agreement(db, expert_a, expert_b)


@router.get("/agreement/global")
def agreement_global(
    db: Session = Depends(get_db), _: Principal = Depends(require_scopes("analytics:read"))
):
    return analytics.global_agreement(db)


@router.get("/tasks/{task_id}/agreement", response_model=schemas.TaskAgreementOut)
def task_agreement(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("analytics:read")),
):
    return analytics.task_agreement(db, task_id)


@router.get("/experts/{expert_id}/reliability", response_model=schemas.ReliabilityOut)
def reliability(
    expert_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("analytics:read")),
):
    return analytics.expert_reliability(db, expert_id)
