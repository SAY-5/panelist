from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select

from panelist.models import (
    Consensus,
    ConsensusStatus,
    Expert,
    ExpertStatus,
    Payout,
    PayoutStatus,
    Task,
    TaskStatus,
    Tier,
)

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
CONSENSUS = Counter("panelist_consensus_total", "Consensus rounds by outcome", ["outcome"])
TIER_CHANGES = Counter("panelist_tier_changes_total", "Automatic tier moves", ["direction"])
PAYOUT_BALANCE = Gauge("panelist_payout_balance_cents", "Payout ledger balance", ["status"])
ADJUDICATION_BACKLOG = Gauge("panelist_adjudication_backlog", "Tasks waiting for adjudication")
PAUSED_EXPERTS = Gauge("panelist_paused_experts", "Experts currently paused")
EXPERTS_BY_TIER = Gauge("panelist_experts_by_tier", "Experts by current tier", ["tier"])

# Tags that have had a queue depth gauge, so a tag whose queue drains reads 0 rather than vanishing.
_queue_tags: set[str] = set()


def refresh_gauges(db) -> None:
    rows = db.execute(
        select(func.unnest(Task.required_tags).label("tag"), func.count())
        .where(Task.status == TaskStatus.queued)
        .group_by("tag")
    ).all()
    seen = {tag for tag, _ in rows}
    for tag, count in rows:
        QUEUE_DEPTH.labels(tag=tag).set(count)
    for tag in _queue_tags - seen:
        QUEUE_DEPTH.labels(tag=tag).set(0)
    _queue_tags.update(seen)

    balances = db.execute(
        select(Payout.status, func.coalesce(func.sum(Payout.amount_cents), 0)).group_by(
            Payout.status
        )
    ).all()
    for status in PayoutStatus:
        PAYOUT_BALANCE.labels(status=status.value).set(0)
    for status, total in balances:
        PAYOUT_BALANCE.labels(status=status.value).set(int(total))

    ADJUDICATION_BACKLOG.set(
        db.scalar(
            select(func.count(Consensus.id)).where(Consensus.status == ConsensusStatus.adjudicating)
        )
        or 0
    )
    PAUSED_EXPERTS.set(
        db.scalar(select(func.count(Expert.id)).where(Expert.status == ExpertStatus.paused)) or 0
    )
    by_tier = dict(
        db.execute(select(Expert.tier, func.count(Expert.id)).group_by(Expert.tier)).all()
    )
    for tier in Tier:
        EXPERTS_BY_TIER.labels(tier=tier.value).set(int(by_tier.get(tier, 0)))
