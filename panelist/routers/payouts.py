import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from panelist import schemas
from panelist.auth import Principal, require_scopes
from panelist.db import get_db
from panelist.models import Payout, PayoutPeriod, PayoutStatus
from panelist.services import payouts

router = APIRouter(prefix="/payouts", tags=["payouts"])


@router.get("", response_model=list[schemas.PayoutOut])
def list_payouts(
    expert_id: uuid.UUID | None = None,
    status: PayoutStatus | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("payouts:read")),
):
    stmt = select(Payout).order_by(Payout.created_at).limit(min(limit, 5000))
    if expert_id:
        stmt = stmt.where(Payout.expert_id == expert_id)
    if status:
        stmt = stmt.where(Payout.status == status)
    return db.scalars(stmt).all()


@router.get("/ledger", response_model=schemas.LedgerOut)
def ledger(db: Session = Depends(get_db), _: Principal = Depends(require_scopes("payouts:read"))):
    return payouts.ledger(db)


@router.post("/periods/close", response_model=schemas.PeriodOut, status_code=201)
def close_period(
    body: schemas.PeriodClose,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scopes("payouts:write")),
):
    try:
        period = payouts.close_period(db, body.label, principal.actor)
    except payouts.PayoutError as e:
        db.rollback()
        raise HTTPException(e.status_code, e.detail) from e
    db.commit()
    return payouts.period_totals(db, period)


@router.get("/periods/{period_id}", response_model=schemas.PeriodOut)
def get_period(
    period_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("payouts:read")),
):
    period = db.get(PayoutPeriod, period_id)
    if period is None:
        raise HTTPException(404, "period not found")
    return payouts.period_totals(db, period)


@router.get("/periods/{period_id}/export.csv")
def export_period(
    period_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_scopes("payouts:read")),
):
    period = db.get(PayoutPeriod, period_id)
    if period is None:
        raise HTTPException(404, "period not found")
    body = payouts.period_csv(db, period)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="payouts-{period.label}.csv"'},
    )
