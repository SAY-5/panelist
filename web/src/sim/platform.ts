/**
 * In-memory port of the Panelist service layer: routing with row locks and leases,
 * rubric grading, attention checks, reviews, payouts, aggregates and delivery export.
 * Every rule here mirrors a function in panelist/services/*.py.
 */

import { Clock } from "./clock";
import { Rng } from "./prng";
import { sha256Hex, utf8Length } from "./sha256";
import {
  AttentionResult,
  AuditEvent,
  Criterion,
  Delivery,
  Expert,
  Grade,
  Payout,
  PayoutPeriod,
  PayoutStatus,
  RateCard,
  ReviewDecision,
  Rubric,
  Settings,
  Task,
  TaskStatus,
  Tier,
  TIER_RANK,
} from "./types";

export class ServiceError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly detail: string,
  ) {
    super(detail);
  }
}

export type ClaimResult =
  | { ok: true; task: Task; preferredGolden: boolean }
  | { ok: false; statusCode: 204 | 423; detail: string };

export interface ExpertInput {
  /** Fixed id, used by the conformance replay; generated from the seed otherwise. */
  id?: string;
  name: string;
  tags: string[];
  tier: Tier;
  taskRateCents?: number | null;
}

export interface TaskInput {
  /** Fixed id, used by the conformance replay; generated from the seed otherwise. */
  id?: string;
  externalRef: string;
  prompt: string;
  responses: { model: string; text: string }[];
  requiredTags: string[];
  taskType: string;
  minTier: Tier;
  priority: number;
  deadline: number | null;
  requiredGrades: number;
  isAttentionCheck: boolean;
  expectedScores: Record<string, number> | null;
}

export interface Ledger {
  rows: { expertId: string; expertName: string; status: PayoutStatus; payoutCount: number; totalCents: number }[];
  totalsByStatus: Record<PayoutStatus, number>;
  countsByStatus: Record<PayoutStatus, number>;
  grandTotalCents: number;
}

export interface PeriodTotals {
  id: string;
  label: string;
  closedAt: number;
  payoutCount: number;
  totalCents: number;
  expertCount: number;
}

export interface Agreement {
  multiGradedTasks: number;
  comparedPairs: number;
  meanAbsDiff: number | null;
  exactAgreement: number | null;
  withinOne: number | null;
}

export interface CriterionMean {
  key: string;
  mean: number;
  stddev: number | null;
  n: number;
}

export interface DeliveryRow {
  task_id: string;
  external_ref: string;
  task_type: string;
  required_tags: string[];
  prompt: string;
  responses: { model: string; text: string }[];
  rubric: { id: string; name: string; version: number };
  expert_id: string;
  expert_tier: Tier;
  scores: Record<string, number>;
  weighted_score: number;
  rationale: string;
  time_spent_seconds: number;
  /** Consensus rounds (4.0.0) are not modelled, so every row carries null here. */
  consensus: null;
}

function eligibleTiers(tier: Tier): Tier[] {
  const rank = TIER_RANK[tier];
  return (Object.keys(TIER_RANK) as Tier[]).filter((t) => TIER_RANK[t] <= rank);
}

/** Delivery fields the service stores as floats: Python prints 4.0 where JSON.stringify prints 4. */
const FLOAT_FIELDS = new Set(["weighted_score", "spread"]);

/** float.__repr__: shortest round-trip digits, always with a fractional part. */
function pythonFloat(n: number): string {
  return Number.isInteger(n) ? `${n}.0` : String(n);
}

