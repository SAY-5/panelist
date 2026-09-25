import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from panelist.models import (
    ConsensusStatus,
    ExpertStatus,
    PayoutStatus,
    ReviewDecision,
    Role,
    TaskStatus,
    Tier,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Experts


class ExpertCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(min_length=1)
    tier: Tier = Tier.junior
    task_rate_cents: int | None = None


class ExpertOut(ORMModel):
    id: uuid.UUID
    name: str
    tags: list[str]
    tier: Tier
    task_rate_cents: int | None
    status: ExpertStatus
    served_count: int
    calibration_score: float | None = None
    calibration_samples: int = 0
    created_at: datetime


class ExpertStatusUpdate(BaseModel):
    status: ExpertStatus


class ApiKeyCreate(BaseModel):
    role: Role
    expert_id: uuid.UUID | None = None
    label: str = ""


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    role: Role
    expert_id: uuid.UUID | None
    label: str
    key: str


# Rubrics


class CriterionIn(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str
    weight: float = 1.0
    scale_min: int = 1
    scale_max: int = 5


class RubricCreate(BaseModel):
    name: str
    version: int = 1
    criteria: list[CriterionIn] = Field(min_length=1)


class CriterionOut(ORMModel):
    id: uuid.UUID
    key: str
    label: str
    weight: float
    scale_min: int
    scale_max: int
    position: int


class RubricOut(ORMModel):
    id: uuid.UUID
    name: str
    version: int
    criteria: list[CriterionOut]
    superseded_at: datetime | None = None
    superseded_by_id: uuid.UUID | None = None


class RubricPublish(BaseModel):
    criteria: list[CriterionIn] = Field(min_length=1)


class RubricPublishOut(BaseModel):
    rubric: RubricOut
    previous_version: int
    migrated_queued: int
    open_on_previous: int


class RateCardIn(BaseModel):
    tier: Tier
    task_type: str
    rate_cents: int = Field(ge=0)


# Tasks


class TaskCreate(BaseModel):
    prompt: str
    responses: list[dict] = Field(min_length=1)
    required_tags: list[str] = Field(min_length=1)
    task_type: str = "single"
    min_tier: Tier = Tier.junior
    rubric_id: uuid.UUID
    priority: int = 0
    deadline: datetime | None = None
    required_grades: int = Field(default=1, ge=1, le=5)
    is_attention_check: bool = False
    expected_scores: dict[str, float] | None = None
    external_ref: str | None = None


class TaskBulkCreate(BaseModel):
    tasks: list[TaskCreate] = Field(min_length=1, max_length=2000)


class TaskBulkOut(BaseModel):
    created: int
    ids: list[uuid.UUID]


class TaskExpertView(ORMModel):
    """What an expert sees. Never exposes attention-check fields."""

    id: uuid.UUID
    prompt: str
    responses: list[dict]
    task_type: str
    rubric: RubricOut
    deadline: datetime | None
    lease_expires_at: datetime | None


class TaskAdminView(ORMModel):
    id: uuid.UUID
    external_ref: str | None
    prompt: str
    responses: list[dict]
    required_tags: list[str]
    task_type: str
    min_tier: Tier
    rubric_id: uuid.UUID
    pinned_rubric_id: uuid.UUID | None
    priority: int
    deadline: datetime | None
    status: TaskStatus
    required_grades: int
    grades_received: int
    is_attention_check: bool
    expected_scores: dict | None
    assigned_expert_id: uuid.UUID | None
    assigned_at: datetime | None
    lease_expires_at: datetime | None
    reclaim_count: int
    created_at: datetime


class ReclaimOut(BaseModel):
    reclaimed: int


# Grades


class GradeCreate(BaseModel):
    task_id: uuid.UUID
    rubric_id: uuid.UUID | None = None
    scores: dict[str, float]
    rationale: str = Field(min_length=1)
    time_spent_seconds: int = Field(default=0, ge=0)


class GradeScoreOut(BaseModel):
    criterion_key: str
    score: float


class GradeOut(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID
    expert_id: uuid.UUID
    rubric_id: uuid.UUID
    scores_snapshot: dict
    rationale: str
    time_spent_seconds: int
    weighted_score: float
    submitted_at: datetime


# Reviews


class ReviewCreate(BaseModel):
    grade_id: uuid.UUID
    decision: ReviewDecision
    reason: str | None = None


class ReviewOut(ORMModel):
    id: uuid.UUID
    grade_id: uuid.UUID
    decision: ReviewDecision
    reason: str | None
    created_at: datetime
    payout_id: uuid.UUID | None = None
    payout_status: PayoutStatus | None = None


# Consensus and adjudication


class ConsensusGradeOut(BaseModel):
    grade_id: uuid.UUID
    expert_id: uuid.UUID
    expert_name: str
    weighted_score: float
    scores: dict
    rationale: str


class AdjudicationOut(BaseModel):
    task_id: uuid.UUID
    external_ref: str | None
    prompt: str
    status: ConsensusStatus
    grade_count: int
    spread: float
    tolerance: float
    opened_at: datetime
    grades: list[ConsensusGradeOut]


class AdjudicationDecision(BaseModel):
    delivered_grade_id: uuid.UUID
    reason: str = Field(min_length=1)


class OutvotedOut(BaseModel):
    grade_id: uuid.UUID
    expert_id: uuid.UUID
    payout_id: uuid.UUID | None
    amount_cents: int | None


class AdjudicationResult(BaseModel):
    task_id: uuid.UUID
    status: ConsensusStatus
    delivered_grade_id: uuid.UUID
    delivered_amount_cents: int
    outvoted_rule: str
    outvoted: list[OutvotedOut]


# Payouts


class PayoutOut(ORMModel):
    id: uuid.UUID
    expert_id: uuid.UUID
    task_id: uuid.UUID
    grade_id: uuid.UUID
    tier: Tier
    task_type: str
    amount_cents: int
    status: PayoutStatus
    period_id: uuid.UUID | None
    created_at: datetime
    paid_at: datetime | None


class PeriodClose(BaseModel):
    label: str = Field(min_length=1, max_length=64)


class PeriodOut(BaseModel):
    id: uuid.UUID
    label: str
    closed_at: datetime
    payout_count: int
    total_cents: int
    expert_count: int


class LedgerRow(BaseModel):
    expert_id: uuid.UUID
    expert_name: str
    status: PayoutStatus
    payout_count: int
    total_cents: int


class LedgerOut(BaseModel):
    rows: list[LedgerRow]
    totals_by_status: dict[str, int]
    grand_total_cents: int


# Attention


class AttentionSummary(BaseModel):
    expert_id: uuid.UUID
    status: ExpertStatus
    checks_total: int
    checks_passed: int
    rolling_window: int
    rolling_pass_rate: float | None
    paused: bool


class TierChangeOut(ORMModel):
    from_tier: Tier
    to_tier: Tier
    score: float
    samples: int
    created_at: datetime


class CalibrationOut(BaseModel):
    expert_id: uuid.UUID
    tier: Tier
    score: float | None
    samples: int
    window: int
    min_samples: int
    promote_at: float
    demote_at: float
    changes: list[TierChangeOut]


# Analytics


class CriterionStat(BaseModel):
    rubric_id: uuid.UUID
    rubric_name: str
    rubric_version: int
    criterion_key: str
    mean: float
    stddev: float | None
    n: int


class AgreementOut(BaseModel):
    expert_a: uuid.UUID
    expert_b: uuid.UUID
    shared_tasks: int
    compared_scores: int
    exact_agreement: float | None
    mean_abs_diff: float | None
    within_one: float | None


class TaskAgreementOut(BaseModel):
    task_id: uuid.UUID
    grades: int
    criteria: dict[str, dict]
    mean_pairwise_abs_diff: float | None


class ReliabilityOut(BaseModel):
    expert_id: uuid.UUID
    grades_total: int
    grades_approved: int
    grades_rejected: int
    approval_rate: float | None
    attention_pass_rate: float | None
    mean_abs_deviation_from_consensus: float | None


# Deliveries


class DeliveryOut(ORMModel):
    id: uuid.UUID
    version: int
    checksum: str
    location: str
    row_count: int
    size_bytes: int
    created_at: datetime


class DeliveryVerification(BaseModel):
    version: int
    location: str
    stored_checksum: str
    checksum: str
    row_count: int
    rows_read: int
    size_bytes: int
    bytes_read: int
    match: bool


# Operations


class PausedExpertOut(BaseModel):
    id: uuid.UUID
    name: str
    tier: Tier
    calibration_score: float | None


class PeriodStatusOut(BaseModel):
    last_label: str | None
    closed_at: datetime | None
    unbilled_payouts: int
    unbilled_cents: int
    withheld_cents: int


class OpsOverview(BaseModel):
    generated_at: datetime
    queued_by_tag: dict[str, int]
    tasks_by_status: dict[str, int]
    expired_leases: int
    paused_experts: list[PausedExpertOut]
    adjudication_backlog: int
    period: PeriodStatusOut
    last_delivery: DeliveryOut | None
