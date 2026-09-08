"""Payouts: one per approved grade, batched into periods, totals derived from the ledger."""

import csv
import io
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from panelist.metrics import PAYOUT_CENTS, PAYOUTS
from panelist.models import (
    Expert,
    ExpertStatus,
    Grade,
    Payout,
    PayoutPeriod,
    PayoutStatus,
    RateCard,
    Task,
    Tier,
)
from panelist.services import audit


class PayoutError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def rate_for(db: Session, expert: Expert, task_type: str) -> int:
    """Expert override wins, then the rate card for (tier, task_type), then (tier, 'default')."""
    if expert.task_rate_cents is not None:
        return expert.task_rate_cents
    card = db.get(RateCard, (expert.tier, task_type)) or db.get(RateCard, (expert.tier, "default"))
    if card is None:
        raise PayoutError(422, f"no rate configured for tier={expert.tier.value} type={task_type}")
    return card.rate_cents


def create_for_grade(db: Session, grade: Grade, task: Task, actor: str) -> Payout:
    expert = grade.expert
    existing = db.scalar(select(Payout).where(Payout.grade_id == grade.id))
    if existing is not None:
        return existing
    amount = rate_for(db, expert, task.task_type)
    status = (
        PayoutStatus.withheld if expert.status == ExpertStatus.paused else PayoutStatus.pending
    )
    payout = Payout(
        expert_id=expert.id,
        task_id=task.id,
        grade_id=grade.id,
        tier=expert.tier,
        task_type=task.task_type,
        amount_cents=amount,
        status=status,
    )
    db.add(payout)
    db.flush()
    PAYOUTS.labels(status=status.value).inc()
    PAYOUT_CENTS.labels(status=status.value).inc(amount)
    audit.record(db, actor, "payout.created", "payout", payout.id, {"amount_cents": amount})
    return payout


def release_withheld(db: Session, expert: Expert, actor: str) -> int:
    result = db.execute(
        update(Payout)
        .where(Payout.expert_id == expert.id, Payout.status == PayoutStatus.withheld)
        .values(status=PayoutStatus.pending)
    )
    audit.record(db, actor, "payout.released", "expert", expert.id, {"count": result.rowcount})
    return result.rowcount


def close_period(db: Session, label: str, actor: str) -> PayoutPeriod:
    if db.scalar(select(PayoutPeriod).where(PayoutPeriod.label == label)):
        raise PayoutError(409, f"period {label} already closed")
    period = PayoutPeriod(label=label)
    db.add(period)
    db.flush()
    now = datetime.now(UTC)
    db.execute(
        update(Payout)
        .where(Payout.status == PayoutStatus.pending, Payout.period_id.is_(None))
        .values(status=PayoutStatus.paid, period_id=period.id, paid_at=now)
    )
    audit.record(db, actor, "period.closed", "payout_period", period.id, {"label": label})
    return period


def period_totals(db: Session, period: PayoutPeriod) -> dict:
    row = db.execute(
        select(
            func.count(Payout.id),
            func.coalesce(func.sum(Payout.amount_cents), 0),
            func.count(func.distinct(Payout.expert_id)),
        ).where(Payout.period_id == period.id)
    ).one()
    return {
        "id": period.id,
        "label": period.label,
        "closed_at": period.closed_at,
        "payout_count": int(row[0]),
        "total_cents": int(row[1]),
        "expert_count": int(row[2]),
    }


def ledger(db: Session) -> dict:
    rows = db.execute(
        select(
            Payout.expert_id,
            Expert.name,
            Payout.status,
            func.count(Payout.id),
            func.coalesce(func.sum(Payout.amount_cents), 0),
        )
        .join(Expert, Expert.id == Payout.expert_id)
        .group_by(Payout.expert_id, Expert.name, Payout.status)
        .order_by(Expert.name, Payout.status)
    ).all()
    out_rows = [
        {
            "expert_id": eid,
            "expert_name": name,
            "status": status,
            "payout_count": int(n),
            "total_cents": int(total),
        }
        for eid, name, status, n, total in rows
    ]
    by_status = {s.value: 0 for s in PayoutStatus}
    for r in out_rows:
        by_status[r["status"].value] += r["total_cents"]
    return {
        "rows": out_rows,
        "totals_by_status": by_status,
        "grand_total_cents": sum(by_status.values()),
    }


def period_csv(db: Session, period: PayoutPeriod) -> str:
    rows = db.execute(
        select(Payout, Expert.name)
        .join(Expert, Expert.id == Payout.expert_id)
        .where(Payout.period_id == period.id)
        .order_by(Expert.name, Payout.created_at)
    ).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["period", "payout_id", "expert_id", "expert_name", "task_id", "tier", "task_type",
         "amount_cents", "paid_at"]
    )
    for payout, name in rows:
        writer.writerow(
            [
                period.label,
                payout.id,
                payout.expert_id,
                name,
                payout.task_id,
                payout.tier.value,
                payout.task_type,
                payout.amount_cents,
                payout.paid_at.isoformat() if payout.paid_at else "",
            ]
        )
    return buf.getvalue()


def seed_rate_card(db: Session, rates: dict[Tier, dict[str, int]]) -> None:
    for tier, by_type in rates.items():
        for task_type, cents in by_type.items():
            db.merge(RateCard(tier=tier, task_type=task_type, rate_cents=cents))
