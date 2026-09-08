from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select

from panelist.models import PayoutStatus, Task, TaskStatus

QUEUE_DEPTH = Gauge("panelist_queue_depth", "Queued tasks by required tag", ["tag"])
ASSIGNMENT_LATENCY = Histogram(
    "panelist_assignment_latency_seconds",
    "Time to claim a task for an expert",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
CLAIMS = Counter("panelist_claims_total", "Claim outcomes", ["outcome"])
DOUBLE_ASSIGN_BLOCKED = Counter(
    "panelist_double_assignment_blocked_total", "Direct claims rejected because task was taken"
)
ATTENTION_CHECKS = Counter("panelist_attention_checks_total", "Attention checks", ["result"])
ATTENTION_PASS_RATE = Gauge("panelist_attention_pass_rate", "Global attention pass rate")
EXPERTS_PAUSED = Counter("panelist_experts_paused_total", "Experts paused by attention checks")
PAYOUTS = Counter("panelist_payouts_total", "Payouts created", ["status"])
PAYOUT_CENTS = Counter("panelist_payout_cents_total", "Payout amount created", ["status"])
GRADES = Counter("panelist_grades_total", "Grades submitted")
PAYOUT_BALANCE = Gauge("panelist_payout_balance_cents", "Payout ledger balance", ["status"])


def refresh_gauges(db) -> None:
    rows = db.execute(
        select(func.unnest(Task.required_tags).label("tag"), func.count())
        .where(Task.status == TaskStatus.queued)
        .group_by("tag")
    ).all()
    seen = set()
    for tag, count in rows:
        QUEUE_DEPTH.labels(tag=tag).set(count)
        seen.add(tag)
    for labels in list(QUEUE_DEPTH._metrics):
        if labels[0] not in seen:
            QUEUE_DEPTH.labels(tag=labels[0]).set(0)

    from panelist.models import Payout

    balances = db.execute(
        select(Payout.status, func.coalesce(func.sum(Payout.amount_cents), 0)).group_by(
            Payout.status
        )
    ).all()
    for status in PayoutStatus:
        PAYOUT_BALANCE.labels(status=status.value).set(0)
    for status, total in balances:
        PAYOUT_BALANCE.labels(status=status.value).set(int(total))
