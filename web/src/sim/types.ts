export type Tier = "junior" | "senior" | "lead";
export const TIER_RANK: Record<Tier, number> = { junior: 0, senior: 1, lead: 2 };

export type ExpertStatus = "active" | "paused" | "inactive";
export type TaskStatus = "queued" | "assigned" | "submitted" | "approved" | "rejected";
export type ReviewDecision = "approve" | "reject";
export type PayoutStatus = "pending" | "withheld" | "paid";

export interface Criterion {
  key: string;
  label: string;
  weight: number;
  scaleMin: number;
  scaleMax: number;
  position: number;
}

export interface Rubric {
  id: string;
  name: string;
  version: number;
  criteria: Criterion[];
}

export interface RateCard {
  tier: Tier;
  taskType: string;
  rateCents: number;
}

export interface Expert {
  id: string;
  name: string;
  tags: string[];
  tier: Tier;
  taskRateCents: number | null;
  status: ExpertStatus;
  servedCount: number;
}

export interface Response {
  model: string;
  text: string;
}

export interface Task {
  id: string;
  seq: number;
  externalRef: string;
  prompt: string;
  responses: Response[];
  requiredTags: string[];
  taskType: string;
  minTier: Tier;
  rubricId: string;
  priority: number;
  deadline: number | null;
  status: TaskStatus;
  requiredGrades: number;
  gradesReceived: number;
  isAttentionCheck: boolean;
  expectedScores: Record<string, number> | null;
  assignedExpertId: string | null;
  assignedAt: number | null;
  leaseExpiresAt: number | null;
  reclaimCount: number;
}

export interface Grade {
  id: string;
  seq: number;
  taskId: string;
  expertId: string;
  rubricId: string;
  scores: Record<string, number>;
  rationale: string;
  timeSpentSeconds: number;
  weightedScore: number;
  submittedAt: number;
  review: Review | null;
}

export interface Review {
  gradeId: string;
  decision: ReviewDecision;
  reason: string | null;
  reviewedAt: number;
}

export interface Payout {
  id: string;
  seq: number;
  expertId: string;
  taskId: string;
  gradeId: string;
  tier: Tier;
  taskType: string;
  amountCents: number;
  status: PayoutStatus;
  periodId: string | null;
  paidAt: number | null;
}

export interface PayoutPeriod {
  id: string;
  label: string;
  closedAt: number;
}

export interface AttentionResult {
  seq: number;
  taskId: string;
  expertId: string;
  gradeId: string;
  passed: boolean;
  maxDeviation: number;
  at: number;
}

export interface Delivery {
  version: number;
  checksum: string;
  location: string;
  rowCount: number;
  sizeBytes: number;
}

export interface AuditEvent {
  seq: number;
  at: number;
  actor: string;
  action: string;
  entity: string;
  entityId: string;
  detail: Record<string, unknown> | null;
}

export interface Settings {
  leaseSeconds: number;
  attentionFraction: number;
  attentionWindow: number;
  attentionMinChecks: number;
  attentionThreshold: number;
  attentionTolerance: number;
  deliveryBucket: string;
}

export const PRODUCTION_SETTINGS: Settings = {
  leaseSeconds: 900,
  attentionFraction: 0.1,
  attentionWindow: 10,
  attentionMinChecks: 3,
  attentionThreshold: 0.7,
  attentionTolerance: 1.0,
  deliveryBucket: "panelist-deliveries",
};

/** sim/demo.py overrides: short leases, one in five serves golden, two checks can pause. */
export const DEMO_SETTINGS: Settings = {
  ...PRODUCTION_SETTINGS,
  leaseSeconds: 3,
  attentionFraction: 0.2,
  attentionMinChecks: 2,
};
