import { DemoSummary, dollars } from "./summary";

/**
 * The PostgreSQL run quoted in README.md, copied from `docs/demo-2026-09-26.json`.
 *
 * It exists so the page can show what the service produced next to what this simulation
 * produces, rather than leaving a reader to assume the two agree. Regenerate the artifact
 * with `uv run python -m sim.demo --json docs/demo-<date>.json` and update these fields from
 * it; nothing here is computed in the browser.
 */
export interface ReferenceRun {
  commit: string;
  ranAt: string;
  claims: number;
  mismatches: number;
  doubleBlocked: number;
  doubleAttempts: number;
  checksServed: number;
  checksFailed: number;
  paused: number;
  gradesStored: number;
  approved: number;
  queuedAtEnd: number;
  golden: number;
  multiGradedTasks: number;
  deliveryRows: number;
  statementCents: number;
  withheldCents: number;
  pendingCents: number;
}

export const REFERENCE_RUN: ReferenceRun = {
  commit: "df7ae50",
  ranAt: "2026-09-27T00:30:51Z",
  claims: 681,
  mismatches: 0,
  doubleBlocked: 40,
  doubleAttempts: 40,
  checksServed: 148,
  checksFailed: 4,
  paused: 2,
  gradesStored: 681,
  approved: 667,
  queuedAtEnd: 50,
  golden: 50,
  multiGradedTasks: 83,
  deliveryRows: 450,
  statementCents: 535500,
  withheldCents: 3900,
  pendingCents: 0,
};

export interface ComparisonRow {
  measure: string;
  service: string;
  here: string;
  /** An invariant has to read the same in both runs; anything else is expected to differ. */
  invariant: boolean;
  why: string;
}

/** The recorded service run beside a run of this port, line by line. */
export function compareRows(s: DemoSummary): ComparisonRow[] {
  const r = REFERENCE_RUN;
  const held = (measure: string, service: string, here: string, why: string): ComparisonRow => ({
    measure,
    service,
    here,
    invariant: true,
    why,
  });
  const differs = (measure: string, service: string, here: string, why: string): ComparisonRow => ({
    measure,
    service,
    here,
    invariant: false,
    why,
  });
  return [
    held("tag mismatches", String(r.mismatches), String(s.mismatches), "no expert is served work outside their tags"),
    held(
      "double claims blocked",
      `${r.doubleBlocked}/${r.doubleAttempts}`,
      `${s.doubleBlocked}/${s.doubleAttempts}`,
      "every concurrent attempt on a held row is refused",
    ),
    held("queued at the end", String(r.queuedAtEnd), String(s.taskStatus.queued), "the golden tasks, which stay reusable"),
    held("experts paused", String(r.paused), String(s.paused.length), "the two careless experts trip the guard"),
    held("payouts still pending", dollars(r.pendingCents), dollars(s.ledger.totalsByStatus.pending), "a closed period leaves nothing pending"),
    held(
      "withheld outside the statement",
      r.withheldCents > 0 ? "yes" : "no",
      s.ledger.totalsByStatus.withheld > 0 ? "yes" : "no",
      "a paused expert's money is not paid into the statement",
    ),
    differs("claims", String(r.claims), String(s.claims), "different generator, different race"),
    differs("grades stored", String(r.gradesStored), String(s.gradesStored), "one grade per claim in both"),
    differs(
      "attention checks served",
      String(r.checksServed),
      String(s.checksServed),
      "the keyed schedule falls differently for different expert ids",
    ),
    differs("statement", dollars(r.statementCents), dollars(s.period.totalCents), "follows the grade count"),
    differs(
      "delivery rows",
      String(r.deliveryRows),
      String(s.delivery.rowCount),
      `consensus is not ported: the service delivers one grade per multi-graded task (${r.multiGradedTasks} of them), this page delivers every approved grade`,
    ),
  ];
}
