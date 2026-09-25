import { Counter } from "./counter";
import { useMeasuredRun } from "./demo";
import { money } from "./format";
import { PORTED_SERVICE_VERSION } from "../sim";

export function Hero() {
  const run = useMeasuredRun();
  const s = run?.summary ?? null;

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
          delivery) ported to TypeScript and run right here: no server, no network, a seeded
          generator and a virtual clock in place of the wall clock.
        </p>
        <p className="hero-meta">
          <span>seed 7</span>
          <span className="dot">/</span>
          <span>40 experts</span>
          <span className="dot">/</span>
          <span>500 tasks, 50 golden</span>
          <span className="dot">/</span>
          <span>
            {run === null
              ? "measuring the run"
              : `run measured in this browser in ${run.elapsedMs.toFixed(0)} ms`}
          </span>
        </p>

        <div className="counters" role="group" aria-label="Measured results of the full run" aria-live="polite">
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
      </div>
    </header>
  );
}
