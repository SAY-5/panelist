import { CRITERIA, Platform, sha256Hex, utf8Length } from "../sim";
import { Card, Section, Stamp } from "./bits";
import { useMeasuredRun } from "./demo";
import { pct } from "./format";
import { useWorkbench } from "./workbench";

function Hex({ value }: { value: string }) {
  return (
    <span className="hex">
      <b>{value.slice(0, 12)}</b>
      {value.slice(12)}
    </span>
  );
}

export function DeliverySection() {
  const { bench, bump } = useWorkbench();
  const platform = bench.platform;
  const run = useMeasuredRun();

  const rows = platform.deliveryRows();
  const body = Platform.jsonl(rows);
  const checksum = rows.length > 0 ? sha256Hex(body) : sha256Hex("");
  const bytes = utf8Length(body);

  const altered = rows.map((r, i) => (i === 0 ? { ...r, rationale: `${r.rationale} (edited)` } : r));
  const alteredChecksum = rows.length > 0 ? sha256Hex(Platform.jsonl(altered)) : "";

  const agreement = platform.globalAgreement();
  const criteria = run?.summary.criteria ?? platform.criterionMeans();
  const criteriaSource = run ? "500-task run" : "workbench";
  const firstLine = body.split("\n")[0] ?? "";

  return (
    <Section
      id="delivery"
      num="04"
      title="Delivery, checksums and agreement"
      lede="Approved non-golden grades become a JSONL dataset: keys sorted, separators compact, one row per grade. The sha256 of that body is the version's identity, and it is part of the object name, so a delivered file that no longer hashes to its name has been tampered with."
    >
      <div className="cols">
        <Card
          title="Dataset"
          aside={<span className="queue-meta">{rows.length} rows · {bytes.toLocaleString("en-US")} bytes</span>}
        >
          {rows.length === 0 ? (
            <p className="empty">Nothing approved yet. Approve a grade in section 03 and the dataset appears here.</p>
          ) : (
            <div className="scroll-x">
              <table>
                <caption>Rows in the next export, ordered by task sequence then expert</caption>
                <thead>
                  <tr>
                    <th scope="col">task</th>
                    <th scope="col">expert</th>
                    {CRITERIA.map((c) => (
                      <th scope="col" className="num" key={c}>
                        {c.slice(0, 4)}
                      </th>
                    ))}
                    <th scope="col" className="num">
                      weighted
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, 8).map((r) => (
                    <tr key={`${r.task_id}:${r.expert_id}`}>
                      <td>{r.external_ref}</td>
                      <td>{platform.expert(r.expert_id).name}</td>
                      {CRITERIA.map((c) => (
                        <td className="num" key={c}>
                          {r.scores[c] ?? "-"}
                        </td>
                      ))}
                      <td className="num">{r.weighted_score.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {rows.length > 8 ? <p className="card-note">{rows.length - 8} more rows not shown.</p> : null}
          {firstLine ? (
            <pre className="csv section-gap-sm">{firstLine.slice(0, 420)}…</pre>
          ) : null}
        </Card>

        <div className="stack">
          <Card title="Checksum" aside={<Stamp>live</Stamp>}>
            <dl className="kv">
              <dt>sha256 of the body</dt>
              <dd>
                <Hex value={checksum} />
              </dd>
              <dt>if one rationale changed</dt>
              <dd>{alteredChecksum ? <Hex value={alteredChecksum} /> : <span className="hex">n/a</span>}</dd>
            </dl>
            <p className="card-note">
              The first twelve hex digits become the object name:{" "}
              <code className="code">panelist-grades-v{platform.deliveries.length + 1}-{checksum.slice(0, 12)}.jsonl</code>.
              Exporting the same rows twice gives the same digest; changing a single character of a
              single rationale does not.
            </p>
            <div className="controls">
              <button
                type="button"
                className="primary"
                onClick={() => {
                  platform.exportDelivery();
                  bump();
                }}
                disabled={rows.length === 0}
              >
                Export a version
              </button>
            </div>
            {platform.deliveries.length > 0 ? (
              <div className="scroll-x section-gap-sm">
                <table>
                  <caption>Stored versions</caption>
                  <thead>
                    <tr>
                      <th scope="col">v</th>
                      <th scope="col" className="num">
                        rows
                      </th>
                      <th scope="col" className="num">
                        bytes
                      </th>
                      <th scope="col">sha256</th>
                    </tr>
                  </thead>
                  <tbody>
                    {platform.deliveries.map((d) => (
                      <tr key={d.version}>
                        <td>v{d.version}</td>
                        <td className="num">{d.rowCount}</td>
                        <td className="num">{d.sizeBytes.toLocaleString("en-US")}</td>
                        <td>
                          <span className="hex">
                            <b>{d.checksum.slice(0, 12)}</b>
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Card>

          <Card title="Inter-rater agreement">
            <div className="scroll-x">
              <table>
                <caption>Pairwise, per criterion, across multi-graded non-golden tasks</caption>
                <thead>
                  <tr>
                    <th scope="col">measure</th>
                    <th scope="col" className="num">
                      workbench
                    </th>
                    <th scope="col" className="num">
                      500-task run
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>multi-graded tasks</td>
                    <td className="num">{agreement.multiGradedTasks}</td>
                    <td className="num">{run ? run.summary.agreement.multiGradedTasks : "–"}</td>
                  </tr>
                  <tr>
                    <td>score pairs</td>
                    <td className="num">{agreement.comparedPairs}</td>
                    <td className="num">{run ? run.summary.agreement.comparedPairs : "–"}</td>
                  </tr>
                  <tr>
                    <td>mean absolute difference</td>
                    <td className="num">{agreement.meanAbsDiff === null ? "n/a" : agreement.meanAbsDiff.toFixed(3)}</td>
                    <td className="num">{run ? (run.summary.agreement.meanAbsDiff ?? 0).toFixed(3) : "–"}</td>
                  </tr>
                  <tr>
                    <td>exact</td>
                    <td className="num">{pct(agreement.exactAgreement)}</td>
                    <td className="num">{run ? pct(run.summary.agreement.exactAgreement) : "–"}</td>
                  </tr>
                  <tr>
                    <td>within one</td>
                    <td className="num">{pct(agreement.withinOne)}</td>
                    <td className="num">{run ? pct(run.summary.agreement.withinOne) : "–"}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="card-note">Criterion means, {criteriaSource}:</p>
            {criteria.map((c) => (
              <div className="bar-row" key={c.key}>
                <span>{c.key}</span>
                <span className="bar">
                  <i style={{ width: `${(c.mean / 5) * 100}%` }} />
                </span>
                <span>{c.mean.toFixed(2)}</span>
              </div>
            ))}
          </Card>
        </div>
      </div>
    </Section>
  );
}
