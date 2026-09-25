/** Self-check for the simulation port. Run with `npm run selfcheck`. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { Clock, EPOCH_MS } from "./sim/clock";
import { runDemo, seedPlatform, DEFAULT_DEMO } from "./sim/demo";
import { Platform, ServiceError } from "./sim/platform";
import { sha256Hex, utf8Length } from "./sim/sha256";
import { formatSummary } from "./sim/summary";
import { DEMO_SETTINGS, PayoutStatus, TaskStatus, Tier } from "./sim/types";
import { buildWorld, CRITERIA } from "./sim/world";

let passed = 0;
let failed = 0;

function check(name: string, ok: boolean, detail = ""): void {
  if (ok) {
    passed++;
    console.log(`ok   ${name}`);
  } else {
    failed++;
    console.log(`FAIL ${name}${detail ? `: ${detail}` : ""}`);
  }
}

function fixture(seed = 11) {
  const clock = new Clock(EPOCH_MS);
  const world = buildWorld({ seed, experts: 6, tasks: 30, goldenShare: 0.2, now: clock.now() });
  const platform = seedPlatform(world, DEMO_SETTINGS, clock);
  return { clock, world, platform };
}

function truth(scores: Record<string, number>): Record<string, number> {
  return Object.fromEntries(CRITERIA.map((c) => [c, scores[c] ?? 3]));
}

// ----- sha256 known answers ---------------------------------------------------
check("sha256 of empty string", sha256Hex("") === "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
check("sha256 of abc", sha256Hex("abc") === "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
check(
  "sha256 of a multi-block message",
  sha256Hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") ===
    "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
);

// ----- determinism ------------------------------------------------------------
{
  const a = runDemo(DEFAULT_DEMO);
  const b = runDemo(DEFAULT_DEMO);
  const sa = formatSummary(a.summary);
  const sb = formatSummary(b.summary);
  check("same seed gives identical summary", sa === sb);
  check("summary block has the README shape", sa.startsWith("=".repeat(72) + "\nPANELIST DEMO SUMMARY\n") && sa.includes("claims by matched tag (") && sa.includes("delivery v1:"));
  const c = runDemo({ ...DEFAULT_DEMO, seed: 8 });
  check("different seed changes the summary", formatSummary(c.summary) !== sa);
  check("tag mismatch count is 0 in the full run", a.summary.mismatches === 0);
  check("every steal attempt was blocked", a.summary.doubleBlocked === a.summary.doubleAttempts && a.summary.doubleAttempts === 40);
  check("concurrent first claims land on unique rows", a.summary.uniqueFirstClaims === a.summary.concurrentFirstClaims);
  check("the two abandoned leases were reclaimed", a.summary.reclaims === 2, String(a.summary.reclaims));
  check("careless experts are paused", a.summary.paused.join(",") === "expert-04,expert-18", a.summary.paused.join(","));
  check("golden tasks stay queued at the end", a.summary.taskStatus.queued === a.summary.golden && a.summary.taskStatus.assigned === 0);
  check("payouts equal approved grades", a.summary.payoutsCreated === a.summary.approved);
  check("statement plus withheld equals every payout", a.summary.period.payoutCount + a.summary.ledger.countsByStatus.withheld === a.summary.payoutsCreated);
  check("ledger has no pending payouts after close", a.summary.ledger.totalsByStatus.pending === 0);
  const withheldExperts = new Set(a.run.platform.payouts.filter((p) => p.status === "withheld").map((p) => a.run.platform.expert(p.expertId).name));
  check("withheld payouts belong only to paused experts", [...withheldExperts].every((n) => a.summary.paused.includes(n)) && withheldExperts.size > 0);
  check("delivery rows are the non-golden approved grades", a.summary.delivery.rowCount === a.run.platform.grades.filter((g) => g.review?.decision === "approve" && !a.run.platform.task(g.taskId).isAttentionCheck).length);
  check("delivery checksum is the sha256 of the body", a.summary.delivery.checksum === sha256Hex(a.run.platform.buildJsonl().body));
  check("delivery location carries version and sha prefix", a.summary.delivery.location.endsWith(`panelist-grades-v1-${a.summary.delivery.checksum.slice(0, 12)}.jsonl`));
  check("agreement stats are within bounds", (a.summary.agreement.withinOne ?? 0) >= (a.summary.agreement.exactAgreement ?? 0) && (a.summary.agreement.meanAbsDiff ?? 0) >= 0);
}

// ----- claim race ------------------------------------------------------------
{
  const { platform } = fixture();
  const eligibleCount = (t: Platform["tasks"][number]) => platform.experts.filter((e) => platform.isEligible(e, t)).length;
  const target = platform.tasks
    .filter((t) => !t.isAttentionCheck)
    .sort((a, b) => eligibleCount(b) - eligibleCount(a) || Platform.order(a, b))[0];
  if (!target) throw new Error("no task");
  const claimants = platform.experts.filter((e) => platform.isEligible(e, target));
  check("fixture has several eligible claimants", claimants.length >= 2, String(claimants.length));
  let winners = 0;
  let rejected = 0;
  for (const e of claimants) {
    try {
      platform.claimById(e, target.id, true);
      winners++;
    } catch (err) {
      if (err instanceof ServiceError && err.statusCode === 409) rejected++;
    }
  }
  platform.releaseLock(target.id);
  check("concurrent claims on one task yield exactly one owner", winners === 1 && rejected === claimants.length - 1);
  check("blocked double assignments are counted", platform.metrics.doubleAssignBlocked === claimants.length - 1);
  check("task is assigned to a single expert", target.status === "assigned" && target.assignedExpertId !== null);
  const other = claimants.find((e) => e.id !== target.assignedExpertId);
  let late409 = false;
  try {
    if (other) platform.claimById(other, target.id);
  } catch (err) {
    late409 = err instanceof ServiceError && err.statusCode === 409 && err.detail === "task is assigned";
  }
  check("a claim after commit is rejected with the row status", late409);
}

// ----- routing rules -----------------------------------------------------------
{
  const { platform } = fixture();
  let mismatches = 0;
  let claims = 0;
  for (let round = 0; round < 20; round++) {
    for (const e of platform.experts) {
      const r = platform.claimNext(e);
      if (!r.ok) continue;
      claims++;
      if (!r.task.requiredTags.some((t) => e.tags.includes(t))) mismatches++;
      platform.release(e, r.task.id);
    }
  }
  check("tag mismatch count is always 0 across many claims", mismatches === 0 && claims > 0);
  const e0 = platform.experts[0];
  if (!e0) throw new Error("no expert");
  const cands = platform.candidates(e0, false);
  const sorted = [...cands].sort(Platform.order);
  check("candidates are ordered by priority desc, deadline asc, seq asc", cands.every((t, i) => t === sorted[i]) && cands.length > 1);
  const junior = platform.experts.find((e) => e.tier === "junior");
  const seniorTask = platform.tasks.find((t) => t.minTier === "senior" && junior !== undefined && t.requiredTags.some((x) => junior.tags.includes(x)));
  let gated = false;
  try {
    if (junior && seniorTask) platform.claimById(junior, seniorTask.id);
  } catch (err) {
    gated = err instanceof ServiceError && err.statusCode === 403;
  }
  check("tier gate rejects a junior on a senior task", junior === undefined || seniorTask === undefined || gated);
}

// ----- lease and reclaim -------------------------------------------------------
{
  const { platform, clock } = fixture();
  const e = platform.experts[0];
  if (!e) throw new Error("no expert");
  const r = platform.claimNext(e);
  check("claim sets a lease", r.ok && r.task.leaseExpiresAt === clock.now() + 3000);
  if (!r.ok) throw new Error("claim failed");
  clock.advance(2999);
  check("lease is still held before expiry", platform.reclaimExpired() === 0 && r.task.status === "assigned");
  clock.advance(2);
  const n = platform.reclaimExpired();
  check("expired lease is reclaimed", n === 1 && r.task.status === "queued" && r.task.reclaimCount === 1 && r.task.assignedExpertId === null);
  let late = false;
  try {
    platform.submitGrade(e, r.task.id, truth({}), "late", 10);
  } catch (err) {
    late = err instanceof ServiceError && err.statusCode === 409;
  }
  check("grading a reclaimed task is rejected with 409", late);
}

// ----- grading validation ------------------------------------------------------
{
  const { platform } = fixture();
  const e = platform.experts[0];
  if (!e) throw new Error("no expert");
  const r = platform.claimNext(e);
  if (!r.ok) throw new Error("claim failed");
  let missing = false;
  try {
    platform.submitGrade(e, r.task.id, { accuracy: 3 }, "", 10);
  } catch (err) {
    missing = err instanceof ServiceError && err.statusCode === 422 && err.detail.includes("missing=");
  }
  check("rubric validation rejects a missing criterion", missing);
  let range = false;
  try {
    platform.submitGrade(e, r.task.id, { ...truth({}), accuracy: 6 }, "", 10);
  } catch (err) {
    range = err instanceof ServiceError && err.statusCode === 422 && err.detail.includes("within [1, 5]");
  }
  check("rubric validation rejects an out-of-scale score", range);
  const g = platform.submitGrade(e, r.task.id, { accuracy: 5, completeness: 3, clarity: 1, safety: 2 }, "ok", 100);
  check("weighted score uses criterion weights", Math.abs(g.weightedScore - (5 * 2 + 3 * 1.5 + 1 + 2) / 5.5) < 1e-9);
}

// ----- attention checks and pausing -------------------------------------------
{
  const { platform } = fixture();
  const golden = platform.tasks.filter((t) => t.isAttentionCheck);
  const careless = platform.experts.find((e) => golden.some((t) => platform.isEligible(e, t)));
  if (!careless) throw new Error("no eligible expert for golden tasks");
  const goldenFor = golden.filter((t) => platform.isEligible(careless, t));
  const bad = (t: (typeof golden)[number]) => Object.fromEntries(Object.entries(t.expectedScores ?? {}).map(([k, v]) => [k, v >= 3 ? 1 : 5]));
  const first = goldenFor[0];
  if (!first) throw new Error("no golden task");
  platform.claimById(careless, first.id);
  platform.submitGrade(careless, first.id, bad(first), "Looks fine.", 9);
  check("one failed check does not pause below min checks", careless.status === "active" && platform.rollingPassRate(careless.id)[2] === 1);
  check("golden task returns to the queue after a grade", first.status === "queued");
  // Approve that grade so a pending payout exists before the pause.
  const g0 = platform.grades[0];
  if (!g0) throw new Error("no grade");
  platform.review(g0.id, "approve");
  check("approval creates a pending payout at the rate card", platform.payouts.length === 1 && platform.payouts[0]?.status === "pending" && platform.payouts[0]?.amountCents === platform.rateFor(careless, first.taskType));
  const second = goldenFor[1];
  if (!second) throw new Error("need a second golden task");
  platform.claimById(careless, second.id);
  platform.submitGrade(careless, second.id, bad(second), "Looks fine.", 9);
  check("careless expert is paused after the threshold", careless.status === "paused" && platform.metrics.expertsPaused === 1);
  check("pending payouts are withheld on pause", platform.payouts.every((p) => p.expertId !== careless.id || p.status === "withheld"));
  const blocked = platform.claimNext(careless);
  check("paused expert gets 423 on claim", !blocked.ok && blocked.statusCode === 423);
  const g1 = platform.grades[1];
  if (!g1) throw new Error("no second grade");
  const { payout } = platform.review(g1.id, "approve");
  check("new approvals for a paused expert are withheld", payout?.status === "withheld");
  const closed = platform.closePeriod("2026-09-T");
  check("period close skips withheld payouts", closed.payoutCount === 0 && platform.ledger().totalsByStatus.withheld === platform.ledger().grandTotalCents);
  const released = platform.reinstate(careless);
  check("reinstatement releases withheld payouts to pending", released === 2 && careless.status === "active" && platform.ledger().totalsByStatus.withheld === 0);
}

// ----- reviews, payouts and period close --------------------------------------
{
  const { platform } = fixture();
  const e = platform.experts.find((x) => x.tier === "senior") ?? platform.experts[0];
  if (!e) throw new Error("no expert");
  const r1 = platform.claimNext(e);
  if (!r1.ok) throw new Error("claim failed");
  const g1 = platform.submitGrade(e, r1.task.id, truth({}), "fine", 200);
  const r2 = platform.claimNext(e);
  if (!r2.ok) throw new Error("claim failed");
  const g2 = platform.submitGrade(e, r2.task.id, truth({}), "fine", 200);
  platform.review(g1.id, "reject", "spot check failed");
  platform.review(g2.id, "approve");
  check("rejected grades are not paid", platform.payouts.length === 1 && platform.payouts[0]?.gradeId === g2.id);
  check("rejected non-golden task is marked rejected", r1.task.isAttentionCheck || r1.task.status === "rejected");
  let twice = false;
  try {
    platform.review(g2.id, "approve");
  } catch (err) {
    twice = err instanceof ServiceError && err.statusCode === 409;
  }
  check("a grade cannot be reviewed twice", twice);
  const expected = platform.rateFor(e, r2.task.taskType);
  const period = platform.closePeriod("2026-09-A");
  check("statement total is the sum of paid rows", period.totalCents === expected && period.payoutCount === 1 && period.expertCount === 1);
  check("closing the same period twice is rejected", (() => { try { platform.closePeriod("2026-09-A"); return false; } catch (err) { return err instanceof ServiceError && err.statusCode === 409; } })());
  const csv = platform.periodCsv(platform.periods[0] as NonNullable<(typeof platform.periods)[number]>);
  check("statement CSV has a header and one row", csv.split("\r\n").filter(Boolean).length === 2 && csv.startsWith("period,payout_id"));
}

// ----- delivery checksum ------------------------------------------------------
{
  const { platform } = fixture();
  const e = platform.experts[0];
  if (!e) throw new Error("no expert");
  for (let i = 0; i < 3; i++) {
    const r = platform.claimNext(e);
    if (!r.ok) break;
    platform.review(platform.submitGrade(e, r.task.id, truth({}), "fine", 100).id, "approve");
  }
  const first = platform.exportDelivery();
  const again = platform.exportDelivery();
  check("delivery checksum is stable for the same rows", first.checksum === again.checksum && again.version === 2);
  const rows = platform.deliveryRows();
  const altered = rows.map((row, i) => (i === 0 ? { ...row, rationale: row.rationale + " (edited)" } : row));
  check("delivery checksum changes when a row changes", sha256Hex(Platform.jsonl(altered)) !== first.checksum);
  const next = platform.claimNext(e);
  if (next.ok) platform.review(platform.submitGrade(e, next.task.id, truth({}), "fine", 100).id, "approve");
  const third = platform.exportDelivery();
  check("a new approval changes the checksum and row count", third.checksum !== first.checksum && third.rowCount === first.rowCount + 1);
  check("jsonl rows use sorted keys and compact separators", platform.buildJsonl().body.split("\n")[0]?.startsWith('{"consensus":null,"expert_id":') === true && !platform.buildJsonl().body.includes(": "));
}

// ----- conformance with the PostgreSQL service -------------------------------
// tests/test_port_conformance.py runs this scenario through the service and writes
// tests/fixtures/port_conformance.json; the port must reproduce every recorded outcome.
interface ConformanceFixture {
  settings: { lease_seconds: number; attention_fraction: number; attention_window: number; attention_min_checks: number; attention_threshold: number; attention_tolerance: number };
  rubric: { id: string; name: string; version: number; criteria: { key: string; label: string; weight: number }[] };
  rate_cards: { tier: Tier; task_type: string; rate_cents: number }[];
  experts: { id: string; name: string; tags: string[]; tier: Tier }[];
  tasks: {
    id: string; external_ref: string; prompt: string; responses: { model: string; text: string }[]; required_tags: string[];
    task_type: string; min_tier: Tier; priority: number; deadline_hours: number | null; required_grades: number;
    is_attention_check: boolean; expected_scores: Record<string, number> | null;
  }[];
  steps: ConformanceStep[];
  final: {
    ledger: { totals_by_status: Record<string, number>; counts_by_status: Record<string, number> };
    agreement: { multi_graded_tasks: number; compared_pairs: number; mean_abs_diff: number | null; exact_agreement: number | null; within_one: number | null };
    task_status: Record<string, number>;
    criterion_means: { key: string; mean: number; stddev: number | null; n: number }[];
  };
}
type ConformanceStep =
  | { action: "claim"; expert: string; expect: string | number }
  | { action: "grade"; expert: string; task: string; scores: Record<string, number>; rationale: string; time_spent_seconds: number; expect: { weighted_score: number; attention: { passed: boolean; max_deviation: number } | null; expert_status: string; task_status: string } }
  | { action: "review"; task: string; expert: string; decision: "approve" | "reject"; reason: string | null; expect: { task_status: string; payout_cents: number | null; payout_status: string | null } }
  | { action: "close_period"; label: string; expect: { payout_count: number; total_cents: number; expert_count: number } }
  | { action: "export"; expect: { row_count: number; size_bytes: number; sha256: string } };

{
  const fx = JSON.parse(readFileSync(resolve(process.cwd(), "..", "tests", "fixtures", "port_conformance.json"), "utf8")) as ConformanceFixture;
  const clock = new Clock(EPOCH_MS);
  const st = fx.settings;
  const platform = new Platform(
    {
      leaseSeconds: st.lease_seconds,
      attentionFraction: st.attention_fraction,
      attentionWindow: st.attention_window,
      attentionMinChecks: st.attention_min_checks,
      attentionThreshold: st.attention_threshold,
      attentionTolerance: st.attention_tolerance,
      deliveryBucket: "conformance",
    },
    clock,
    1,
  );
  platform.createRubric(fx.rubric.name, fx.rubric.version, fx.rubric.criteria, fx.rubric.id);
  platform.putRateCards(fx.rate_cards.map((c) => ({ tier: c.tier, taskType: c.task_type, rateCents: c.rate_cents })));
  for (const e of fx.experts) platform.createExpert({ id: e.id, name: e.name, tags: e.tags, tier: e.tier });
  platform.createTasks(
    fx.tasks.map((t) => ({
      id: t.id,
      externalRef: t.external_ref,
      prompt: t.prompt,
      responses: t.responses,
      requiredTags: t.required_tags,
      taskType: t.task_type,
      minTier: t.min_tier,
      priority: t.priority,
      deadline: t.deadline_hours === null ? null : clock.now() + t.deadline_hours * 3_600_000,
      requiredGrades: t.required_grades,
      isAttentionCheck: t.is_attention_check,
      expectedScores: t.expected_scores,
    })),
  );
  const expertNamed = (name: string) => {
    const e = platform.experts.find((x) => x.name === name);
    if (!e) throw new Error(`no expert ${name}`);
    return e;
  };
  const taskRef = (ref: string) => {
    const t = platform.tasks.find((x) => x.externalRef === ref);
    if (!t) throw new Error(`no task ${ref}`);
    return t;
  };
  const near = (a: number | null, b: number | null): boolean => (a === null || b === null ? a === b : Math.abs(a - b) < 1e-9);
  fx.steps.forEach((step, i) => {
    clock.advance(1000);
    const label = `conformance ${i + 1} ${step.action}`;
    switch (step.action) {
      case "claim": {
        const r = platform.claimNext(expertNamed(step.expert));
        const got = r.ok ? r.task.externalRef : r.statusCode;
        check(`${label}: ${step.expert} gets ${String(step.expect)}`, got === step.expect, String(got));
        break;
      }
      case "grade": {
        const expert = expertNamed(step.expert);
        const task = taskRef(step.task);
        const g = platform.submitGrade(expert, task.id, step.scores, step.rationale, step.time_spent_seconds);
        const att = platform.attentionResults.find((a) => a.gradeId === g.id) ?? null;
        const attentionOk = att === null ? step.expect.attention === null : step.expect.attention !== null && att.passed === step.expect.attention.passed && near(att.maxDeviation, step.expect.attention.max_deviation);
        const ok = near(g.weightedScore, step.expect.weighted_score) && expert.status === step.expect.expert_status && task.status === step.expect.task_status && attentionOk;
        check(`${label}: ${step.expert} on ${step.task}`, ok, `${g.weightedScore} ${expert.status} ${task.status} ${JSON.stringify(att)}`);
        break;
      }
      case "review": {
        const task = taskRef(step.task);
        const expert = expertNamed(step.expert);
        const grade = platform.grades.find((g) => g.taskId === task.id && g.expertId === expert.id);
        if (!grade) throw new Error(`no grade on ${step.task} by ${step.expert}`);
        const { payout } = platform.review(grade.id, step.decision, step.reason);
        const ok = task.status === step.expect.task_status && (payout?.amountCents ?? null) === step.expect.payout_cents && (payout?.status ?? null) === step.expect.payout_status;
        check(`${label}: ${step.decision} ${step.task} by ${step.expert}`, ok, `${task.status} ${String(payout?.amountCents)} ${String(payout?.status)}`);
        break;
      }
      case "close_period": {
        const totals = platform.closePeriod(step.label);
        const ok = totals.payoutCount === step.expect.payout_count && totals.totalCents === step.expect.total_cents && totals.expertCount === step.expect.expert_count;
        check(`${label}: ${step.label}`, ok, JSON.stringify(totals));
        break;
      }
      case "export": {
        const { body, count } = platform.buildJsonl();
        const sha = sha256Hex(body);
        const ok = count === step.expect.row_count && utf8Length(body) === step.expect.size_bytes && sha === step.expect.sha256;
        check(`${label}: ${step.expect.row_count} rows, sha256 ${step.expect.sha256.slice(0, 12)}`, ok, `${count} rows, ${utf8Length(body)} bytes, ${sha.slice(0, 12)}`);
        break;
      }
    }
  });
  const ledger = platform.ledger();
  const statuses: PayoutStatus[] = ["pending", "withheld", "paid"];
  check(
    "conformance: ledger totals and counts match the service",
    statuses.every((s) => ledger.totalsByStatus[s] === fx.final.ledger.totals_by_status[s] && ledger.countsByStatus[s] === fx.final.ledger.counts_by_status[s]),
    JSON.stringify(ledger.totalsByStatus),
  );
  const agreement = platform.globalAgreement();
  const fa = fx.final.agreement;
  check(
    "conformance: agreement statistics match the service",
    agreement.multiGradedTasks === fa.multi_graded_tasks && agreement.comparedPairs === fa.compared_pairs && near(agreement.meanAbsDiff, fa.mean_abs_diff) && near(agreement.exactAgreement, fa.exact_agreement) && near(agreement.withinOne, fa.within_one),
    JSON.stringify(agreement),
  );
  const status = platform.queueSummary();
  check(
    "conformance: task status counts match the service",
    (Object.keys(status) as TaskStatus[]).every((k) => status[k] === fx.final.task_status[k]) && fx.final.task_status["adjudication"] === 0,
    JSON.stringify(status),
  );
  const means = platform.criterionMeans();
  check(
    "conformance: criterion means match the service",
    means.length === fx.final.criterion_means.length &&
      means.every((m, i) => {
        const f = fx.final.criterion_means[i];
        if (f === undefined || f.key !== m.key || f.n !== m.n || !near(m.mean, f.mean)) return false;
        return m.stddev === null || f.stddev === null ? m.stddev === f.stddev : Math.abs(m.stddev - f.stddev) < 1e-6;
      }),
    JSON.stringify(means),
  );
}

// ----- stylesheet: contrast and size floors --------------------------------
{
  const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
  const token = (name: string): string => {
    const m = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
    if (!m?.[1]) throw new Error(`token --${name} missing`);
    return m[1];
  };
  const luminance = (hex: string): number => {
    const channel = (i: number) => {
      const v = parseInt(hex.slice(i, i + 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
  };
  const contrast = (fg: string, bg: string): number => {
    const [a, b] = [luminance(fg), luminance(bg)];
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };
  // WCAG 2.1 AA for text below 18pt regular: 4.5:1. Every ink token on every surface.
  for (const fg of ["ink", "ink-2", "ink-3"]) {
    for (const bg of ["paper", "paper-2", "panel"]) {
      const r = contrast(token(fg), token(bg));
      check(`--${fg} on --${bg} reaches 4.5:1`, r >= 4.5, r.toFixed(2));
    }
  }
  check("--accent-ink on --accent-soft reaches 4.5:1", contrast(token("accent-ink"), token("accent-soft")) >= 4.5, contrast(token("accent-ink"), token("accent-soft")).toFixed(2));
  check("--accent on --paper reaches 4.5:1", contrast(token("accent"), token("paper")) >= 4.5, contrast(token("accent"), token("paper")).toFixed(2));
  check("--accent on --panel reaches 4.5:1", contrast(token("accent"), token("panel")) >= 4.5, contrast(token("accent"), token("panel")).toFixed(2));
  const sizes = [...css.matchAll(/font-size:\s*([\d.]+)px/g)].map((m) => Number(m[1]));
  check("no fixed font size below 10px", sizes.length > 0 && Math.min(...sizes) >= 10, String(Math.min(...sizes)));
  const blocks = css.split("}").map((b) => b.split("{")).filter((p) => p.length === 2) as [string, string][];
  const sizeOf = (selector: string): number | null => {
    for (const [sel, body] of blocks) {
      if (sel.trim() !== selector) continue;
      const m = body.match(/font-size:\s*([\d.]+)px/);
      if (m?.[1]) return Number(m[1]);
    }
    return null;
  };
  for (const selector of [".counter-label", "th", "caption", ".stamp", ".tag", ".log-kind", ".criterion-scale"]) {
    const size = sizeOf(selector);
    check(`${selector} tracked label is at least 11px`, size !== null && size >= 11, String(size));
  }
}

console.log(`\n${passed} passed, ${failed} failed, ${passed + failed} assertions`);
process.exit(failed === 0 ? 0 : 1);
