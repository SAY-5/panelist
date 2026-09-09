import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  createDemo,
  DEFAULT_DEMO,
  dollars,
  formatSummary,
  NOTABLE,
  TAGS,
  type DemoEvent,
  type DemoSummary,
} from "./sim";

type View = "Tasks" | "Experts" | "Events";
const phases = [
  "seed",
  "contention",
  "grading",
  "review",
  "payouts",
  "delivery",
];
const labels: Record<string, string> = {
  seed: "Seed the queue",
  contention: "Claim work",
  grading: "Grade responses",
  review: "Review quality",
  payouts: "Close payouts",
  delivery: "Export delivery",
};

function describe(event: DemoEvent): string {
  switch (event.kind) {
    case "phase":
      return labels[event.name] ?? event.name;
    case "seeded":
      return `${event.experts} experts · ${event.tasks} tasks · ${event.golden} attention checks`;
    case "contention":
      return `${event.unique} distinct claims; ${event.blocked}/${event.attempts} double assignments blocked`;
    case "claim":
      return `${event.expert} claimed ${event.ref} (${event.tag})`;
    case "empty":
      return `${event.expert} has no eligible work`;
    case "reclaimed":
      return `${event.count} expired leases returned to the queue`;
    case "attention":
      return `${event.expert}: attention check ${event.passed ? "passed" : "failed"}`;
    case "paused":
      return `${event.expert} paused; ${event.withheld} payouts withheld`;
    case "grade-conflict":
      return `${event.expert}: stale claim rejected for ${event.ref}`;
    case "sweep":
      return `Final sweep reclaimed ${event.reclaimed} leases`;
    case "review-rejected":
      return `${event.ref}: review rejected (${event.expert})`;
    case "reviewed":
      return `${event.approved} grades approved · ${event.rejected} rejected`;
    case "period":
      return `${dollars(event.totalCents)} paid to ${event.experts} experts`;
    case "delivery":
      return `${event.rows} rows exported with a SHA-256 checksum`;
    case "done":
      return "Simulation complete. Delivery and payout exports are ready.";
  }
}

