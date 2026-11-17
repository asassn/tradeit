import { Caution, Provenance, Unresolved } from "@/components/Absence";
import { formatFigure } from "@/components/Figure";
import {
  Card,
  Empty,
  Pill,
  Spinner,
  TableScroller,
} from "@/components/Primitives";
import { ApiError, getFundamentals } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import "./FundamentalsTab.css";

/**
 * The as-filed statement spine.
 *
 * **Every figure is the value as FIRST filed**, with the date it became
 * public. A later filing restating the same period is not shown, because it
 * was not knowable at the time and acting on it is look-ahead. The dates earn
 * their place: Apple's FY2017 and FY2018 revenue were first filed in **2019**,
 * when the FY2019 10-K restated them under the ASC 606 tag.
 *
 * Periods before 2013 are marked. This corpus's `knowledge_time` begins in
 * 2009 and the median gap from period end is 1,574 days, so an early row is a
 * later filing being read as a contemporaneous one.
 */
export function FundamentalsTab({ securityId }: { securityId: string }) {
  const { data, error, loading } = useAsync(
    () => getFundamentals(securityId),
    [securityId],
  );

  if (loading) return <Spinner />;
  if (error) {
    return (
      <Unresolved
        what="Fundamentals"
        reason={
          error instanceof ApiError && error.status === 501
            ? "Fundamentals read the research corpus, a different database from the decision record. Start the dashboard with --corpus."
            : error.message
        }
      />
    );
  }
  if (!data) return <Empty>Nothing to show.</Empty>;
  if (!data.available) {
    return <Unresolved what="Fundamentals" reason={data.note ?? "No data."} />;
  }

  const periods = data.periods ?? [];
  const lines = data.lines ?? [];
  const statements = [...new Set(lines.map((l) => l.statement))];

  return (
    <>
      {/* An accounting-standard change read as a business event is the
          mistake this flag exists to prevent. */}
      {data.tag_change_note && (
        <Caution title="A line is reported under more than one tag">
          {data.tag_change_note}
        </Caution>
      )}

      {/* Only when it applies — a note that is always there stops being read. */}
      {data.point_in_time_note && (
        <Caution title="Early periods are not point-in-time">
          {data.point_in_time_note}
        </Caution>
      )}

      {statements.map((statement) => (
        <Card key={statement} title={statement.replace(/^\w/, (c) => c.toUpperCase())} flush>
          <TableScroller>
            <table className="fund">
              <thead>
                <tr>
                  <th className="fund__label">Line</th>
                  {periods.map((p) => (
                    <th key={p} className="r">
                      {p.slice(0, 4)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {lines
                  .filter((l) => l.statement === statement)
                  .map((line) => (
                    <tr key={line.label}>
                      <td className="fund__label">{line.label}</td>
                      {line.values.map((cell) => (
                        <td key={cell.period_end} className="r num">
                          {cell.value === null ? (
                            <span className="fund__absent">&mdash;</span>
                          ) : (
                            <span
                              // The filing date on hover, so the table stays
                              // scannable while the provenance stays reachable.
                              title={
                                cell.first_filed
                                  ? `first filed ${cell.first_filed} · ${cell.tag}`
                                  : undefined
                              }
                              className={
                                cell.point_in_time === false ? "fund__stale" : undefined
                              }
                            >
                              {formatFigure(
                                cell.value,
                                line.label === "Shares outstanding" ? "quantity" : "money",
                              )}
                            </span>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
              </tbody>
            </table>
          </TableScroller>
        </Card>
      ))}

      <Provenance
        summary="When each figure became public"
        lines={lines.flatMap((line) =>
          line.values
            .filter((cell) => cell.value !== null && cell.first_filed)
            .map(
              (cell) =>
                `${line.label} ${cell.period_end}: ${formatFigure(cell.value, "money")} — first filed ${cell.first_filed}, tag ${cell.tag}`,
            ),
        )}
      />

      <p className="fund__note">
        {data.first_filed_note} {data.tag_changes?.length ? (
          <Pill tone="warning">{data.tag_changes.length} line with mixed tags</Pill>
        ) : null}
      </p>
    </>
  );
}
