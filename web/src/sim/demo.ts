/**
 * Port of sim/demo.py as a step generator: seed, contention round, concurrent grading,
 * lease reclaim, review, period close, aggregates and export. Every yield is one
 * observable step so a UI can run it at any speed; the return value is the summary.
 */

import { Clock, EPOCH_MS } from "./clock";
import { Platform, ServiceError } from "./platform";
import { hashString, Rng } from "./prng";
import { DemoSummary } from "./summary";
import { DEMO_SETTINGS, Settings } from "./types";
import { buildWorld, gradeFor, RATES, RUBRIC, SimExpert, SimTask, weighted, World } from "./world";

export interface DemoOptions {
  seed: number;
  experts: number;
  tasks: number;
  goldenShare: number;
  settings: Settings;
  /** Virtual ms the clock moves per expert action; leases expire against this. */
  actionMs: number;
}

export const DEFAULT_DEMO: DemoOptions = {
  seed: 7,
  experts: 40,
  tasks: 500,
  goldenShare: 0.1,
  settings: DEMO_SETTINGS,
  actionMs: 20,
};

export type DemoEvent =
  | { kind: "phase"; name: string }
  | { kind: "seeded"; experts: number; tasks: number; golden: number }
  | { kind: "contention"; claimed: number; unique: number; blocked: number; attempts: number }
  | { kind: "claim"; expert: string; ref: string; tag: string; golden: boolean; queueLeft: number }
  | { kind: "empty"; expert: string }
  | { kind: "reclaimed"; count: number; refs: string[] }
  | { kind: "attention"; expert: string; ref: string; passed: boolean; maxDeviation: number; rate: number; checks: number }
  | { kind: "paused"; expert: string; rate: number; checks: number; withheld: number }
  | { kind: "grade-conflict"; expert: string; ref: string }
  | { kind: "sweep"; reclaimed: number }
  | { kind: "review-rejected"; expert: string; ref: string; drift: number }
  | { kind: "reviewed"; approved: number; rejected: number }
  | { kind: "period"; label: string; payouts: number; totalCents: number; experts: number; withheldCents: number }
  | { kind: "delivery"; version: number; rows: number; bytes: number; checksum: string }
  | { kind: "done" };

export const NOTABLE: ReadonlySet<DemoEvent["kind"]> = new Set([
  "phase", "seeded", "contention", "reclaimed", "attention", "paused", "grade-conflict", "sweep",
  "review-rejected", "reviewed", "period", "delivery", "done",
]);

export interface DemoStats {
  claims: number;
  empty: number;
  routedByTag: Record<string, number>;
  mismatches: number;
  doubleAttempts: number;
  doubleBlocked: number;
  concurrentFirstClaims: number;
  uniqueFirstClaims: number;
  reclaims: number;
  adminSweepReclaimed: number;
  checksServed: number;
  checksFailed: number;
  paused: string[];
  grades: number;
  approved: number;
  rejected: number;
  payouts: number;
  totalActions: number;
}

export interface DemoRun {
  world: World;
  platform: Platform;
  stats: DemoStats;
  steps: Generator<DemoEvent, DemoSummary>;
}

function newStats(): DemoStats {
  return {
    claims: 0, empty: 0, routedByTag: {}, mismatches: 0, doubleAttempts: 0, doubleBlocked: 0,
    concurrentFirstClaims: 0, uniqueFirstClaims: 0, reclaims: 0, adminSweepReclaimed: 0,
    checksServed: 0, checksFailed: 0, paused: [], grades: 0, approved: 0, rejected: 0, payouts: 0,
    totalActions: 0,
  };
}

export function seedPlatform(world: World, settings: Settings, clock: Clock): Platform {
  const platform = new Platform(settings, clock, world.seed * 7919 + 17);
  platform.createRubric(RUBRIC.name, RUBRIC.version, RUBRIC.criteria);
  platform.putRateCards(RATES);
  for (const e of world.experts) e.id = platform.createExpert({ name: e.name, tags: e.tags, tier: e.tier }).id;
  const created = platform.createTasks(world.tasks.map((t) => t.payload));
  world.tasks.forEach((t, i) => {
    t.id = created[i]?.id ?? "";
  });
  return platform;
}

export function createDemo(opts: DemoOptions = DEFAULT_DEMO): DemoRun {
  const clock = new Clock(EPOCH_MS);
  const world = buildWorld({ seed: opts.seed, experts: opts.experts, tasks: opts.tasks, goldenShare: opts.goldenShare, now: clock.now() });
  const platform = seedPlatform(world, opts.settings, clock);
  const stats = newStats();
  return { world, platform, stats, steps: runSteps(opts, world, platform, stats) };
}

/** Run to completion without a UI (selfcheck, tests). */
export function runDemo(opts: DemoOptions = DEFAULT_DEMO): { run: DemoRun; events: DemoEvent[]; summary: DemoSummary } {
  const run = createDemo(opts);
  const events: DemoEvent[] = [];
  for (;;) {
    const next = run.steps.next();
    if (next.done) return { run, events, summary: next.value };
    events.push(next.value);
  }
}

