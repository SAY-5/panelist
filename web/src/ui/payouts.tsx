import { useState } from "react";
import { PeriodTotals, ServiceError, trueScoresFor, weighted as trueWeighted } from "../sim";
import { Card, Section, Stamp } from "./bits";
import { money } from "./format";
import { useWorkbench } from "./workbench";

/** The reviewer's rule in sim/demo.py: reject a grade that drifts far from the known answer. */
const DRIFT_LIMIT = 1.5;

export function PayoutsSection() {
  const { bench, bump } = useWorkbench();
  const platform = bench.platform;
  const [note, setNote] = useState<string | null>(null);
  const [label, setLabel] = useState("2026-09-A");
  const [showCsv, setShowCsv] = useState(false);
  const [reveal, setReveal] = useState(false);

  const pending = platform.unreviewedGrades();
  const ledger = platform.ledger();
  const closed: PeriodTotals[] = platform.periods.map((p) => platform.periodTotals(p));
  const latest = closed.length > 0 ? closed[closed.length - 1] ?? null : null;
  const latestPeriod = platform.periods.length > 0 ? platform.periods[platform.periods.length - 1] ?? null : null;

  function driftOf(gradeId: string): number | null {
    const g = platform.grades.find((x) => x.id === gradeId);
    if (!g) return null;
    const truth = trueScoresFor(bench, g.taskId);
    if (!truth) return null;
    return Math.abs(g.weightedScore - trueWeighted(truth));
  }

  function decide(gradeId: string, approve: boolean) {
    const g = platform.grades.find((x) => x.id === gradeId);
    if (!g) return;
    const expert = platform.expert(g.expertId);
    const task = platform.task(g.taskId);
    const { payout } = platform.review(gradeId, approve ? "approve" : "reject", approve ? null : "spot check failed");
    const amount = money(payout?.amountCents ?? 0);
    setNote(
      approve
        ? payout?.status === "withheld"
          ? `approved ${task.externalRef}: ${amount} withheld, ${expert.name} is paused`
          : `approved ${task.externalRef}: ${amount} pending for ${expert.name}`
        : `rejected ${task.externalRef}: no payout is created and the task goes back to the queue for another expert`,
    );
    bump();
  }

  function reviewAll() {
    let approved = 0;
    let rejected = 0;
    for (const g of platform.unreviewedGrades()) {
      const drift = driftOf(g.id);
      if (drift !== null && drift > DRIFT_LIMIT) {
        platform.review(g.id, "reject", "spot check failed");
        rejected++;
      } else {
        platform.review(g.id, "approve");
        approved++;
      }
    }
    setNote(`spot check over ${approved + rejected} grades: ${approved} approved, ${rejected} rejected above a drift of ${DRIFT_LIMIT}`);
    bump();
  }

  function close() {
    try {
      const totals = platform.closePeriod(label);
      setNote(`${totals.label} closed: ${totals.payoutCount} payouts, ${money(totals.totalCents)} to ${totals.expertCount} experts`);
    } catch (err) {
      setNote(err instanceof ServiceError ? `${err.statusCode}: ${err.detail}` : String(err));
    }
    bump();
  }

  return (
    <Section
      id="payouts"
      num="03"
      title="Review, rate card and period close"
      lede="A grade is worth nothing until a reviewer approves it. Approval creates one payout at the rate for the expert's tier and the task type, or at a per-expert override. Closing a period sweeps every pending payout into a statement and leaves withheld money exactly where it is."
    >
      <div className="cols">
        <Card
          title={`Unreviewed grades (${pending.length})`}
          aside={
            <label className="toggle">
              <input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} />
              show drift
            </label>
          }
        >
          {pending.length === 0 ? (
            <p className="empty">Every grade has been reviewed. Grade something in section 02 to refill this queue.</p>
          ) : (
            <div className="scroll-x">
              <table>
                <caption>Awaiting a reviewer decision</caption>
                <thead>
                  <tr>
                    <th scope="col">task</th>
                    <th scope="col">expert</th>
                    <th scope="col" className="num">
                      weighted
                    </th>
                    {reveal ? (
                      <th scope="col" className="num">
                        drift
                      </th>
                    ) : null}
                    <th scope="col" className="num">
                      decision
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pending.map((g) => {
                    const drift = driftOf(g.id);
                    const bad = drift !== null && drift > DRIFT_LIMIT;
                    return (
                      <tr key={g.id} className={bad && reveal ? "is-out" : ""}>
                        <td>{platform.task(g.taskId).externalRef}</td>
                        <td>{platform.expert(g.expertId).name}</td>
                        <td className="num">{g.weightedScore.toFixed(2)}</td>
                        {reveal ? <td className="num">{drift === null ? "-" : drift.toFixed(2)}</td> : null}
                        <td className="num">
                          <button type="button" className="tiny" onClick={() => decide(g.id, true)}>
                            approve
                          </button>{" "}
                          <button type="button" className="tiny danger" onClick={() => decide(g.id, false)}>
                            reject
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="controls">
            <button type="button" onClick={reviewAll} disabled={pending.length === 0}>
              Spot check all
            </button>
          </div>
          <p className="card-note">
            Drift is the distance between the weighted score and the answer the simulation knows.
            The reviewer in <code className="code">sim/demo.py</code> rejects anything past{" "}
            {DRIFT_LIMIT}; a human reviewer reads the rationale instead.
          </p>
        </Card>

        <div className="stack">
          <Card title="Payout ledger" aside={<span className="queue-meta">{platform.payouts.length} payouts</span>}>
            {ledger.rows.length === 0 ? (
              <p className="empty">No payouts yet. Approve a grade to create the first one.</p>
            ) : (
              <div className="scroll-x">
                <table>
                  <caption>Derived from the payout rows, grouped by expert and status</caption>
                  <thead>
                    <tr>
                      <th scope="col">expert</th>
                      <th scope="col">status</th>
                      <th scope="col" className="num">
                        n
                      </th>
                      <th scope="col" className="num">
                        total
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.rows.map((r) => (
                      <tr key={`${r.expertId}:${r.status}`} className={r.status === "withheld" ? "is-out" : ""}>
                        <td>{r.expertName}</td>
                        <td>
                          {r.status === "withheld" ? <Stamp tone="accent">withheld</Stamp> : <Stamp>{r.status}</Stamp>}
                        </td>
                        <td className="num">{r.payoutCount}</td>
                        <td className="num">{money(r.totalCents)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan={2}>grand total</td>
                      <td className="num">{ledger.countsByStatus.pending + ledger.countsByStatus.withheld + ledger.countsByStatus.paid}</td>
                      <td className="num">{money(ledger.grandTotalCents)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </Card>

          <Card title="Close a period">
            <div className="controls tight">
              <label className="toggle" htmlFor="period-label">
                label
              </label>
              <input id="period-label" type="text" value={label} onChange={(e) => setLabel(e.target.value)} size={12} />
              <button type="button" className="primary" onClick={close} disabled={ledger.countsByStatus.pending === 0}>
                Close period
              </button>
              <button type="button" onClick={() => setShowCsv((v) => !v)} disabled={latestPeriod === null}>
                {showCsv ? "Hide statement CSV" : "Statement CSV"}
              </button>
            </div>
            <dl className="kv section-gap-sm">
              <dt>pending</dt>
              <dd>
                {ledger.countsByStatus.pending} · {money(ledger.totalsByStatus.pending)}
              </dd>
              <dt>withheld</dt>
              <dd>
                {ledger.countsByStatus.withheld} · {money(ledger.totalsByStatus.withheld)}
              </dd>
              <dt>paid</dt>
              <dd>
                {ledger.countsByStatus.paid} · {money(ledger.totalsByStatus.paid)}
              </dd>
              {latest ? (
                <>
                  <dt>{latest.label}</dt>
                  <dd>
                    {latest.payoutCount} payouts, {money(latest.totalCents)}, {latest.expertCount} experts
                  </dd>
                </>
              ) : null}
            </dl>
            {showCsv && latestPeriod ? (
              <pre className="csv section-gap-sm">{platform.periodCsv(latestPeriod).split("\r\n").slice(0, 6).join("\n")}</pre>
            ) : null}
            <p className="card-note" role="status">
              {note ?? "Withheld money never enters a statement; reinstating the expert in section 02 moves it back to pending first."}
            </p>
          </Card>
        </div>
      </div>
    </Section>
  );
}
