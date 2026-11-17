import { Caution, Unresolved } from "@/components/Absence";
import { Figure, formatFigure } from "@/components/Figure";
import {
  Card,
  Empty,
  PageHeader,
  Pill,
  Spinner,
  Stat,
  StatRow,
  TableScroller,
} from "@/components/Primitives";
import { getWalkForward } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import "./WalkForwardView.css";

/**
 * Did the edge survive out of sample?
 *
 * **The mean is never shown alone, and that is the whole design of this
 * screen.** `edge_retained` divides out-of-sample CAGR by in-sample CAGR, so
 * a window that barely made money in sample produces an enormous ratio from
 * an ordinary out-of-sample year — and an arithmetic mean is dominated by
 * exactly those windows.
 *
 * On the real robustness record the mean reads **4.21** against a bar of
 * 0.5, which is true and would be read as a triumph. The median is 1.68,
 * thirteen of twenty-one windows clear the bar, and **eight lost money out of
 * sample while making it in sample**. So the headline is the count clearing
 * the bar, and the mean sits beside it carrying its own warning.
 */
export function WalkForwardView() {
  const { data, error, loading } = useAsync(getWalkForward, []);

  if (loading) return <Spinner />;
  if (error) return <Unresolved what="Walk-forward" reason={error.message} />;
  if (!data) return <Empty>Nothing to show.</Empty>;

  if (data.window_count === 0) {
    return (
      <>
        <PageHeader title="Walk-forward" />
        <Unresolved
          what="Walk-forward windows"
          reason={
            "This record holds no paired in-sample/out-of-sample runs. The " +
            "pairing lives in the run name — `…-is` and `…-oos` — and this " +
            "database has none." +
            (data.unpaired.length
              ? ` Unpaired runs: ${data.unpaired.join(", ")}.`
              : "")
          }
        />
      </>
    );
  }

  const clearing = data.windows_clearing_bar ?? 0;
  const share = data.resolved_count ? clearing / data.resolved_count : 0;

  return (
    <>
      <PageHeader
        title="Walk-forward"
        subtitle={`${data.window_count} windows, each trained in sample and tested on the year after`}
      />

      {/* The headline is the COUNT, not the mean. A count cannot be dragged
          by one window with a near-zero denominator. */}
      <Card>
        <div className="hero">
          <div>
            <div className="hero__cap">Windows keeping half the edge</div>
            <div
              className="hero__big num"
              style={share < 0.5 ? { color: "var(--serious)" } : undefined}
            >
              {clearing} of {data.resolved_count}
            </div>
            <p className="hero__ctx">
              The bar is {formatFigure(data.bar, "ratio")} — half the in-sample
              edge must survive. {data.unresolved_count} further{" "}
              {data.unresolved_count === 1 ? "window has" : "windows have"} no
              in-sample edge to retain and {data.unresolved_count === 1 ? "is" : "are"}{" "}
              unresolved rather than scored zero.
            </p>
          </div>
          <div>
            <StatRow>
              <Stat
                label="Median retained"
                value={formatFigure(data.median_edge_retained, "ratio")}
                context="Half the windows did better than this"
              />
              <Stat
                label="Mean retained"
                value={formatFigure(data.mean_edge_retained, "ratio")}
                context="Read the distribution first"
                tone="warning"
              />
              <Stat
                label="Lost money out of sample"
                value={<Figure value={data.windows_negative} kind="count" />}
                context="Made money in sample, lost it out"
                tone={data.windows_negative ? "warning" : "neutral"}
              />
            </StatRow>
          </div>
        </div>
      </Card>

      {/* The warning travels with the mean, always. */}
      {data.distribution_note && (
        <Caution title="The mean is not the headline">
          {data.distribution_note}
        </Caution>
      )}

      {data.unpaired.length > 0 && (
        <Caution title="Runs with no partner">
          {data.unpaired.join(", ")}. A missing out-of-sample leg is the one
          most worth noticing, so these are reported rather than dropped.
        </Caution>
      )}

      <Card title="Every window" flush>
        <TableScroller>
          <table>
            <thead>
              <tr>
                <th>Window</th>
                <th>In sample</th>
                <th className="r">IS CAGR</th>
                <th>Out of sample</th>
                <th className="r">OOS CAGR</th>
                <th className="r">Retained</th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {data.windows.map((w) => (
                <tr key={w.window}>
                  <td className="mono">{w.window}</td>
                  <td className="mono wf__span">
                    {w.in_sample.start} → {w.in_sample.end}
                  </td>
                  <td className="r">
                    <Figure value={w.in_sample.cagr} kind="percent" />
                  </td>
                  <td className="mono wf__span">
                    {w.out_of_sample.start} → {w.out_of_sample.end}
                  </td>
                  <td
                    className="r"
                    style={
                      (w.out_of_sample.cagr ?? 0) < 0
                        ? { color: "var(--critical)" }
                        : undefined
                    }
                  >
                    <Figure value={w.out_of_sample.cagr} kind="percent" />
                  </td>
                  <td className="r">
                    <Figure value={w.edge_retained} kind="ratio" />
                  </td>
                  <td>
                    {/* Never colour alone: the verdict is a word. */}
                    {w.clears_bar === null ? (
                      <Pill>no edge to retain</Pill>
                    ) : w.clears_bar ? (
                      <Pill tone="good">kept half</Pill>
                    ) : (
                      <Pill tone="warning">below the bar</Pill>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroller>
      </Card>

      <p className="wf__note">{data.note}</p>
      <p className="wf__note">Bar: {data.bar_citation}</p>
    </>
  );
}
