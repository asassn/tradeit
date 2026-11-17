import { Unresolved, Caution } from "@/components/Absence";
import { Figure } from "@/components/Figure";
import {
  Card,
  Empty,
  PageHeader,
  Pill,
  Spinner,
  TableScroller,
} from "@/components/Primitives";
import { getRuns } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";

export function RunsView() {
  const { data: runs, error, loading } = useAsync(getRuns, []);

  if (loading) return <Spinner />;
  if (error) return <Unresolved what="Runs" reason={error.message} />;
  if (!runs?.length) return <Empty>No runs recorded in this database.</Empty>;

  const disagreements = runs.filter((r) => r.trade_count_disagrees);

  return (
    <>
      <PageHeader
        title="Strategy runs"
        subtitle={`${runs.length} recorded in this database`}
      />

      <Card flush>
        <TableScroller>
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Window</th>
                <th className="r">Trades</th>
                <th className="r">CAGR</th>
                <th className="r">Max drawdown</th>
                <th className="r">Sharpe</th>
                <th className="r">Win rate</th>
                <th>Sample</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    {run.name}
                    <br />
                    <span style={{ color: "var(--ink-3)", font: "var(--t-caption)" }}>
                      {run.status}
                    </span>
                  </td>
                  <td className="mono">
                    {run.start}
                    <br />
                    {run.end}
                  </td>
                  <td className="r">
                    <Figure value={run.trades_recorded} kind="count" />{" "}
                    {/* Sample size beside the ratios, never instead of them:
                        a Sharpe from nineteen trades is a number, not
                        evidence. */}
                    {!run.statistically_meaningful && (
                      <Pill tone="warning">under 30</Pill>
                    )}
                  </td>
                  <td className="r">
                    <Figure value={run.metrics.cagr} kind="percent" />
                  </td>
                  <td className="r">
                    <Figure value={run.metrics.max_drawdown_pct} kind="percent" />
                  </td>
                  <td className="r">
                    <Figure value={run.metrics.sharpe} kind="ratio" />
                  </td>
                  <td className="r">
                    <Figure value={run.metrics.win_rate} kind="percent" />
                  </td>
                  <td>
                    {run.is_out_of_sample ? (
                      <Pill tone="accent">Out of sample</Pill>
                    ) : (
                      <Pill>In sample</Pill>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroller>
      </Card>

      {/* Surfaced, never silently corrected. The displayed count is counted
          from the trade rows; the stored column is still wrong on disk for
          anyone querying the table directly. */}
      {disagreements.map((run) => (
        <Caution key={run.id} title={`${run.name}: stored trade count disagrees`}>
          {run.trade_count_disagrees}
        </Caution>
      ))}
    </>
  );
}
