import { useState } from "react";
import { Expert, ServiceError, Task } from "../sim";
import { Card, Section, Stamp, Tag } from "./bits";
import { hours, tick } from "./format";
import { useWorkbench } from "./workbench";

interface RaceRow {
  expert: string;
  code: number;
  detail: string;
}

function TaskLine({ task, rank, now, reveal }: { task: Task; rank: number; now: number; reveal: boolean }) {
  return (
    <li className="queue-row">
      <span className="queue-rank">{rank}</span>
      <span>
        <span>{task.externalRef}</span>{" "}
        {task.requiredTags.map((t) => (
          <Tag key={t}>{t}</Tag>
        ))}
        {reveal && task.isAttentionCheck ? <Stamp tone="accent">golden</Stamp> : null}
      </span>
      <span className="queue-meta">
        p{task.priority} · {task.minTier} · {task.deadline === null ? "no deadline" : `due ${hours(task.deadline, now)}`}
      </span>
    </li>
  );
}

export function RoutingSection() {
  const { bench, bump, expertId, setExpertId } = useWorkbench();
  const platform = bench.platform;
  const [reveal, setReveal] = useState(false);
  const [claimNote, setClaimNote] = useState<string | null>(null);
  const [sweepNote, setSweepNote] = useState<string | null>(null);
  const [raceRows, setRaceRows] = useState<RaceRow[] | null>(null);
  const [raceTaskId, setRaceTaskId] = useState<string>("");

  const now = platform.clock.now();
  const expert: Expert | null = platform.experts.find((e) => e.id === expertId) ?? platform.experts[0] ?? null;
  const queue = expert ? platform.candidates(expert).slice(0, 7) : [];
  const held = expert ? platform.tasks.filter((t) => t.assignedExpertId === expert.id) : [];

  // Recomputed every render: every section mutates the one shared platform.
  const contested = platform.tasks
    .filter((t) => t.status === "queued")
    .map((t) => ({ task: t, n: platform.experts.filter((e) => platform.isEligible(e, t)).length }))
    .filter((c) => c.n >= 2)
    .sort((a, b) => b.n - a.n || a.task.seq - b.task.seq);

  const raceTask = platform.tasks.find((t) => t.id === raceTaskId) ?? contested[0]?.task ?? null;
  const raceOwner = raceTask?.assignedExpertId ?? null;

  function claimNext() {
    if (!expert) return;
    const r = platform.claimNext(expert);
    setClaimNote(
      r.ok
        ? `${expert.name} was served ${r.task.externalRef} (${r.task.requiredTags.join(", ")}), lease until ${tick(r.task.leaseExpiresAt ?? 0)}${r.preferredGolden ? ", the queue owed this expert a hidden check" : ""}`
        : `${r.statusCode}: ${r.detail}`,
    );
    setSweepNote(null);
    bump();
  }

  function advance(ms: number) {
    platform.clock.advance(ms);
    setSweepNote(null);
    bump();
  }

  function sweep() {
    const n = platform.reclaimExpired();
    setSweepNote(n === 0 ? "no lease had expired, nothing moved" : `${n} expired lease${n === 1 ? "" : "s"} returned to the queue`);
    bump();
  }

  function runRace() {
    if (!raceTask) return;
    const claimants = platform.experts.filter((e) => platform.isEligible(e, raceTask));
    const rows: RaceRow[] = [];
    for (const e of claimants) {
      try {
        const t = platform.claimById(e, raceTask.id, true);
        rows.push({ expert: e.name, code: 201, detail: `lease until ${tick(t.leaseExpiresAt ?? 0)}` });
      } catch (err) {
        if (err instanceof ServiceError) rows.push({ expert: e.name, code: err.statusCode, detail: err.detail });
        else throw err;
      }
    }
    platform.releaseLock(raceTask.id);
    setRaceRows(rows);
    bump();
  }

  function releaseRace() {
    if (!raceTask || raceOwner === null) return;
    platform.release(platform.expert(raceOwner), raceTask.id);
    setRaceRows(null);
    bump();
  }

  return (
    <Section
      id="routing"
      num="01"
      title="Routing, leases and the claim race"
      lede="A claim is one transaction. It picks the highest-priority task whose required tags overlap the expert's, gates it on tier, skips rows another transaction holds, and stamps a lease. Two experts reaching for the same row is the interesting case, so it has its own control below."
    >
      <div className="cols">
        <Card
          title="Experts"
          aside={
            <label className="toggle">
              <input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} />
              operator view
            </label>
          }
        >
          <div className="expert-list">
            {platform.experts.map((e) => (
              <button
                key={e.id}
                type="button"
                className="expert-row"
                aria-pressed={e.id === expert?.id}
                onClick={() => setExpertId(e.id)}
              >
                <span className="expert-name">{e.name}</span>
                <span className="queue-meta">
                  {e.tier} · {e.servedCount} served{" "}
                  {e.status === "paused" ? <Stamp tone="accent">paused</Stamp> : null}
                </span>
                <span className="expert-tags">
                  {e.tags.map((t) => (
                    <Tag key={t} active>
                      {t}
                    </Tag>
                  ))}
                </span>
              </button>
            ))}
          </div>
          <p className="card-note">
            Tier is a floor, not a match: a lead may take junior work, a junior may not take
            senior work.
          </p>
        </Card>

        <div className="stack">
          <Card title={`Queue for ${expert?.name ?? "no expert"}`} aside={<span className="queue-meta">{tick(now)}</span>}>
            {queue.length === 0 ? (
              <p className="empty">Nothing eligible: no queued task overlaps these tags, or this expert already graded them.</p>
            ) : (
              <ol className="queue-list">
                {queue.map((t, i) => (
                  <TaskLine key={t.id} task={t} rank={i + 1} now={now} reveal={reveal} />
                ))}
              </ol>
            )}
            <p className="card-note">
              Ordered by <code className="code">priority DESC, deadline ASC NULLS LAST, seq ASC</code>. The
              expert-facing payload never carries the golden flag or its expected scores; turn on
              operator view to see what the queue is really holding.
            </p>
            <div className="controls">
              <button type="button" className="primary" onClick={claimNext} disabled={!expert}>
                Claim next task
              </button>
              <button type="button" onClick={() => advance(1000)}>
                Clock +1s
              </button>
              <button type="button" onClick={() => advance(4000)}>
                Clock +4s
              </button>
              <button type="button" onClick={sweep}>
                Sweep expired leases
              </button>
            </div>
            {claimNote ? (
              <p className="card-note" role="status">
                {claimNote}
              </p>
            ) : null}
            {sweepNote ? (
              <p className="card-note" role="status">
                {sweepNote}
              </p>
            ) : null}
            {held.length > 0 ? (
              <div className="scroll-x section-gap-sm">
                <table>
                  <caption>Held by {expert?.name}</caption>
                  <thead>
                    <tr>
                      <th scope="col">task</th>
                      <th scope="col">lease</th>
                      <th scope="col" className="num">
                        state
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {held.map((t) => {
                      const expired = t.leaseExpiresAt !== null && t.leaseExpiresAt < now;
                      return (
                        <tr key={t.id}>
                          <td>{t.externalRef}</td>
                          <td>{t.leaseExpiresAt === null ? "-" : tick(t.leaseExpiresAt)}</td>
                          <td className="num">
                            {expired ? <Stamp tone="accent" animate>expired</Stamp> : <Stamp>held</Stamp>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Card>
        </div>
      </div>

      <div className="section-gap">
        <Card
          title="Concurrent claim on one row"
          aside={<span className="queue-meta">blocked so far: {platform.metrics.doubleAssignBlocked}</span>}
        >
          <div className="controls tight">
            <label className="toggle" htmlFor="race-task">
              contested task
            </label>
            <select
              id="race-task"
              value={raceTask?.id ?? ""}
              onChange={(e) => {
                setRaceTaskId(e.target.value);
                setRaceRows(null);
              }}
            >
              {contested.slice(0, 8).map((c) => (
                <option key={c.task.id} value={c.task.id}>
                  {c.task.externalRef} · {c.task.requiredTags.join("+")} · {c.n} eligible
                </option>
              ))}
              {raceTask && raceTask.status !== "queued" ? (
                <option value={raceTask.id}>{raceTask.externalRef} · claimed</option>
              ) : null}
            </select>
            <button type="button" className="primary" onClick={runRace} disabled={!raceTask || raceTask.status !== "queued"}>
              Everyone claims at once
            </button>
            <button type="button" onClick={releaseRace} disabled={raceOwner === null}>
              Release the row
            </button>
          </div>

          {raceRows === null ? (
            <p className="card-note">
              Each eligible expert issues <code className="code">POST /tasks/&#123;id&#125;/claim</code> against the
              same row inside its own transaction. The first one holds it under{" "}
              <code className="code">SELECT … FOR UPDATE</code>; everyone else is turned away.
            </p>
          ) : (
            <div className="section-gap-sm">
              {raceRows.map((r, i) => (
                <div key={r.expert} className={`race-row${r.code === 201 ? "" : " is-out"} row-in`} style={{ animationDelay: `${i * 45}ms` }}>
                  <span>{r.expert}</span>
                  <span className="race-code">
                    {r.code === 201 ? <Stamp animate>201 assigned</Stamp> : <Stamp tone="accent" animate>{r.code}</Stamp>}
                  </span>
                  <span className="race-detail">{r.detail}</span>
                </div>
              ))}
              <p className="card-note">
                {raceRows.filter((r) => r.code === 201).length} owner,{" "}
                {raceRows.filter((r) => r.code === 409).length} rejected with 409. The full 500-task
                run repeats this with all 40 experts and blocks every one of the 40 attempts.
              </p>
            </div>
          )}
        </Card>
      </div>
    </Section>
  );
}
