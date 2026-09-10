import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Tier(enum.StrEnum):
    junior = "junior"
    senior = "senior"
    lead = "lead"


TIER_RANK = {Tier.junior: 0, Tier.senior: 1, Tier.lead: 2}


class ExpertStatus(enum.StrEnum):
    active = "active"
    paused = "paused"
    inactive = "inactive"


class TaskStatus(enum.StrEnum):
    queued = "queued"
    assigned = "assigned"
    submitted = "submitted"
    adjudication = "adjudication"
    approved = "approved"
    rejected = "rejected"


class ConsensusStatus(enum.StrEnum):
    agreed = "agreed"
    adjudicating = "adjudicating"
    adjudicated = "adjudicated"


class ReviewDecision(enum.StrEnum):
    approve = "approve"
    reject = "reject"


class PayoutStatus(enum.StrEnum):
    pending = "pending"
    withheld = "withheld"
    paid = "paid"


class Role(enum.StrEnum):
    expert = "expert"
    reviewer = "reviewer"
    senior_reviewer = "senior_reviewer"
    admin = "admin"


class Expert(Base):
    __tablename__ = "experts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    tier: Mapped[Tier] = mapped_column(Enum(Tier, name="tier"), default=Tier.junior)
    task_rate_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hourly_rate_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ExpertStatus] = mapped_column(
        Enum(ExpertStatus, name="expert_status"), default=ExpertStatus.active
    )
    served_count: Mapped[int] = mapped_column(Integer, default=0)
    calibration_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibration_samples: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tier_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_experts_tags", "tags", postgresql_using="gin"),)


class TierChange(Base):
    __tablename__ = "tier_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    expert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experts.id"))
    from_tier: Mapped[Tier] = mapped_column(Enum(Tier, name="tier"))
    to_tier: Mapped[Tier] = mapped_column(Enum(Tier, name="tier"))
    score: Mapped[float] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_tier_changes_expert_created", "expert_id", "created_at"),)


class Rubric(Base):
    __tablename__ = "rubrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rubrics.id"), nullable=True
    )

    criteria: Mapped[list["RubricCriterion"]] = relationship(
        back_populates="rubric", order_by="RubricCriterion.position", lazy="selectin"
    )

    __table_args__ = (UniqueConstraint("name", "version", name="uq_rubric_name_version"),)


class RubricCriterion(Base):
    __tablename__ = "rubric_criteria"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rubric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubrics.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(200))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    scale_min: Mapped[int] = mapped_column(Integer, default=1)
    scale_max: Mapped[int] = mapped_column(Integer, default=5)
    position: Mapped[int] = mapped_column(Integer, default=0)

    rubric: Mapped[Rubric] = relationship(back_populates="criteria")

    __table_args__ = (UniqueConstraint("rubric_id", "key", name="uq_criterion_key"),)


class RateCard(Base):
    __tablename__ = "rate_cards"

    tier: Mapped[Tier] = mapped_column(Enum(Tier, name="tier"), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    rate_cents: Mapped[int] = mapped_column(Integer)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    responses: Mapped[list] = mapped_column(JSONB, default=list)
    required_tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    task_type: Mapped[str] = mapped_column(String(64), default="single")
    min_tier: Mapped[Tier] = mapped_column(Enum(Tier, name="tier"), default=Tier.junior)
    rubric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubrics.id"))
    pinned_rubric_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rubrics.id"), nullable=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"), default=TaskStatus.queued
    )
    required_grades: Mapped[int] = mapped_column(Integer, default=1)
    grades_received: Mapped[int] = mapped_column(Integer, default=0)
    is_attention_check: Mapped[bool] = mapped_column(Boolean, default=False)
    expected_scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    assigned_expert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experts.id"), nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reclaim_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    rubric: Mapped[Rubric] = relationship(lazy="selectin", foreign_keys=[rubric_id])
    pinned_rubric: Mapped[Rubric | None] = relationship(
        lazy="selectin", foreign_keys=[pinned_rubric_id]
    )

    __table_args__ = (
        Index("ix_tasks_required_tags", "required_tags", postgresql_using="gin"),
        Index("ix_tasks_queue", "status", "priority", "deadline", "seq"),
        Index("ix_tasks_lease", "status", "lease_expires_at"),
    )


class Grade(Base):
    __tablename__ = "grades"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    expert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experts.id"))
    rubric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubrics.id"))
    scores_snapshot: Mapped[dict] = mapped_column(JSONB)
    rationale: Mapped[str] = mapped_column(Text)
    time_spent_seconds: Mapped[int] = mapped_column(Integer, default=0)
    weighted_score: Mapped[float] = mapped_column(Float)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    task: Mapped[Task] = relationship(lazy="selectin")
    expert: Mapped[Expert] = relationship(lazy="selectin")
    rubric: Mapped[Rubric] = relationship(lazy="selectin")
    scores: Mapped[list["GradeScore"]] = relationship(
        back_populates="grade", cascade="all, delete-orphan", lazy="selectin"
    )
    review: Mapped["Review | None"] = relationship(back_populates="grade", uselist=False)

    __table_args__ = (UniqueConstraint("task_id", "expert_id", name="uq_grade_task_expert"),)


class GradeScore(Base):
    __tablename__ = "grade_scores"

    grade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grades.id", ondelete="CASCADE"), primary_key=True
    )
    criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_criteria.id"), primary_key=True
    )
    score: Mapped[float] = mapped_column(Float)

    grade: Mapped[Grade] = relationship(back_populates="scores")
    criterion: Mapped[RubricCriterion] = relationship(lazy="selectin")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    grade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("grades.id"), unique=True)
    reviewer_key_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_keys.id"))
    decision: Mapped[ReviewDecision] = mapped_column(Enum(ReviewDecision, name="review_decision"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    grade: Mapped[Grade] = relationship(back_populates="review")


class Consensus(Base):
    __tablename__ = "consensus"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), unique=True)
    status: Mapped[ConsensusStatus] = mapped_column(Enum(ConsensusStatus, name="consensus_status"))
    grade_count: Mapped[int] = mapped_column(Integer)
    spread: Mapped[float] = mapped_column(Float)
    tolerance: Mapped[float] = mapped_column(Float)
    delivered_grade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("grades.id"), nullable=True
    )
    adjudicator_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[Task] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_consensus_status_created", "status", "created_at"),)


class PayoutPeriod(Base):
    __tablename__ = "payout_periods"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    label: Mapped[str] = mapped_column(String(64), unique=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    payouts: Mapped[list["Payout"]] = relationship(back_populates="period")


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    expert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experts.id"))
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    grade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("grades.id"), unique=True)
    tier: Mapped[Tier] = mapped_column(Enum(Tier, name="tier"))
    task_type: Mapped[str] = mapped_column(String(64))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[PayoutStatus] = mapped_column(
        Enum(PayoutStatus, name="payout_status"), default=PayoutStatus.pending
    )
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payout_periods.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    period: Mapped[PayoutPeriod | None] = relationship(back_populates="payouts")
    expert: Mapped[Expert] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("task_id", "expert_id", name="uq_payout_task_expert"),
        Index("ix_payouts_expert_status", "expert_id", "status"),
    )


class AttentionResult(Base):
    __tablename__ = "attention_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    expert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experts.id"))
    grade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("grades.id"))
    passed: Mapped[bool] = mapped_column(Boolean)
    max_deviation: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_attention_expert_created", "expert_id", "created_at"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"))
    expert_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experts.id"), nullable=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    expert: Mapped[Expert | None] = relationship(lazy="selectin")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    checksum: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(512))
    row_count: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
