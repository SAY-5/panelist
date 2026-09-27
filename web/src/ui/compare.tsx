import { compareRows, DemoSummary, REFERENCE_RUN } from "../sim";
import { Card } from "./bits";

export function CompareCard({ summary }: { summary: DemoSummary | null }) {
  const rows = summary === null ? [] : compareRows(summary);
  return (
    <Card title="This run beside the PostgreSQL run">
      {summary === null ? (
        <p className="empty">
          Run the simulation above to compare it line by line with the service run recorded in{" "}
          <code className="code">docs/demo-2026-09-26.json</code>.
        </p>
      ) : (
        <div className="scroll-x">
          <table>
            <caption>
              Service at commit {REFERENCE_RUN.commit}, {REFERENCE_RUN.ranAt.slice(0, 10)}, against this
              simulation
            </caption>
            <thead>
              <tr>
                <th scope="col">measure</th>
                <th scope="col" className="num">service</th>
                <th scope="col" className="num">this page</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.measure}>
                  <th scope="row">
                    {row.measure}
                    <span className="row-why">
                      {row.invariant ? "holds in both" : "differs"}
                      <span className="row-why-text">: {row.why}</span>
                    </span>
                  </th>
                  <td className="num">{row.service}</td>
                  <td className="num">{row.here}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