/** json.dumps(sort_keys=True, separators=(",", ":")), byte for byte with the service's export. */
export function stableStringify(value: unknown, asFloat = false): string {
  if (typeof value === "number") return asFloat ? pythonFloat(value) : JSON.stringify(value);
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((v) => stableStringify(v, asFloat)).join(",")}]`;
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k], asFloat || FLOAT_FIELDS.has(k) || k === "scores")}`).join(",")}}`;
}

export class Platform {
  readonly clock: Clock;
  readonly settings: Settings;
  readonly experts: Expert[] = [];
  readonly tasks: Task[] = [];
  readonly grades: Grade[] = [];
  readonly payouts: Payout[] = [];
  readonly periods: PayoutPeriod[] = [];
  readonly attentionResults: AttentionResult[] = [];
  readonly deliveries: Delivery[] = [];
  readonly audit: AuditEvent[] = [];
  readonly rateCards = new Map<string, RateCard>();
  rubric: Rubric | null = null;

  /** Rows currently held by an open claim transaction (FOR UPDATE). */
  private readonly locked = new Set<string>();
  private readonly ids: Rng;
  private taskSeq = 0;
  private gradeSeq = 0;
  private payoutSeq = 0;
  private attentionSeq = 0;
  private auditSeq = 0;
  readonly metrics = {
    claimsAssigned: 0,
    claimsEmpty: 0,
    doubleAssignBlocked: 0,
    gradesTotal: 0,
    expertsPaused: 0,
    attentionPass: 0,
    attentionFail: 0,
  };

  constructor(settings: Settings, clock: Clock, idSeed: number) {
    this.settings = settings;
    this.clock = clock;
    this.ids = new Rng(idSeed);
  }

  // ----- seeding -----------------------------------------------------------

  createRubric(name: string, version: number, criteria: Omit<Criterion, "position" | "scaleMin" | "scaleMax">[], id?: string): Rubric {
    this.rubric = {
      id: id ?? this.ids.uuid(),
      name,
      version,
      criteria: criteria.map((c, i) => ({ ...c, scaleMin: 1, scaleMax: 5, position: i })),
    };
    return this.rubric;
  }

  putRateCards(cards: RateCard[]): void {
    for (const card of cards) this.rateCards.set(`${card.tier}:${card.taskType}`, card);
  }

  createExpert(input: ExpertInput): Expert {
    const expert: Expert = {
      id: input.id ?? this.ids.uuid(),
      name: input.name,
      tags: [...input.tags],
      tier: input.tier,
      taskRateCents: input.taskRateCents ?? null,
      status: "active",
      servedCount: 0,
    };
    this.experts.push(expert);
    return expert;
  }

  createTasks(inputs: TaskInput[]): Task[] {
    if (!this.rubric) throw new ServiceError(422, "no rubric");
    const rubricId = this.rubric.id;
    return inputs.map((input) => {
      const task: Task = {
        id: input.id ?? this.ids.uuid(),
        seq: ++this.taskSeq,
        externalRef: input.externalRef,
        prompt: input.prompt,
        responses: input.responses.map((r) => ({ ...r })),
        requiredTags: [...input.requiredTags],
        taskType: input.taskType,
        minTier: input.minTier,
        rubricId,
        priority: input.priority,
        deadline: input.deadline,
        status: "queued",
        requiredGrades: input.requiredGrades,
        gradesReceived: 0,
        isAttentionCheck: input.isAttentionCheck,
        expectedScores: input.expectedScores ? { ...input.expectedScores } : null,
        assignedExpertId: null,
        assignedAt: null,
        leaseExpiresAt: null,
        reclaimCount: 0,
      };
      this.tasks.push(task);
      return task;
    });
  }

  // ----- lookups -----------------------------------------------------------

  expert(id: string): Expert {
    const e = this.experts.find((x) => x.id === id);
    if (!e) throw new ServiceError(404, "expert not found");
    return e;
  }

  task(id: string): Task {
    const t = this.tasks.find((x) => x.id === id);
    if (!t) throw new ServiceError(404, "task not found");
    return t;
  }

  grade(id: string): Grade {
    const g = this.grades.find((x) => x.id === id);
    if (!g) throw new ServiceError(404, "grade not found");
    return g;
  }

  private record(actor: string, action: string, entity: string, entityId: string, detail: Record<string, unknown> | null = null): void {
    this.audit.push({ seq: ++this.auditSeq, at: this.clock.now(), actor, action, entity, entityId, detail });
  }

  // ----- routing (services/routing.py) -----------------------------------

  /** UPDATE tasks SET status='queued' ... WHERE status='assigned' AND lease_expires_at < now() */
  reclaimExpired(): number {
    const now = this.clock.now();
    let n = 0;
    for (const t of this.tasks) {
      if (t.status === "assigned" && t.leaseExpiresAt !== null && t.leaseExpiresAt < now && !this.locked.has(t.id)) {
        t.status = "queued";
        t.assignedExpertId = null;
        t.assignedAt = null;
        t.leaseExpiresAt = null;
        t.reclaimCount += 1;
        n++;
      }
    }
    return n;
  }

  private alreadyGraded(expert: Expert, task: Task): boolean {
    return this.grades.some((g) => g.taskId === task.id && g.expertId === expert.id);
  }

  /** Task.status == queued AND required_tags && expert.tags AND min_tier <= tier AND no grade by this expert */
  isEligible(expert: Expert, task: Task): boolean {
    if (task.status !== "queued") return false;
    if (!task.requiredTags.some((tag) => expert.tags.includes(tag))) return false;
    if (!eligibleTiers(expert.tier).includes(task.minTier)) return false;
    return !this.alreadyGraded(expert, task);
  }

  /** ORDER BY priority DESC, deadline ASC NULLS LAST, seq ASC */
  static order(a: Task, b: Task): number {
    if (a.priority !== b.priority) return b.priority - a.priority;
    if (a.deadline !== b.deadline) {
      if (a.deadline === null) return 1;
      if (b.deadline === null) return -1;
      return a.deadline - b.deadline;
    }
    return a.seq - b.seq;
  }

  /** The ordered candidate list an expert would see, with locked rows skipped. */
  candidates(expert: Expert, wantAttention: boolean | null = null): Task[] {
    return this.tasks
      .filter((t) => this.isEligible(expert, t) && !this.locked.has(t.id))
      .filter((t) => wantAttention === null || t.isAttentionCheck === wantAttention)
      .sort(Platform.order);
  }

  private pick(expert: Expert, wantAttention: boolean | null): Task | undefined {
    return this.candidates(expert, wantAttention)[0];
  }

  private assign(task: Task, expert: Expert): Task {
    const now = this.clock.now();
    task.status = "assigned";
    task.assignedExpertId = expert.id;
    task.assignedAt = now;
    task.leaseExpiresAt = now + this.settings.leaseSeconds * 1000;
    expert.servedCount += 1;
    this.record(`expert:${expert.id}`, "task.assigned", "task", task.id, { lease_expires_at: task.leaseExpiresAt });
    return task;
  }

  attentionPeriod(): number {
    const f = this.settings.attentionFraction;
    return f ? Math.max(1, Math.round(1 / f)) : 0;
  }

  prefersAttention(expert: Expert): boolean {
    const period = this.attentionPeriod();
    return period > 0 && (expert.servedCount + 1) % period === 0;
  }

  /** POST /tasks/next */
  claimNext(expert: Expert): ClaimResult {
    if (expert.status !== "active") return { ok: false, statusCode: 423, detail: `expert is ${expert.status}` };
    this.reclaimExpired();
    const preferredGolden = this.prefersAttention(expert);
    let task = preferredGolden ? this.pick(expert, true) : undefined;
    if (task === undefined) task = this.pick(expert, false); // golden tasks are never filler
    if (task === undefined) {
      this.metrics.claimsEmpty += 1;
      return { ok: false, statusCode: 204, detail: "queue empty for this expert" };
    }
    this.metrics.claimsAssigned += 1;
    return { ok: true, task: this.assign(task, expert), preferredGolden };
  }

  /**
   * POST /tasks/{id}/claim. `holdLock` keeps the row locked after the call so
   * a later claimant sees SKIP LOCKED behaviour; call releaseLock() to commit.
   */
  claimById(expert: Expert, taskId: string, holdLock = false): Task {
    if (expert.status !== "active") throw new ServiceError(423, `expert is ${expert.status}`);
    this.reclaimExpired();
    const task = this.tasks.find((t) => t.id === taskId);
    if (!task) throw new ServiceError(404, "task not found");
    if (this.locked.has(task.id)) {
      this.metrics.doubleAssignBlocked += 1;
      throw new ServiceError(409, "task is being claimed by another expert");
    }
    if (task.status !== "queued") {
      this.metrics.doubleAssignBlocked += 1;
      throw new ServiceError(409, `task is ${task.status}`);
    }
    if (!task.requiredTags.some((tag) => expert.tags.includes(tag))) {
      throw new ServiceError(403, "task requires expertise the expert does not have");
    }
    if (TIER_RANK[task.minTier] > TIER_RANK[expert.tier]) throw new ServiceError(403, "task requires a higher tier");
    this.metrics.claimsAssigned += 1;
    if (holdLock) this.locked.add(task.id);
    return this.assign(task, expert);
  }

  releaseLock(taskId: string): void {
    this.locked.delete(taskId);
  }

  isLocked(taskId: string): boolean {
    return this.locked.has(taskId);
  }

  /** POST /tasks/{id}/release */
  release(expert: Expert, taskId: string): Task {
    const task = this.task(taskId);
    if (task.status !== "assigned" || task.assignedExpertId !== expert.id) {
      throw new ServiceError(409, "task is not assigned to this expert");
    }
    task.status = "queued";
    task.assignedExpertId = null;
    task.assignedAt = null;
    task.leaseExpiresAt = null;
    this.record(`expert:${expert.id}`, "task.released", "task", task.id);
    return task;
  }

  queueDepthByTag(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const t of this.tasks) {
      if (t.status !== "queued") continue;
      for (const tag of t.requiredTags) out[tag] = (out[tag] ?? 0) + 1;
    }
    return out;
  }

  queueSummary(): Record<TaskStatus, number> {
    const out: Record<TaskStatus, number> = { queued: 0, assigned: 0, submitted: 0, approved: 0, rejected: 0 };
    for (const t of this.tasks) out[t.status] += 1;
    return out;
  }

  // ----- grading (services/grading.py) -----------------------------------

  validateScores(scores: Record<string, number>): void {
    if (!this.rubric) throw new ServiceError(422, "no rubric");
    const byKey = new Map(this.rubric.criteria.map((c) => [c.key, c]));
    const missing = [...byKey.keys()].filter((k) => !(k in scores)).sort();
    const unknown = Object.keys(scores).filter((k) => !byKey.has(k)).sort();
    if (missing.length || unknown.length) {
      throw new ServiceError(422, `scores mismatch rubric: missing=[${missing.join(", ")}] unknown=[${unknown.join(", ")}]`);
    }
    for (const [key, value] of Object.entries(scores)) {
      const c = byKey.get(key) as Criterion;
      if (!(c.scaleMin <= value && value <= c.scaleMax)) {
        throw new ServiceError(422, `score for ${key} must be within [${c.scaleMin}, ${c.scaleMax}]`);
      }
    }
  }

  weighted(scores: Record<string, number>): number {
    if (!this.rubric) throw new ServiceError(422, "no rubric");
    const total = this.rubric.criteria.reduce((s, c) => s + c.weight, 0) || 1;
    return this.rubric.criteria.reduce((s, c) => s + (scores[c.key] ?? 0) * c.weight, 0) / total;
  }

  /** POST /grades */
  submitGrade(expert: Expert, taskId: string, scores: Record<string, number>, rationale: string, timeSpentSeconds: number): Grade {
    const task = this.task(taskId);
    if (task.status !== "assigned" || task.assignedExpertId !== expert.id) {
      throw new ServiceError(409, "task is not assigned to this expert");
    }
    this.validateScores(scores);
    const grade: Grade = {
      id: this.ids.uuid(),
      seq: ++this.gradeSeq,
      taskId: task.id,
      expertId: expert.id,
      rubricId: task.rubricId,
      scores: Object.fromEntries(Object.entries(scores).map(([k, v]) => [k, Number(v)])),
      rationale,
      timeSpentSeconds,
      weightedScore: this.weighted(scores),
      submittedAt: this.clock.now(),
      review: null,
    };
    this.grades.push(grade);
    this.metrics.gradesTotal += 1;

    task.gradesReceived += 1;
    task.assignedExpertId = null;
    task.assignedAt = null;
    task.leaseExpiresAt = null;
    task.status = task.isAttentionCheck || task.gradesReceived < task.requiredGrades ? "queued" : "submitted";
    this.record(`expert:${expert.id}`, "grade.submitted", "grade", grade.id);

    const result = this.evaluateAttention(task, grade);
    if (result !== null && !result.passed) this.enforceAttention(expert);
    return grade;
  }

  // ----- attention (services/attention.py) --------------------------------

  evaluateAttention(task: Task, grade: Grade): AttentionResult | null {
    if (!task.isAttentionCheck || !task.expectedScores) return null;
    const deviations = Object.entries(task.expectedScores).map(([k, exp]) => Math.abs((grade.scores[k] ?? 0) - exp));
    const maxDeviation = deviations.length ? Math.max(...deviations) : 0;
    const passed = maxDeviation <= this.settings.attentionTolerance;
    const result: AttentionResult = {
      seq: ++this.attentionSeq,
      taskId: task.id,
      expertId: grade.expertId,
      gradeId: grade.id,
      passed,
      maxDeviation,
      at: this.clock.now(),
    };
    this.attentionResults.push(result);
    if (passed) this.metrics.attentionPass += 1;
    else this.metrics.attentionFail += 1;
    return result;
  }

  /** Pass rate over the most recent `attentionWindow` checks: [rate, passed, total]. */
  rollingPassRate(expertId: string): [number | null, number, number] {
    const recent = this.attentionResults
      .filter((r) => r.expertId === expertId)
      .sort((a, b) => b.seq - a.seq)
      .slice(0, this.settings.attentionWindow);
    if (recent.length === 0) return [null, 0, 0];
    const passed = recent.filter((r) => r.passed).length;
    return [passed / recent.length, passed, recent.length];
  }

  /** Pause the expert and withhold pending payouts when the rolling rate is too low. */
  enforceAttention(expert: Expert): boolean {
    const [rate, , total] = this.rollingPassRate(expert.id);
    if (rate === null || total < this.settings.attentionMinChecks || rate >= this.settings.attentionThreshold) return false;
    if (expert.status === "paused") return false;
    expert.status = "paused";
    for (const p of this.payouts) if (p.expertId === expert.id && p.status === "pending") p.status = "withheld";
    this.metrics.expertsPaused += 1;
    this.record("system", "expert.paused", "expert", expert.id, { rolling_pass_rate: rate, checks: total });
    return true;
  }

  /** PATCH /experts/{id}/status active: releases withheld payouts. */
  reinstate(expert: Expert, actor = "admin"): number {
    expert.status = "active";
    let n = 0;
    for (const p of this.payouts) {
      if (p.expertId === expert.id && p.status === "withheld") {
        p.status = "pending";
        n++;
      }
    }
    this.record(actor, "payout.released", "expert", expert.id, { count: n });
    return n;
  }

  lifetimeAttention(expertId: string): { total: number; passed: number } {
    const mine = this.attentionResults.filter((r) => r.expertId === expertId);
    return { total: mine.length, passed: mine.filter((r) => r.passed).length };
  }

  // ----- reviews and payouts (services/grading.py, services/payouts.py) ---

  rateFor(expert: Expert, taskType: string): number {
    if (expert.taskRateCents !== null) return expert.taskRateCents;
    const card = this.rateCards.get(`${expert.tier}:${taskType}`) ?? this.rateCards.get(`${expert.tier}:default`);
    if (!card) throw new ServiceError(422, `no rate configured for tier=${expert.tier} type=${taskType}`);
    return card.rateCents;
  }

  private createPayout(grade: Grade, task: Task, actor: string): Payout {
    const existing = this.payouts.find((p) => p.gradeId === grade.id);
    if (existing) return existing;
    const expert = this.expert(grade.expertId);
    const amount = this.rateFor(expert, task.taskType);
    const payout: Payout = {
      id: this.ids.uuid(),
      seq: ++this.payoutSeq,
      expertId: expert.id,
      taskId: task.id,
      gradeId: grade.id,
      tier: expert.tier,
      taskType: task.taskType,
      amountCents: amount,
      status: expert.status === "paused" ? "withheld" : "pending",
      periodId: null,
      paidAt: null,
    };
    this.payouts.push(payout);
    this.record(actor, "payout.created", "payout", payout.id, { amount_cents: amount });
    return payout;
  }

  /** POST /reviews */
  review(gradeId: string, decision: ReviewDecision, reason: string | null = null, actor = "reviewer"): { grade: Grade; payout: Payout | null } {
    const grade = this.grade(gradeId);
    if (grade.review !== null) throw new ServiceError(409, "grade already reviewed");
    grade.review = { gradeId, decision, reason, reviewedAt: this.clock.now() };
    const task = this.task(grade.taskId);
    let payout: Payout | null = null;
    if (decision === "approve") payout = this.createPayout(grade, task, actor);
    if (!task.isAttentionCheck) {
      if (decision === "approve") task.status = "approved";
      else if (task.status !== "approved") task.status = "rejected";
    }
    this.record(actor, `grade.${decision}`, "grade", grade.id, { reason });
    return { grade, payout };
  }

  unreviewedGrades(): Grade[] {
    return this.grades.filter((g) => g.review === null);
  }

  /** POST /payouts/periods/close */
  closePeriod(label: string, actor = "admin"): PeriodTotals {
    if (this.periods.some((p) => p.label === label)) throw new ServiceError(409, `period ${label} already closed`);
    const period: PayoutPeriod = { id: this.ids.uuid(), label, closedAt: this.clock.now() };
    this.periods.push(period);
    for (const p of this.payouts) {
      if (p.status === "pending" && p.periodId === null) {
        p.status = "paid";
        p.periodId = period.id;
        p.paidAt = period.closedAt;
      }
    }
    this.record(actor, "period.closed", "payout_period", period.id, { label });
    return this.periodTotals(period);
  }

  periodTotals(period: PayoutPeriod): PeriodTotals {
    const rows = this.payouts.filter((p) => p.periodId === period.id);
    return {
      id: period.id,
      label: period.label,
      closedAt: period.closedAt,
      payoutCount: rows.length,
      totalCents: rows.reduce((s, p) => s + p.amountCents, 0),
      expertCount: new Set(rows.map((p) => p.expertId)).size,
    };
  }

  ledger(): Ledger {
    const byKey = new Map<string, Ledger["rows"][number]>();
    for (const p of this.payouts) {
      const key = `${p.expertId}:${p.status}`;
      const row = byKey.get(key) ?? {
        expertId: p.expertId,
        expertName: this.expert(p.expertId).name,
        status: p.status,
        payoutCount: 0,
        totalCents: 0,
      };
      row.payoutCount += 1;
      row.totalCents += p.amountCents;
      byKey.set(key, row);
    }
    const rows = [...byKey.values()].sort((a, b) => a.expertName.localeCompare(b.expertName) || a.status.localeCompare(b.status));
    const totalsByStatus: Record<PayoutStatus, number> = { pending: 0, withheld: 0, paid: 0 };
    const countsByStatus: Record<PayoutStatus, number> = { pending: 0, withheld: 0, paid: 0 };
    for (const r of rows) {
      totalsByStatus[r.status] += r.totalCents;
      countsByStatus[r.status] += r.payoutCount;
    }
    return { rows, totalsByStatus, countsByStatus, grandTotalCents: totalsByStatus.pending + totalsByStatus.withheld + totalsByStatus.paid };
  }

  periodCsv(period: PayoutPeriod): string {
    const header = "period,payout_id,expert_id,expert_name,task_id,tier,task_type,amount_cents,paid_at";
    const lines = this.payouts
      .filter((p) => p.periodId === period.id)
      .map((p) => ({ p, name: this.expert(p.expertId).name }))
      .sort((a, b) => a.name.localeCompare(b.name) || a.p.seq - b.p.seq)
      .map(({ p, name }) =>
        [period.label, p.id, p.expertId, name, p.taskId, p.tier, p.taskType, p.amountCents, p.paidAt === null ? "" : new Date(p.paidAt).toISOString()].join(","),
      );
    return [header, ...lines].join("\r\n") + "\r\n";
  }

  // ----- analytics (services/analytics.py) --------------------------------

  criterionMeans(): CriterionMean[] {
    if (!this.rubric) return [];
    return this.rubric.criteria.map((c) => {
      const values = this.grades.map((g) => g.scores[c.key]).filter((v): v is number => v !== undefined);
      const n = values.length;
      const mean = n ? values.reduce((s, v) => s + v, 0) / n : 0;
      const variance = n > 1 ? values.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1) : null;
      return { key: c.key, mean, stddev: variance === null ? null : Math.sqrt(variance), n };
    });
  }

  /** Mean pairwise absolute score difference across every multi-graded, non-golden task. */
  globalAgreement(): Agreement {
    const byTask = new Map<string, Grade[]>();
    for (const g of this.grades) {
      const task = this.task(g.taskId);
      if (task.isAttentionCheck) continue;
      byTask.set(g.taskId, [...(byTask.get(g.taskId) ?? []), g]);
    }
    const multi = [...byTask.values()].filter((gs) => gs.length > 1);
    const diffs: number[] = [];
    let exact = 0;
    for (const gs of multi) {
      for (const key of this.rubric?.criteria.map((c) => c.key) ?? []) {
        for (let i = 0; i < gs.length; i++) {
          for (let j = i + 1; j < gs.length; j++) {
            const x = gs[i]?.scores[key] ?? 0;
            const y = gs[j]?.scores[key] ?? 0;
            diffs.push(Math.abs(x - y));
            if (x === y) exact++;
          }
        }
      }
    }
    const n = diffs.length;
    return {
      multiGradedTasks: multi.length,
      comparedPairs: n,
      meanAbsDiff: n ? diffs.reduce((s, d) => s + d, 0) / n : null,
      exactAgreement: n ? exact / n : null,
      withinOne: n ? diffs.filter((d) => d <= 1).length / n : null,
    };
  }

  // ----- delivery (services/delivery.py) ----------------------------------

  deliveryRows(): DeliveryRow[] {
    if (!this.rubric) return [];
    const rubric = this.rubric;
    const criteria = [...rubric.criteria].sort((a, b) => a.position - b.position);
    return this.grades
      .filter((g) => g.review?.decision === "approve" && !this.task(g.taskId).isAttentionCheck)
      .map((g) => ({ g, task: this.task(g.taskId) }))
      .sort((a, b) => a.task.seq - b.task.seq || (a.g.expertId < b.g.expertId ? -1 : a.g.expertId > b.g.expertId ? 1 : 0))
      .map(({ g, task }) => ({
        task_id: task.id,
        external_ref: task.externalRef,
        task_type: task.taskType,
        required_tags: [...task.requiredTags].sort(),
        prompt: task.prompt,
        responses: task.responses,
        rubric: { id: rubric.id, name: rubric.name, version: rubric.version },
        expert_id: g.expertId,
        expert_tier: this.expert(g.expertId).tier,
        scores: Object.fromEntries(criteria.map((c) => [c.key, g.scores[c.key] ?? 0])),
        weighted_score: g.weightedScore,
        rationale: g.rationale,
        time_spent_seconds: g.timeSpentSeconds,
        consensus: null,
      }));
  }

  static jsonl(rows: readonly DeliveryRow[]): string {
    const lines = rows.map((r) => stableStringify(r));
    return lines.length ? lines.join("\n") + "\n" : "";
  }

  buildJsonl(): { body: string; count: number } {
    const rows = this.deliveryRows();
    return { body: Platform.jsonl(rows), count: rows.length };
  }

  /** GET /deliveries/export */
  exportDelivery(actor = "admin"): Delivery {
    const { body, count } = this.buildJsonl();
    const checksum = sha256Hex(body);
    const version = this.deliveries.reduce((m, d) => Math.max(m, d.version), 0) + 1;
    const name = `panelist-grades-v${version}-${checksum.slice(0, 12)}.jsonl`;
    const delivery: Delivery = {
      version,
      checksum,
      location: `s3://${this.settings.deliveryBucket}/deliveries/${name}`,
      rowCount: count,
      sizeBytes: utf8Length(body),
    };
    this.deliveries.push(delivery);
    this.record(actor, "delivery.exported", "delivery", name, { version, rows: count });
    return delivery;
  }
}
