import { useEffect, useRef, useState } from "react";
import { createDemo, DEFAULT_DEMO, DemoEvent, DemoRun, DemoSummary, formatSummary, NOTABLE } from "../sim";
import { Card, Section, Stamp } from "./bits";
import { money, pct, prefersReducedMotion } from "./format";

const ACCENT_KINDS = new Set(["paused", "review-rejected", "grade-conflict", "reclaimed"]);
const LOG_LIMIT = 80;

interface LogRow {
  seq: number;
  kind: string;
  text: string;
  accent: boolean;
}

/** Passed attention checks are the common case, so only failures reach the log. */
function loggable(e: DemoEvent): boolean {
  if (!NOTABLE.has(e.kind)) return false;
  if (e.kind === "attention") return !e.passed;
  return true;
}

function describe(e: DemoEvent): string {
  switch (e.kind) {
    case "phase":
      return `phase: ${e.name}`;
    case "seeded":
      return `${e.experts} experts, ${e.tasks} tasks, ${e.golden} of them golden`;
    case "contention":
      return `${e.claimed} simultaneous claims landed on ${e.unique} distinct rows; ${e.blocked} of ${e.attempts} steal attempts blocked`;
    case "reclaimed":
      return `${e.count} expired lease${e.count === 1 ? "" : "s"} back in the queue: ${e.refs.slice(0, 3).join(", ")}`;
    case "attention":
      return `${e.expert} failed check ${e.ref}, max deviation ${e.maxDeviation.toFixed(0)}, rolling ${pct(e.rate)} over ${e.checks}`;
    case "paused":
      return `${e.expert} paused at ${pct(e.rate)} over ${e.checks} checks, ${e.withheld} payouts withheld`;
    case "grade-conflict":
      return `${e.expert} lost ${e.ref}: the lease had already been reclaimed`;
    case "sweep":
      return `admin sweep returned ${e.reclaimed} lease${e.reclaimed === 1 ? "" : "s"}`;
    case "review-rejected":
      return `${e.expert} rejected on ${e.ref}, drift ${e.drift.toFixed(2)} from the known answer`;
    case "reviewed":
      return `${e.approved} approved, ${e.rejected} rejected`;
    case "period":
      return `${e.label}: ${e.payouts} payouts, ${money(e.totalCents)} to ${e.experts} experts, ${money(e.withheldCents)} withheld`;
    case "delivery":
      return `v${e.version}: ${e.rows} rows, ${e.bytes.toLocaleString("en-US")} bytes, sha256 ${e.checksum.slice(0, 12)}`;
    case "done":
      return "run complete";
    default:
      return e.kind;
  }
}

