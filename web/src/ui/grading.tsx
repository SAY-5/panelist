import { useEffect, useState } from "react";
import { CRITERIA, gradeFor, ServiceError, trueScoresFor } from "../sim";
import { Card, Section, Stamp } from "./bits";
import { money, pct } from "./format";
import { useWorkbench } from "./workbench";

const ARC = Math.PI * 100;

function polar(fraction: number, radius: number): [number, number] {
  const theta = Math.PI - fraction * Math.PI;
  return [120 + radius * Math.cos(theta), 120 - radius * Math.sin(theta)];
}

function Gauge({ rate, threshold, paused, checks, windowSize }: { rate: number | null; threshold: number; paused: boolean; checks: number; windowSize: number }) {
  const shown = rate ?? 0;
  const [tx, ty] = polar(threshold, 112);
  const [tx2, ty2] = polar(threshold, 86);
  const label = rate === null ? "n/a" : `${Math.round(rate * 100)}%`;
  return (
    <svg className="gauge" viewBox="0 0 240 150" role="img" aria-label={`Rolling attention pass rate ${label} over the last ${checks} of ${windowSize} checks${paused ? ", expert paused" : ""}`}>
      <path className="gauge-track" d="M 20 120 A 100 100 0 0 1 220 120" />
      <path
        className={`gauge-fill${paused ? " is-paused" : ""}`}
        d="M 20 120 A 100 100 0 0 1 220 120"
        strokeDasharray={`${shown * ARC} ${ARC}`}
      />
      <line className="gauge-threshold" x1={tx2} y1={ty2} x2={tx} y2={ty} />
      <text className={`gauge-value${paused ? " is-paused" : ""}`} x="120" y="112" textAnchor="middle">
        {label}
      </text>
      <text className="gauge-caption" x="120" y="134" textAnchor="middle">
        {checks} of last {windowSize} checks
      </text>
      <text className="gauge-caption" x={tx} y={ty - 6} textAnchor="middle">
        pause below {Math.round(threshold * 100)}%
      </text>
    </svg>
  );
}

interface Verdict {
  ref: string;
  golden: boolean;
  passed: boolean;
  maxDeviation: number;
  expected: Record<string, number> | null;
  scores: Record<string, number>;
  pausedNow: boolean;
}