function* contentionRound(world: World, platform: Platform, stats: DemoStats): Generator<DemoEvent, void> {
  // Every expert claims at once: each claim is its own transaction, SKIP LOCKED means
  // they land on distinct rows. The order of arrival is seeded.
  const order = world.rng.shuffle([...world.experts]);
  const held: { expert: SimExpert; taskId: string | null }[] = [];
  for (const e of order) {
    const r = platform.claimNext(platform.expert(e.id));
    held.push({ expert: e, taskId: r.ok ? r.task.id : null });
  }
  const taskIds = held.filter((h) => h.taskId !== null).map((h) => h.taskId as string);
  const unique = new Set(taskIds).size;
  stats.concurrentFirstClaims = taskIds.length;
  stats.uniqueFirstClaims = unique;

  // Then every expert tries to steal a task someone else holds: 409 each time.
  const holders = new Map(held.filter((h) => h.taskId !== null).map((h) => [h.taskId as string, h.expert]));
  for (const e of world.experts) {
    const target = [...holders.entries()].find(([, holder]) => holder !== e)?.[0];
    if (target === undefined) continue;
    stats.doubleAttempts += 1;
    try {
      platform.claimById(platform.expert(e.id), target);
    } catch (err) {
      if (err instanceof ServiceError && err.statusCode === 409) stats.doubleBlocked += 1;
    }
  }
  // Release everything except the abandoners' claims, which will expire.
  for (const h of held) {
    if (h.taskId !== null && !h.expert.abandonsFirst) platform.release(platform.expert(h.expert.id), h.taskId);
  }
  yield { kind: "contention", claimed: taskIds.length, unique, blocked: stats.doubleBlocked, attempts: stats.doubleAttempts };
}

interface Worker {
  sim: SimExpert;
  rng: Rng;
  pending: { taskId: string; sim: SimTask } | null;
  done: boolean;
}

function* expertAction(w: Worker, platform: Platform, stats: DemoStats, tasksById: Map<string, SimTask>): Generator<DemoEvent, void> {
  const expert = platform.expert(w.sim.id);
  if (w.pending === null) {
    const before = platform.tasks.filter((t) => t.status === "assigned" && t.leaseExpiresAt !== null && t.leaseExpiresAt < platform.clock.now());
    const r = platform.claimNext(expert);
    if (before.length > 0) {
      stats.reclaims += before.length;
      yield { kind: "reclaimed", count: before.length, refs: before.map((t) => t.externalRef) };
    }
    if (!r.ok) {
      if (r.statusCode === 423) {
        w.sim.paused = true;
        if (!stats.paused.includes(w.sim.name)) stats.paused.push(w.sim.name);
      } else {
        stats.empty += 1;
        yield { kind: "empty", expert: w.sim.name };
      }
      w.done = true;
      return;
    }
    const task = r.task;
    const sim = tasksById.get(task.id);
    if (!sim) throw new Error("unknown task");
    w.sim.served += 1;
    const matched = [...task.requiredTags].filter((t) => expert.tags.includes(t)).sort();
    const tag = matched[0] ?? "";
    stats.claims += 1;
    for (const t of matched) stats.routedByTag[t] = (stats.routedByTag[t] ?? 0) + 1;
    if (matched.length === 0) stats.mismatches += 1;
    w.pending = { taskId: task.id, sim };
    const queueLeft = platform.tasks.filter((t) => t.status === "queued" && !t.isAttentionCheck).length;
    yield { kind: "claim", expert: w.sim.name, ref: task.externalRef, tag, golden: task.isAttentionCheck, queueLeft };
    return;
  }
  const { taskId, sim } = w.pending;
  w.pending = null;
  const body = gradeFor(w.rng, w.sim, sim);
  const task = platform.task(taskId);
  const wasPaused = expert.status === "paused";
  try {
    const grade = platform.submitGrade(expert, taskId, body.scores, body.rationale, body.timeSpentSeconds);
    stats.grades += 1;
    w.sim.graded += 1;
    if (task.isAttentionCheck) {
      const result = platform.attentionResults.find((a) => a.gradeId === grade.id);
      const [rate, , checks] = platform.rollingPassRate(expert.id);
      stats.checksServed += 1;
      if (result && !result.passed) stats.checksFailed += 1;
      yield {
        kind: "attention", expert: w.sim.name, ref: task.externalRef, passed: result?.passed ?? true,
        maxDeviation: result?.maxDeviation ?? 0, rate: rate ?? 1, checks,
      };
      if (!wasPaused && expert.status === "paused") {
        const withheld = platform.payouts.filter((p) => p.expertId === expert.id && p.status === "withheld").length;
        yield { kind: "paused", expert: w.sim.name, rate: rate ?? 0, checks, withheld };
      }
    }
  } catch (err) {
    if (err instanceof ServiceError && err.statusCode === 409) {
      yield { kind: "grade-conflict", expert: w.sim.name, ref: task.externalRef };
      return;
    }
    throw err;
  }
}