function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function App() {
  const [options, setOptions] = useState(DEFAULT_DEMO);
  const [run, setRun] = useState(() => createDemo(DEFAULT_DEMO));
  const [running, setRunning] = useState(false);
  const [speed, setSpeed] = useState(5);
  const [phase, setPhase] = useState("seed");
  const [summary, setSummary] = useState<DemoSummary | null>(null);
  const [events, setEvents] = useState<
    { sequence: number; event: DemoEvent }[]
  >([]);
  const [eventCount, setEventCount] = useState(0);
  const [view, setView] = useState<View>("Tasks");
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [error, setError] = useState("");
  const completed = useRef(false);
  const sequence = useRef(0);

  const advance = useCallback(
    (count: number) => {
      if (completed.current) return;
      const added: { sequence: number; event: DemoEvent }[] = [];
      try {
        for (let i = 0; i < count; i++) {
          const result = run.steps.next();
          if (result.done) {
            completed.current = true;
            setSummary(result.value);
            setRunning(false);
            break;
          }
          sequence.current += 1;
          if (result.value.kind === "phase") setPhase(result.value.name);
          if (NOTABLE.has(result.value.kind))
            added.push({ sequence: sequence.current, event: result.value });
          if (result.value.kind === "done") {
            const terminal = run.steps.next();
            if (!terminal.done)
              throw new Error(
                "The completed simulation did not return its delivery summary.",
              );
            completed.current = true;
            setSummary(terminal.value);
            setRunning(false);
            break;
          }
        }
        setEvents((previous) => [...previous, ...added].slice(-150));
        setEventCount(sequence.current);
      } catch (cause) {
        setError(
          cause instanceof Error
            ? cause.message
            : "The simulation could not continue. Reset to try again.",
        );
        setRunning(false);
      }
    },
    [run],
  );

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => advance(speed), 80);
    return () => clearInterval(timer);
  }, [advance, running, speed]);

  const reset = (nextOptions = options) => {
    setRunning(false);
    completed.current = false;
    sequence.current = 0;
    setOptions(nextOptions);
    setRun(createDemo(nextOptions));
    setSummary(null);
    setPhase("seed");
    setEvents([]);
    setEventCount(0);
    setPage(0);
    setError("");
  };

  const configure = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const seed = Number(form.get("seed"));
    const tasks = Number(form.get("tasks"));
    if (
      !Number.isInteger(seed) ||
      seed < 1 ||
      seed > 999999 ||
      ![100, 500, 1000].includes(tasks)
    ) {
      setError("Choose a seed from 1 to 999999 and an available task count.");
      return;
    }
    reset({ ...DEFAULT_DEMO, seed, tasks });
  };

  const status = summary
    ? "Complete"
    : running
      ? "Running"
      : eventCount
        ? "Paused"
        : "Ready";
  const taskRows = run.platform.tasks.filter(
    (task) => filter === "all" || task.status === filter,
  );
  const pages = Math.max(1, Math.ceil(taskRows.length / 12));
  const safePage = Math.min(page, pages - 1);
  const ledger = run.platform.ledger();
  const delivery = summary?.delivery;
  const period = run.platform.periods[0];
  const phaseIndex = phases.indexOf(phase);

  return (
    <>
      <a className="skip" href="#workbench">
        Skip to simulation
      </a>
      <header className="masthead">
        <a className="wordmark" href="/">
          panelist<span>.</span>
        </a>
        <span className="edition">Human evaluation, made observable</span>
        <a href="https://github.com/SAY-5/panelist">Source code</a>
      </header>
      <main>
        <section className="intro" aria-labelledby="title">
          <h1 id="title">
            The work behind
            <br />a better answer.
          </h1>
          <div>
            <p>
              Follow a queue from expert assignment to reviewed grades, payouts,
              and a verifiable delivery.
            </p>
            <p className="simulation-note">
              Local simulation. Synthetic tasks and payouts. No accounts,
              payments, or network services are used.
            </p>
          </div>
        </section>
        <div className="workspace" id="workbench">
          <aside className="controls" aria-label="Simulation controls">
            <div className="control-heading">
              <h2>Run the process</h2>
              <span
                className={`status ${status.toLowerCase()}`}
                data-testid="run-status"
                role="status"
              >
                {status}
              </span>
            </div>
            <p className="muted">
              A fixed seed reproduces the same work, decisions, and delivery.
            </p>
            <form onSubmit={configure}>
              <label htmlFor="seed">Seed</label>
              <input
                id="seed"
                name="seed"
                type="number"
                min="1"
                max="999999"
                required
                defaultValue={options.seed}
                disabled={running}
              />
              <label htmlFor="tasks">Task count</label>
              <select
                id="tasks"
                name="tasks"
                defaultValue={options.tasks}
                disabled={running}
              >
                <option value="100">100 tasks</option>
                <option value="500">500 tasks</option>
                <option value="1000">1,000 tasks</option>
              </select>
              <button className="subtle" disabled={running} type="submit">
                Apply settings
              </button>
            </form>
            <label htmlFor="speed">Playback speed</label>
            <select
              id="speed"
              value={speed}
              onChange={(event) => setSpeed(Number(event.target.value))}
            >
              <option value="1">1 event per tick</option>
              <option value="5">5 events per tick</option>
              <option value="25">25 events per tick</option>
              <option value="100">100 events per tick</option>
            </select>
            <button
              className="primary"
              disabled={Boolean(summary || error)}
              onClick={() => setRunning(!running)}
            >
              {running ? "Pause simulation" : "Run simulation"}
            </button>
            <div className="button-pair">
              <button
                disabled={running || Boolean(summary || error)}
                onClick={() => advance(1)}
              >
                Step once
              </button>
              <button onClick={() => reset()}>Reset simulation</button>
            </div>
            {error && (
              <p role="alert" className="error">
                {error} Reset the simulation to recover.
              </p>
            )}
            <dl className="configuration">
              <div>
                <dt>Active seed</dt>
                <dd data-testid="active-seed">{options.seed}</dd>
              </div>
              <div>
                <dt>Experts</dt>
                <dd>{run.world.experts.length}</dd>
              </div>
              <div>
                <dt>Events processed</dt>
                <dd data-testid="event-count">{eventCount.toLocaleString()}</dd>
              </div>
            </dl>
            <ol className="phases" aria-label="Simulation phases">
              {phases.map((name, index) => (
                <li
                  key={name}
                  className={
                    index === phaseIndex && !summary
                      ? "current"
                      : index < phaseIndex || summary
                        ? "finished"
                        : ""
                  }
                  aria-current={
                    index === phaseIndex && !summary ? "step" : undefined
                  }
                >
                  <span>{index + 1}</span>
                  {labels[name]}
                </li>
              ))}
            </ol>
          </aside>
          <section className="results" aria-label="Simulation results">
            <div className="result-title">
              <h2>
                {summary ? "A complete, inspectable delivery." : labels[phase]}
              </h2>
              <span>{options.tasks.toLocaleString()} tasks in this run</span>
            </div>
            <dl className="metrics">
              <div>
                <dt>Grades submitted</dt>
                <dd>{run.stats.grades.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Assignments protected</dt>
                <dd>
                  {run.stats.doubleBlocked}
                  <small> / {run.stats.doubleAttempts}</small>
                </dd>
              </div>
              <div>
                <dt>Experts paused</dt>
                <dd>{run.stats.paused.length}</dd>
              </div>
              <div>
                <dt>Payouts approved</dt>
                <dd>
                  {dollars(
                    ledger.totalsByStatus.pending + ledger.totalsByStatus.paid,
                  )}
                </dd>
              </div>
            </dl>
            <div className="routing">
              <h3>Work follows expertise</h3>
              <p>
                Assignments by required tag. {run.stats.mismatches} tag
                mismatches.
              </p>
              <div className="tag-counts">
                {TAGS.map((tag) => (
                  <div key={tag}>
                    <span>{tag}</span>
                    <strong>{run.stats.routedByTag[tag] ?? 0}</strong>
                  </div>
                ))}
              </div>
            </div>
            <nav className="views" aria-label="Inspect simulation">
              {(["Tasks", "Experts", "Events"] as const).map((name) => (
                <button
                  key={name}
                  aria-pressed={view === name}
                  onClick={() => setView(name)}
                >
                  {name}
                </button>
              ))}
            </nav>
            {view === "Tasks" && (
              <section className="data-view" aria-labelledby="queue-title">
                <div className="table-heading">
                  <h3 id="queue-title">Task queue</h3>
                  <label>
                    Status{" "}
                    <select
                      value={filter}
                      onChange={(event) => {
                        setFilter(event.target.value);
                        setPage(0);
                      }}
                    >
                      <option value="all">All statuses</option>
                      {[
                        "queued",
                        "assigned",
                        "submitted",
                        "approved",
                        "rejected",
                      ].map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Task</th>
                        <th scope="col">Expertise</th>
                        <th scope="col">Status</th>
                        <th scope="col">Grades</th>
                      </tr>
                    </thead>
                    <tbody>
                      {taskRows
                        .slice(safePage * 12, safePage * 12 + 12)
                        .map((task) => (
                          <tr key={task.id}>
                            <td>
                              {task.externalRef}
                              {task.isAttentionCheck && (
                                <span className="attention-label">
                                  Attention check
                                </span>
                              )}
                            </td>
                            <td>{task.requiredTags.join(", ")}</td>
                            <td>
                              <span className={`task-status ${task.status}`}>
                                {task.status}
                              </span>
                            </td>
                            <td>
                              {task.gradesReceived} / {task.requiredGrades}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {!taskRows.length && (
                  <p className="empty">No tasks have this status yet.</p>
                )}
                <div className="pagination">
                  <span>
                    {taskRows.length} tasks · Page {safePage + 1} of {pages}
                  </span>
                  <div>
                    <button
                      disabled={safePage === 0}
                      onClick={() => setPage(safePage - 1)}
                    >
                      Previous
                    </button>
                    <button
                      disabled={safePage + 1 >= pages}
                      onClick={() => setPage(safePage + 1)}
                    >
                      Next
                    </button>
                  </div>
                </div>
              </section>
            )}
            {view === "Experts" && (
              <section className="data-view" aria-labelledby="experts-title">
                <div className="table-heading">
                  <h3 id="experts-title">Expert roster</h3>
                  <span>{run.platform.experts.length} simulated experts</span>
                </div>
                <div className="table-scroll roster">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Expert</th>
                        <th scope="col">Expertise</th>
                        <th scope="col">Tier</th>
                        <th scope="col">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {run.platform.experts.map((expert) => (
                        <tr key={expert.id}>
                          <td>{expert.name}</td>
                          <td>{expert.tags.join(", ")}</td>
                          <td>{expert.tier}</td>
                          <td>
                            <span className={`task-status ${expert.status}`}>
                              {expert.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
            {view === "Events" && (
              <section className="data-view" aria-labelledby="events-title">
                <div className="table-heading">
                  <h3 id="events-title">Event journal</h3>
                  <span>Latest 150 notable events</span>
                </div>
                {!events.length ? (
                  <p className="empty">
                    Run or step through the simulation to inspect its events.
                  </p>
                ) : (
                  <ol className="journal">
                    {[...events]
                      .reverse()
                      .map(({ sequence: number, event }) => (
                        <li key={number}>
                          <span className="event-number">
                            {number.toString().padStart(4, "0")}
                          </span>
                          <div>
                            <strong>{event.kind.replaceAll("-", " ")}</strong>
                            <p>{describe(event)}</p>
                          </div>
                        </li>
                      ))}
                  </ol>
                )}
              </section>
            )}
            <section className="delivery" aria-labelledby="delivery-title">
              <div>
                <h3 id="delivery-title">Take the evidence with you.</h3>
                <p>
                  {delivery
                    ? `${delivery.rowCount} reviewed rows. The checksum verifies exactly what you download.`
                    : "Complete the run to download reviewed grades and the payout statement."}
                </p>
              </div>
              <div className="export-actions">
                <button
                  disabled={!summary}
                  onClick={() =>
                    download(
                      `panelist-seed-${options.seed}.jsonl`,
                      run.platform.buildJsonl().body,
                      "application/x-ndjson",
                    )
                  }
                >
                  Download JSONL
                </button>
                <button
                  disabled={!summary || !period}
                  onClick={() => {
                    if (period)
                      download(
                        `panelist-payouts-${options.seed}.csv`,
                        run.platform.periodCsv(period),
                        "text/csv",
                      );
                  }}
                >
                  Download payouts CSV
                </button>
              </div>
              {delivery && (
                <div className="checksum">
                  <span>SHA-256</span>
                  <code data-testid="checksum">{delivery.checksum}</code>
                </div>
              )}
              {summary && (
                <details>
                  <summary>Read the full run summary</summary>
                  <pre>{formatSummary(summary)}</pre>
                </details>
              )}
            </section>
          </section>
        </div>
        <footer>
          <span>
            Panelist · A local demonstration of human evaluation operations
          </span>
          <span>Deterministic by design. Inspectable at every step.</span>
        </footer>
      </main>
    </>
  );
}