export function GradingSection() {
  const { bench, bump, expertId } = useWorkbench();
  const platform = bench.platform;
  const settings = platform.settings;
  const rubric = platform.rubric;

  const expert = platform.experts.find((e) => e.id === expertId) ?? platform.experts[0] ?? null;
  const assigned = expert ? platform.tasks.find((t) => t.assignedExpertId === expert.id) ?? null : null;

  const [scores, setScores] = useState<Record<string, number>>(() => Object.fromEntries(CRITERIA.map((c) => [c, 3])));
  const [rationale, setRationale] = useState("Accurate on the core claim; one edge case is missing.");
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);

  const assignedId = assigned?.id ?? null;
  useEffect(() => {
    if (assignedId !== null) setScores(Object.fromEntries(CRITERIA.map((c) => [c, 3])));
  }, [assignedId]);

  const roll: [number | null, number, number] = expert ? platform.rollingPassRate(expert.id) : [null, 0, 0];
  const [rate, passedCount, checkCount] = roll;
  const lifetime = expert ? platform.lifetimeAttention(expert.id) : { total: 0, passed: 0 };
  const withheld = expert ? platform.payouts.filter((p) => p.expertId === expert.id && p.status === "withheld") : [];
  const weightedNow = platform.weighted(scores);

  function claim(golden: boolean) {
    if (!expert) return;
    setError(null);
    setVerdict(null);
    try {
      if (golden) {
        const target = platform.tasks.find((t) => t.isAttentionCheck && platform.isEligible(expert, t));
        if (!target) {
          setError("no golden task is eligible for this expert right now");
          return;
        }
        platform.claimById(expert, target.id);
      } else {
        const r = platform.claimNext(expert);
        if (!r.ok) setError(`${r.statusCode}: ${r.detail}`);
      }
    } catch (err) {
      setError(err instanceof ServiceError ? `${err.statusCode}: ${err.detail}` : String(err));
    }
    bump();
  }

  function autofill(careless: boolean) {
    if (!assigned) return;
    const truth = trueScoresFor(bench, assigned.id);
    const body = gradeFor(bench.rng, { careless }, { trueScores: truth ?? {} });
    setScores(body.scores);
    setRationale(body.rationale);
    bump();
  }

  function submit() {
    if (!expert || !assigned) return;
    setError(null);
    const wasPaused = expert.status === "paused";
    const ref = assigned.externalRef;
    const golden = assigned.isAttentionCheck;
    const expected = assigned.expectedScores;
    try {
      const grade = platform.submitGrade(expert, assigned.id, scores, rationale, 240);
      const result = platform.attentionResults.find((a) => a.gradeId === grade.id);
      setVerdict({
        ref,
        golden,
        passed: result?.passed ?? true,
        maxDeviation: result?.maxDeviation ?? 0,
        expected: expected ? { ...expected } : null,
        scores: { ...scores },
        pausedNow: !wasPaused && expert.status === "paused",
      });
    } catch (err) {
      setError(err instanceof ServiceError ? `${err.statusCode}: ${err.detail}` : String(err));
    }
    bump();
  }

  function reinstate() {
    if (!expert) return;
    const n = platform.reinstate(expert);
    setVerdict(null);
    setError(`reinstated, ${n} withheld payout${n === 1 ? "" : "s"} moved back to pending`);
    bump();
  }

  return (
    <Section
      id="grading"
      num="02"
      title="Rubric grading and hidden attention checks"
      lede="Scores are validated against the rubric that the task is pinned to, stored as one normalized row per criterion, and combined with the criterion weights. One task in five is a golden check whose answer the platform already knows. The expert is never told which."
    >
      <div className="cols">
        <Card
          title={assigned ? `Grading ${assigned.externalRef}` : "No task in hand"}
          aside={
            <label className="toggle">
              <input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} />
              operator view
            </label>
          }
        >
          {assigned ? (
            <>
              <p className="prompt">
                {assigned.prompt}
                {reveal && assigned.isAttentionCheck ? (
                  <>
                    {" "}
                    <Stamp tone="accent">golden</Stamp>
                  </>
                ) : null}
              </p>
              {rubric?.criteria.map((c) => (
                <div className="criterion" key={c.key}>
                  <div className="criterion-head">
                    <label htmlFor={`score-${c.key}`}>{c.label}</label>
                    <span>
                      <span className="criterion-weight">weight {c.weight.toFixed(1)}</span>{" "}
                      <span className="score-badge">{scores[c.key] ?? 3}</span>
                    </span>
                  </div>
                  <input
                    id={`score-${c.key}`}
                    type="range"
                    min={c.scaleMin}
                    max={c.scaleMax}
                    step={1}
                    value={scores[c.key] ?? 3}
                    onChange={(e) => setScores((s) => ({ ...s, [c.key]: Number(e.target.value) }))}
                  />
                  <div className="criterion-scale">
                    <span>{c.scaleMin} unusable</span>
                    <span>{c.scaleMax} exemplary</span>
                  </div>
                </div>
              ))}
              <dl className="kv section-gap-sm">
                <dt>weighted score</dt>
                <dd>{weightedNow.toFixed(2)}</dd>
                <dt>rate if approved</dt>
                <dd>
                  {expert
                    ? `${money(platform.rateFor(expert, assigned.taskType))} · ${expert.tier} / ${assigned.taskType}`
                    : "-"}
                </dd>
              </dl>
              <div className="controls">
                <button type="button" className="primary" onClick={submit}>
                  Submit grade
                </button>
                <button type="button" className="tiny" onClick={() => autofill(false)}>
                  Autofill: careful
                </button>
                <button type="button" className="tiny" onClick={() => autofill(true)}>
                  Autofill: careless
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="empty">
                {expert?.status === "paused"
                  ? `${expert.name} is paused and cannot claim. Reinstate to release the withheld payouts.`
                  : "Claim a task to see the rubric the expert is asked to fill in."}
              </p>
              <div className="controls">
                <button type="button" className="primary" onClick={() => claim(false)} disabled={expert?.status === "paused"}>
                  Claim next task
                </button>
                <button type="button" onClick={() => claim(true)} disabled={expert?.status === "paused"}>
                  Serve a golden check
                </button>
              </div>
              <p className="card-note">
                Serving a golden check is an operator action here so the guard can be tripped in two
                clicks. In the service it happens on its own, every fifth serve.
              </p>
              <div className="scroll-x section-gap-sm">
                <table>
                  <caption>Rubric: {rubric?.name} v{rubric?.version}</caption>
                  <thead>
                    <tr>
                      <th scope="col">criterion</th>
                      <th scope="col" className="num">
                        weight
                      </th>
                      <th scope="col" className="num">
                        scale
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rubric?.criteria.map((c) => (
                      <tr key={c.key}>
                        <td>{c.label}</td>
                        <td className="num">{c.weight.toFixed(1)}</td>
                        <td className="num">
                          {c.scaleMin} to {c.scaleMax}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <p className="card-note" role="status">
            {error ?? ""}
          </p>
        </Card>

        <div className="stack">
          <Card
            title={`Attention record, ${expert?.name ?? "-"}`}
            aside={expert?.status === "paused" ? <Stamp tone="accent" animate>paused</Stamp> : <Stamp>active</Stamp>}
          >
            <Gauge
              rate={rate}
              threshold={settings.attentionThreshold}
              paused={expert?.status === "paused"}
              checks={checkCount}
              windowSize={settings.attentionWindow}
            />
            <dl className="kv section-gap-sm">
              <dt>rolling window</dt>
              <dd>
                {passedCount}/{checkCount} passed, {pct(rate)}
              </dd>
              <dt>lifetime checks</dt>
              <dd>
                {lifetime.passed}/{lifetime.total} passed
              </dd>
              <dt>pause rule</dt>
              <dd>
                below {pct(settings.attentionThreshold)} after {settings.attentionMinChecks} checks
              </dd>
              <dt>tolerance</dt>
              <dd>±{settings.attentionTolerance} per criterion</dd>
              <dt>payouts withheld</dt>
              <dd>{withheld.length}</dd>
            </dl>
            {expert?.status === "paused" ? (
              <div className="controls">
                <button type="button" onClick={reinstate}>
                  Reinstate expert
                </button>
              </div>
            ) : null}
          </Card>

          <Card title="Last submission">
            {verdict === null ? (
              <p className="empty">Submit a grade and the platform's side of it shows up here.</p>
            ) : (
              <div aria-live="polite">
                <p>
                  {verdict.ref}{" "}
                  {verdict.golden ? (
                    verdict.passed ? (
                      <Stamp animate>check passed</Stamp>
                    ) : (
                      <Stamp tone="accent" animate>check failed</Stamp>
                    )
                  ) : (
                    <Stamp animate>stored, awaiting review</Stamp>
                  )}
                </p>
                {verdict.golden && verdict.expected ? (
                  <div className="scroll-x section-gap-sm">
                    <table>
                      <caption>Hidden expected scores, revealed after the fact</caption>
                      <thead>
                        <tr>
                          <th scope="col">criterion</th>
                          <th scope="col" className="num">
                            expected
                          </th>
                          <th scope="col" className="num">
                            submitted
                          </th>
                          <th scope="col" className="num">
                            deviation
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(verdict.expected).map(([k, v]) => {
                          const got = verdict.scores[k] ?? 0;
                          const dev = Math.abs(got - v);
                          return (
                            <tr key={k} className={dev > settings.attentionTolerance ? "is-out" : ""}>
                              <td>{k}</td>
                              <td className="num">{v}</td>
                              <td className="num">{got}</td>
                              <td className="num">{dev.toFixed(0)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                <p className="card-note">
                  {verdict.golden
                    ? `max deviation ${verdict.maxDeviation.toFixed(0)} against a tolerance of ${settings.attentionTolerance}. A golden task goes straight back to the queue: it is reusable across experts.`
                    : "Non-golden tasks wait for a reviewer before they become payable."}
                </p>
                {verdict.pausedNow ? (
                  <p className="card-note">
                    <Stamp tone="accent" animate>expert paused</Stamp> pending payouts moved to withheld
                    in the same transaction.
                  </p>
                ) : null}
              </div>
            )}
          </Card>
        </div>
      </div>
    </Section>
  );
}