export function FullRunSection() {
  const runRef = useRef<DemoRun | null>(null);
  const frame = useRef(0);
  const seq = useRef(0);
  const logRef = useRef<HTMLDivElement | null>(null);
  const [, setTick] = useState(0);
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<LogRow[]>([]);
  const [summary, setSummary] = useState<DemoSummary | null>(null);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);

  function ensureRun(): DemoRun {
    if (runRef.current === null) runRef.current = createDemo(DEFAULT_DEMO);
    return runRef.current;
  }

  function consume(run: DemoRun, count: number): boolean {
    const fresh: LogRow[] = [];
    let finished = false;
    for (let i = 0; i < count; i++) {
      const next = run.steps.next();
      if (next.done) {
        setSummary(next.value);
        finished = true;
        break;
      }
      if (loggable(next.value)) {
        fresh.push({
          seq: ++seq.current,
          kind: next.value.kind,
          text: describe(next.value),
          accent: ACCENT_KINDS.has(next.value.kind) || next.value.kind === "attention",
        });
      }
    }
    if (fresh.length > 0) setLog((rows) => [...rows, ...fresh].slice(-LOG_LIMIT));
    setTick((t) => t + 1);
    return finished;
  }

  function start() {
    const run = ensureRun();
    if (prefersReducedMotion()) {
      // Skip the animated pacing: drain the generator in one go.
      while (!consume(run, 200)) {
        /* keep draining */
      }
      setRunning(false);
      return;
    }
    setRunning(true);
    const step = () => {
      if (consume(run, 4)) {
        setRunning(false);
        return;
      }
      frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
  }

  function pause() {
    cancelAnimationFrame(frame.current);
    setRunning(false);
  }

  function reset() {
    cancelAnimationFrame(frame.current);
    runRef.current = null;
    seq.current = 0;
    setRunning(false);
    setLog([]);
    setSummary(null);
    setTick((t) => t + 1);
  }

  const run = runRef.current;
  const stats = run?.stats ?? null;
  const platform = run?.platform ?? null;
  const nonGolden = platform ? platform.tasks.filter((t) => !t.isAttentionCheck).length : 0;
  const stillQueued = platform ? platform.tasks.filter((t) => !t.isAttentionCheck && t.status === "queued").length : 0;
  const drained = nonGolden === 0 ? 0 : (nonGolden - stillQueued) / nonGolden;
  const started = run !== null;

  return (
    <Section
      id="run"
      num="05"
      title="The full run, end to end"
      lede="The same 500-task run that sim/demo.py drives against the HTTP API, stepped one observable event at a time. Forty experts claim, grade, trip attention checks and abandon leases; a reviewer spot-checks every grade; a period closes and the dataset is exported."
    >
      <div className="cols">
        <Card
          title="Run"
          aside={
            summary ? <Stamp animate>complete</Stamp> : running ? <Stamp tone="accent">running</Stamp> : <Stamp>idle</Stamp>
          }
        >
          <div className="controls tight">
            <button type="button" className="primary" onClick={start} disabled={running || summary !== null}>
              {started && !summary ? "Resume" : "Run 500 tasks"}
            </button>
            <button type="button" onClick={pause} disabled={!running}>
              Pause
            </button>
            <button type="button" onClick={reset} disabled={!started}>
              Reset
            </button>
          </div>

          <div className="progress" aria-hidden="true">
            <i style={{ width: `${Math.round(drained * 100)}%` }} />
          </div>
          <p className="card-note">
            {started ? `${Math.round(drained * 100)}% of the non-golden queue drained` : "seed 7, 40 experts, 500 tasks, 3 second leases"}
          </p>

          <div className="run-stats" role="group" aria-label="Live run counters" aria-live="polite">
            <dl className="kv">
              <dt>claims</dt>
              <dd>{stats?.claims ?? 0}</dd>
              <dt>grades stored</dt>
              <dd>{stats?.grades ?? 0}</dd>
              <dt>checks served</dt>
              <dd>
                {stats?.checksServed ?? 0} ({stats?.checksFailed ?? 0} failed)
              </dd>
              <dt>tag mismatches</dt>
              <dd>{stats?.mismatches ?? 0}</dd>
              <dt>steal attempts blocked</dt>
              <dd>
                {stats?.doubleBlocked ?? 0}/{stats?.doubleAttempts ?? 0}
              </dd>
              <dt>leases reclaimed</dt>
              <dd>{stats?.reclaims ?? 0}</dd>
              <dt>experts paused</dt>
              <dd>{stats?.paused.length ?? 0}</dd>
              <dt>approved / rejected</dt>
              <dd>
                {stats?.approved ?? 0} / {stats?.rejected ?? 0}
              </dd>
            </dl>
          </div>
        </Card>

        <Card title="Notable events" aside={<span className="queue-meta">{log.length ? `${log.length} shown` : "waiting"}</span>}>
          {log.length === 0 ? (
            <p className="empty">Start the run to see phases, contention, reclaimed leases, failed checks, pauses, rejections and the export.</p>
          ) : (
            <div className="log" ref={logRef} aria-live="polite" aria-relevant="additions">
              {log.map((r) => (
                <div key={r.seq} className={`log-row${r.accent ? " is-accent" : ""} row-in`}>
                  <span className="log-seq">{r.seq}</span>
                  <span className="log-kind">{r.kind}</span>
                  <span className="log-text">{r.text}</span>
                </div>
              ))}
            </div>
          )}
          <p className="card-note">
            Passing attention checks are not logged; failures, pauses, reclaims and rejections are.
          </p>
        </Card>
      </div>

      <div className="section-gap">
        <Card title="Summary block">
          {summary === null ? (
            <p className="empty">
              The run prints the same block as <code className="code">sim/demo.py</code> when it finishes.
            </p>
          ) : (
            <pre className="summary-block" tabIndex={0} aria-label="Demo summary block">
              {formatSummary(summary)}
            </pre>
          )}
        </Card>
      </div>
    </Section>
  );
}