function* gradingPass(workers: Worker[], platform: Platform, stats: DemoStats, tasksById: Map<string, SimTask>, actionMs: number): Generator<DemoEvent, void> {
  for (const w of workers) w.done = false;
  let active = workers.filter((w) => !w.done);
  while (active.length > 0) {
    for (const w of active) {
      platform.clock.advance(actionMs);
      stats.totalActions += 1;
      yield* expertAction(w, platform, stats, tasksById);
    }
    active = active.filter((w) => !w.done);
  }
}

function* runSteps(opts: DemoOptions, world: World, platform: Platform, stats: DemoStats): Generator<DemoEvent, DemoSummary> {
  const golden = world.tasks.filter((t) => t.payload.isAttentionCheck).length;
  yield { kind: "phase", name: "seed" };
  yield { kind: "seeded", experts: world.experts.length, tasks: world.tasks.length, golden };

  yield { kind: "phase", name: "contention" };
  yield* contentionRound(world, platform, stats);

  const tasksById = new Map(world.tasks.map((t) => [t.id, t]));
  const workers: Worker[] = world.experts.map((sim) => ({
    sim,
    rng: new Rng((world.seed ^ (hashString(sim.name) & 0xffff)) >>> 0),
    pending: null,
    done: false,
  }));

  yield { kind: "phase", name: "grading" };
  yield* gradingPass(workers, platform, stats, tasksById, opts.actionMs);

  // Abandoned leases expire after LEASE_SECONDS; sweep and let active experts finish them.
  platform.clock.advance(opts.settings.leaseSeconds * 1000 + 500);
  stats.adminSweepReclaimed = platform.reclaimExpired();
  stats.reclaims += stats.adminSweepReclaimed;
  yield { kind: "sweep", reclaimed: stats.adminSweepReclaimed };
  yield* gradingPass(workers.filter((w) => !w.sim.paused), platform, stats, tasksById, opts.actionMs);

  yield { kind: "phase", name: "review" };
  for (const g of platform.unreviewedGrades()) {
    const sim = tasksById.get(g.taskId);
    if (!sim) continue;
    const drift = Math.abs(g.weightedScore - weighted(sim.trueScores));
    if (drift > 1.5) {
      platform.review(g.id, "reject", "spot check failed");
      stats.rejected += 1;
      yield { kind: "review-rejected", expert: platform.expert(g.expertId).name, ref: platform.task(g.taskId).externalRef, drift };
    } else {
      platform.review(g.id, "approve");
      stats.approved += 1;
    }
  }
  stats.payouts = platform.payouts.length;
  yield { kind: "reviewed", approved: stats.approved, rejected: stats.rejected };

  yield { kind: "phase", name: "payouts" };
  const period = platform.closePeriod("2026-09-A");
  const ledger = platform.ledger();
  yield { kind: "period", label: period.label, payouts: period.payoutCount, totalCents: period.totalCents, experts: period.expertCount, withheldCents: ledger.totalsByStatus.withheld };

  yield { kind: "phase", name: "delivery" };
  const agreement = platform.globalAgreement();
  const criteria = platform.criterionMeans();
  const delivery = platform.exportDelivery();
  yield { kind: "delivery", version: delivery.version, rows: delivery.rowCount, bytes: delivery.sizeBytes, checksum: delivery.checksum };

  const reclaims = platform.tasks.reduce((s, t) => s + t.reclaimCount, 0);
  const attention = world.experts.map((e) => ({ e, ...platform.lifetimeAttention(e.id) }));
  const summary: DemoSummary = {
    experts: world.experts.length,
    tasks: world.tasks.length,
    golden,
    seed: opts.seed,
    settings: opts.settings,
    claims: stats.claims,
    routedByTag: stats.routedByTag,
    mismatches: stats.mismatches,
    doubleBlocked: stats.doubleBlocked,
    doubleAttempts: stats.doubleAttempts,
    concurrentFirstClaims: stats.concurrentFirstClaims,
    uniqueFirstClaims: stats.uniqueFirstClaims,
    reclaims,
    adminSweepReclaimed: stats.adminSweepReclaimed,
    checksServed: attention.reduce((s, a) => s + a.total, 0),
    checksFailed: attention.reduce((s, a) => s + a.total - a.passed, 0),
    paused: attention.filter((a) => platform.expert(a.e.id).status === "paused").map((a) => a.e.name),
    gradesStored: platform.metrics.gradesTotal,
    approved: stats.approved,
    rejected: stats.rejected,
    taskStatus: platform.queueSummary(),
    payoutsCreated: platform.payouts.length,
    period,
    ledger,
    agreement,
    criteria,
    delivery,
    storage: "s3 (in-memory)",
  };
  yield { kind: "done" };
  return summary;
}
