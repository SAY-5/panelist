import { Counter } from "./counter";
import { useSimulatedRun } from "./demo";
import { money } from "./format";
import { RUN } from "./params";
import { PORTED_SERVICE_VERSION } from "../sim";

export function Hero() {
  const run = useSimulatedRun();
  const s = run?.summary ?? null;
  const spoken =
    s === null
      ? ""
      : `Simulated run complete: ${s.claims} claims, ${s.mismatches} tag mismatches, ` +
        `${s.doubleBlocked} of ${s.doubleAttempts} double claims blocked, ${s.paused.length} experts paused, ` +
        `${money(s.period.totalCents)} in the statement, ${s.delivery.rowCount} delivery rows.`;

  return (
    <header className="hero">
      <div className="wrap">
        <p className="kicker">Panelist / browser demo</p>
        <h1>
          A grading queue that never hands the same task to <em>two experts</em>.
        </h1>
        <p className="hero-lede">
          Panelist is a FastAPI and PostgreSQL platform where domain experts pull work from a
          tag-routed queue, score model output against a versioned rubric, are paid per approved
          grade, and whose approved grades ship as checksummed JSONL. Everything on this page is
          the {PORTED_SERVICE_VERSION} service layer (routing, grading, attention, payouts, analytics,
          delivery) ported to TypeScript and run right here: no backend and no API calls, a seeded
          generator and a virtual clock in place of the wall clock. The only request this page makes
          is for the two webfonts it sets in Fraunces and IBM Plex Mono.
        </p>
        <p className="hero-meta">
          <span>seed {RUN.seed}</span>
          <span className="dot">/</span>
          <span>{RUN.experts} experts</span>
          <span className="dot">/</span>
          <span>
            {RUN.tasks} tasks, {RUN.golden} golden
          </span>
          <span className="dot">/</span>
          <span>
            {run === null
              ? "simulating the run"
              : `simulated in this browser, compute time ${run.elapsedMs.toFixed(0)} ms`}
          </span>
        </p>

        <div className="counters" role="group" aria-label={`Results of the simulated ${RUN.tasks}-task run`}>
          <Counter
            label="Claims routed"
            target={s ? s.claims : null}
            note={s ? `${Object.keys(s.routedByTag).length} expertise tags, ${s.gradesStored} grades stored` : " "}
            spoken={s ? String(s.claims) : ""}
          />
          <Counter
            label="Tag mismatches"
            target={s ? s.mismatches : null}
            note="no expert was served work outside their tags"
          />
          <Counter
            label="Double claims blocked"
            target={s ? s.doubleBlocked : null}
            format={(v) => `${Math.round(v)}/${s ? s.doubleAttempts : 0}`}
            spoken={s ? `${s.doubleBlocked} of ${s.doubleAttempts}` : ""}
            note={s ? `${s.concurrentFirstClaims} concurrent first claims landed on ${s.uniqueFirstClaims} distinct rows` : " "}
          />
          <Counter
            label="Experts paused"
            target={s ? s.paused.length : null}
            accent
            note={s ? `${s.paused.join(", ") || "none"} after ${s.checksFailed} failed attention checks` : " "}
          />
          <Counter
            label="Statement"
            target={s ? s.period.totalCents : null}
            format={(v) => money(v)}
            spoken={s ? money(s.period.totalCents) : ""}
            note={s ? `${s.period.payoutCount} payouts to ${s.period.expertCount} experts, ${money(s.ledger.totalsByStatus.withheld)} withheld` : " "}
          />
          <Counter
            label="Delivery rows"
            target={s ? s.delivery.rowCount : null}
            note={
              s ? (
                <span className="hex">
                  sha256 <b>{s.delivery.checksum.slice(0, 12)}</b>
                  {s.delivery.checksum.slice(12, 24)}…, one row per approved grade, the{" "}
                  {PORTED_SERVICE_VERSION} export rule
                </span>
              ) : (
                " "
              )
            }
          />
        </div>
        <p className="hero-note">
          Deterministic simulation with its own PRNG, not a measurement of the service: the totals
          differ from the PostgreSQL run in the README, the invariants hold in both.
        </p>
        <p className="sr" role="status">
          {spoken}
        </p>
      </div>
    </header>
  );
}
