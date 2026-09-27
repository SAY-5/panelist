import { CARELESS_EXPERTS } from "./world";
import { DemoSummary, dollars } from "./summary";

/**
 * One PostgreSQL run, the one quoted in README.md and recorded in `docs/demo-2026-09-26.json`.
 *
 * It is an observation, not a specification: the service races 40 threads for rows, so another
 * run of the same configuration produces different counts. It exists so the page can show what
 * the service produced next to what this simulation produces, rather than leaving a reader to
 * assume the two agree. Regenerate the artifact with
 * `uv run python -m sim.demo --json docs/demo-<date>.json` and update these fields from it;
 * nothing here is computed in the browser.
 */
export interface ReferenceRun {
  commit: string;
  ranAt: string;
  /** The one, five and fifteen minute load averages the artifact records for the run's start. */
  loadAverageStart: number[];
  claims: number;
  mismatches: number;
  doubleBlocked: number;
  doubleAttempts: number;
  checksServed: number;
  checksFailed: number;
  pausedExperts: string[];
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
  commit: "67dae00",
  ranAt: "2026-09-27T05:39:20Z",
  loadAverageStart: [15.15, 12.99, 10.83],
  claims: 664,
  mismatches: 0,
  doubleBlocked: 40,
  doubleAttempts: 40,
  checksServed: 132,
  checksFailed: 4,
  pausedExperts: ["expert-04", "expert-18"],
  gradesStored: 664,
  approved: 656,
  queuedAtEnd: 50,
  golden: 50,
  multiGradedTasks: 82,
  deliveryRows: 450,
  statementCents: 520050,
  withheldCents: 2850,
  pendingCents: 0,
};

/**
 * held: the rule is enforced in code, so the two runs have to read the same.
 * differs: the two runs are built differently and are expected to disagree.
 * varies: the service does not fix this figure from run to run, so an equal reading and an
 *   unequal one are both ordinary; the service column is the one recorded run, nothing more.
 */
export type RowKind = "held" | "differs" | "varies";

export interface ComparisonRow {
  measure: string;
  service: string;
  here: string;
  kind: RowKind;
  why: string;
}

/** The recorded service run beside a run of this port, line by line. */
export function compareRows(s: DemoSummary): ComparisonRow[] {
  const r = REFERENCE_RUN;
  const row =
    (kind: RowKind) =>
    (measure: string, service: string, here: string, why: string): ComparisonRow => ({
      measure,
      service,
      here,
      kind,
      why,
    });
  const held = row("held");
  const differs = row("differs");
  const varies = row("varies");
  const strangers = (paused: string[]) => paused.filter((name) => !CARELESS_EXPERTS.includes(name));
  return [
    held("tag mismatches", String(r.mismatches), String(s.mismatches), "no expert is served work outside their tags"),
    held(
      "double claims blocked",
      `${r.doubleBlocked}/${r.doubleAttempts}`,
      `${s.doubleBlocked}/${s.doubleAttempts}`,
      "every concurrent attempt on a held row is refused",
    ),
    held("queued at the end", String(r.queuedAtEnd), String(s.taskStatus.queued), "the golden tasks, which stay reusable"),
    held(
      "paused experts outside the careless pair",
      String(strangers(r.pausedExperts).length),
      String(strangers(s.paused).length),
      `a careful grade lands within one of the reference score and the tolerance is one, so only ${CARELESS_EXPERTS.join(" and ")} can fail a check`,
    ),
    held("payouts still pending", dollars(r.pendingCents), dollars(s.ledger.totalsByStatus.pending), "a closed period leaves nothing pending"),
    held(
      "withheld outside the statement",
      r.withheldCents > 0 ? "yes" : "no",
      s.ledger.totalsByStatus.withheld > 0 ? "yes" : "no",
      "a paused expert's money is not paid into the statement",
    ),
    varies(
      "experts paused",
      String(r.pausedExperts.length),
      String(s.paused.length),
      `the guard cannot act until it holds ${s.settings.attentionMinChecks} of an expert's checks,` +
        " and the claim race decides how many each careless expert is served: 2 paused in 14 of 15" +
        " service runs at this configuration and 1 in the fifteenth, where the other careless" +
        " expert had been served a single check. This page runs a fixed schedule and pauses both" +
        " every time",
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
