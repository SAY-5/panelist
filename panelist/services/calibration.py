"""Calibration: rolling agreement with reviewers and golden answers moves experts between tiers."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from panelist.config import get_settings
from panelist.models import (
    TIER_RANK,
    AttentionResult,
    Expert,
    Grade,
    Review,
    ReviewDecision,
    Tier,
    TierChange,
)
from panelist.services import audit

TIERS_ASCENDING = sorted(TIER_RANK, key=TIER_RANK.get)


def signals(db: Session, expert_id, window: int) -> list[bool]:
    """Newest-first agreement flags: reviewer approvals and passed golden checks."""
    reviews = db.execute(
        select(Review.created_at, Review.decision == ReviewDecision.approve)
        .join(Grade, Grade.id == Review.grade_id)
        .where(Grade.expert_id == expert_id)
        .order_by(Review.created_at.desc())
        .limit(window)
    ).all()
    checks = db.execute(
        select(AttentionResult.created_at, AttentionResult.passed)
        .where(AttentionResult.expert_id == expert_id)
        .order_by(AttentionResult.created_at.desc())
        .limit(window)
    ).all()
    merged = sorted(reviews + checks, key=lambda row: row[0], reverse=True)[:window]
    return [bool(agreed) for _, agreed in merged]


def score(db: Session, expert_id) -> tuple[float | None, int]:
    flags = signals(db, expert_id, get_settings().calibration_window)
    if not flags:
        return None, 0
    return sum(flags) / len(flags), len(flags)


def _step(tier: Tier, delta: int) -> Tier:
    index = TIERS_ASCENDING.index(tier) + delta
    return TIERS_ASCENDING[max(0, min(index, len(TIERS_ASCENDING) - 1))]


def update(db: Session, expert: Expert, actor: str = "system") -> TierChange | None:
    """Recompute the rolling score and move one tier when it clears a band edge."""
    settings = get_settings()
    rate, samples = score(db, expert.id)
    expert.calibration_score = rate
    expert.calibration_samples = samples
    if rate is None or samples < settings.calibration_min_samples:
        return None
    target = expert.tier
    if rate >= settings.calibration_promote_at:
        target = _step(expert.tier, 1)
    elif rate <= settings.calibration_demote_at:
        target = _step(expert.tier, -1)
    if target == expert.tier:
        return None
    change = TierChange(
        expert_id=expert.id, from_tier=expert.tier, to_tier=target, score=rate, samples=samples
    )
    expert.tier = target
    expert.tier_updated_at = datetime.now(UTC)
    db.add(change)
    db.flush()
    audit.record(
        db,
        actor,
        "expert.tier",
        "expert",
        expert.id,
        {"from": change.from_tier.value, "to": target.value, "score": rate, "samples": samples},
    )
    return change


def history(db: Session, expert_id) -> list[TierChange]:
    return db.scalars(
        select(TierChange)
        .where(TierChange.expert_id == expert_id)
        .order_by(TierChange.created_at, TierChange.id)
    ).all()
