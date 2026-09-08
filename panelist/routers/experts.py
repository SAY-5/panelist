import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, current_expert, issue_key, require_scopes
from panelist.db import get_db
from panelist.models import Expert, ExpertStatus, Role
from panelist.services import attention, audit, payouts

router = APIRouter(prefix="/experts", tags=["experts"])


@router.post("", response_model=schemas.ExpertOut, status_code=201)
def create_expert(
    body: schemas.ExpertCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("experts:write")),
):
    expert = Expert(**body.model_dump())
    db.add(expert)
    db.flush()
    audit.record(db, principal.actor, "expert.created", "expert", expert.id)
    db.commit()
    return expert


@router.get("/me", response_model=schemas.ExpertOut)
def me(expert: Expert = Depends(current_expert)):
    return expert


@router.get("/{expert_id}", response_model=schemas.ExpertOut)
def get_expert(
    expert_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("tasks:read")),
):
    expert = db.get(Expert, expert_id)
    if expert is None:
        raise HTTPException(404, "expert not found")
    return expert


@router.post("/{expert_id}/api-key", response_model=schemas.ApiKeyOut, status_code=201)
def create_expert_key(
    expert_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("experts:write")),
):
    expert = db.get(Expert, expert_id)
    if expert is None:
        raise HTTPException(404, "expert not found")
    key, raw = issue_key(db, Role.expert, expert_id=expert.id, label=expert.name)
    audit.record(db, principal.actor, "apikey.issued", "api_key", key.id)
    db.commit()
    return schemas.ApiKeyOut(id=key.id, role=key.role, expert_id=key.expert_id, label=key.label, key=raw)


@router.get("/{expert_id}/attention", response_model=schemas.AttentionSummary)
def attention_summary(
    expert_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("tasks:read")),
):
    expert = db.get(Expert, expert_id)
    if expert is None:
        raise HTTPException(404, "expert not found")
    total, passed = attention.lifetime_counts(db, expert.id)
    rate, _, window_n = attention.rolling_pass_rate(db, expert.id)
    return schemas.AttentionSummary(
        expert_id=expert.id,
        status=expert.status,
        checks_total=total,
        checks_passed=passed,
        rolling_window=window_n,
        rolling_pass_rate=rate,
        paused=expert.status == ExpertStatus.paused,
    )


@router.patch("/{expert_id}/status", response_model=schemas.ExpertOut)
def set_status(
    expert_id: uuid.UUID,
    body: schemas.ExpertStatusUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("experts:write")),
):
    """Reviewer-approved reinstatement releases withheld payouts."""
    expert = db.get(Expert, expert_id)
    if expert is None:
        raise HTTPException(404, "expert not found")
    previous = expert.status
    expert.status = body.status
    if previous == ExpertStatus.paused and body.status == ExpertStatus.active:
        payouts.release_withheld(db, expert, principal.actor)
    audit.record(
        db, principal.actor, "expert.status", "expert", expert.id, {"from": previous.value, "to": body.status.value}
    )
    db.commit()
    return expert
