/** The PANELIST DEMO SUMMARY block, same shape as sim/demo.py prints. */

import { Agreement, CriterionMean, Ledger, PeriodTotals } from "./platform";
import { Delivery, Settings, TaskStatus } from "./types";

export interface Timing {
  gradingSeconds: number;
  latenciesMs: number[];
}

export interface DemoSummary {
  experts: number;
  tasks: number;
  golden: number;
  seed: number;
  settings: Settings;
  claims: number;
  routedByTag: Record<string, number>;
  mismatches: number;
  doubleBlocked: number;
  doubleAttempts: number;
  concurrentFirstClaims: number;
  uniqueFirstClaims: number;
  reclaims: number;
  adminSweepReclaimed: number;
  checksServed: number;
  checksFailed: number;
  paused: string[];
  gradesStored: number;
  approved: number;
  rejected: number;
  taskStatus: Record<TaskStatus, number>;
  payoutsCreated: number;
  period: PeriodTotals;
  ledger: Ledger;
  agreement: Agreement;
  criteria: CriterionMean[];
  delivery: Delivery;
  storage: string;
}

const RULE = "=".repeat(72);

export function dollars(cents: number): string {
  return "$" + (cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pyDict(obj: Record<string, number>): string {
  return "{" + Object.entries(obj).map(([k, v]) => `'${k}': ${v}`).join(", ") + "}";
}

function pyList(items: string[]): string {
  return "[" + items.map((s) => `'${s}'`).join(", ") + "]";
}

function pct(v: number | null): string {
  return v === null ? "n/a" : `${(v * 100).toFixed(1)}%`;
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))] ?? 0;
}

export function formatSummary(s: DemoSummary, timing: Timing | null = null): string {
  const byTag = Object.keys(s.routedByTag)
    .sort()
    .map((k) => `${k}=${s.routedByTag[k]}`)
    .join(", ");
  const st = s.settings;
  const lines = [
    RULE,
    "PANELIST DEMO SUMMARY",
    RULE,
    `experts: ${s.experts}  tasks: ${s.tasks} (golden: ${s.golden})  seed: ${s.seed}`,
    `config: attention fraction ${st.attentionFraction}, window ${st.attentionWindow}, min checks ${st.attentionMinChecks}, threshold ${st.attentionThreshold}, lease ${st.leaseSeconds}s`,
    `tasks routed by tag (${s.claims} claims): ${byTag}`,
    `tag mismatches: ${s.mismatches}`,
    `double-assignment attempts blocked: ${s.doubleBlocked}/${s.doubleAttempts}  (concurrent first claims: ${s.concurrentFirstClaims}, unique: ${s.uniqueFirstClaims})`,
    `expired leases reclaimed: ${s.reclaims} (admin sweep: ${s.adminSweepReclaimed})`,
    `attention checks served: ${s.checksServed}  failed: ${s.checksFailed}`,
    `experts paused: ${s.paused.length} ${pyList(s.paused)}`,
    `grades stored: ${s.gradesStored}  approved: ${s.approved}  rejected: ${s.rejected}`,
    `task status: ${pyDict(s.taskStatus)}`,
    `payouts created: ${s.payoutsCreated}  statement ${s.period.label}: ${s.period.payoutCount} payouts, ${dollars(s.period.totalCents)} to ${s.period.expertCount} experts`,
    `payout ledger: ${pyDict(s.ledger.totalsByStatus)}  withheld: ${dollars(s.ledger.totalsByStatus.withheld)}`,
    `inter-rater agreement: ${s.agreement.multiGradedTasks} multi-graded tasks, ${s.agreement.comparedPairs} score pairs, mean abs diff ${(s.agreement.meanAbsDiff ?? 0).toFixed(3)}, exact ${pct(s.agreement.exactAgreement)}, within one ${pct(s.agreement.withinOne)}`,
    `criterion means: ${s.criteria.map((c) => `${c.key}=${c.mean.toFixed(2)}`).join(", ")}`,
    `delivery v${s.delivery.version}: ${s.delivery.rowCount} rows, ${s.delivery.sizeBytes.toLocaleString("en-US")} bytes, sha256 ${s.delivery.checksum}`,
    `delivery location: ${s.delivery.location}  (${s.storage})`,
  ];
  if (timing) {
    const lat = [...timing.latenciesMs].sort((a, b) => a - b);
    lines.push(
      `grading wall time: ${timing.gradingSeconds.toFixed(1)}s  claim latency over ${lat.length} claims: p50 ${percentile(lat, 0.5).toFixed(3)}ms  p95 ${percentile(lat, 0.95).toFixed(3)}ms`,
    );
  }
  lines.push(RULE);
  return lines.join("\n");
}
