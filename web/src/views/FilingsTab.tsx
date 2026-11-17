import { Caution, Unresolved } from "@/components/Absence";
import { Figure } from "@/components/Figure";
import {
  Card,
  Empty,
  Spinner,
  Stat,
  StatRow,
  TableScroller,
} from "@/components/Primitives";
import { ApiError, getFilings } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import "./FilingsTab.css";

/**
 * What this issuer filed — and whether the record of it is complete.
 *
 * **Completeness is the question, not the list.** IMC Global is the worked
 * example: its filing record stops on 2004-11-19 while the security traded
 * until 2018-01-30, 4,820 days later. Shown as a plain list, that reads as a
 * company that stopped filing in 2004. It is a record that stops, and only
 * the second is established.
 */
export function FilingsTab({ securityId }: { securityId: string }) {
  const { data, error, loading } = useAsync(
    () => getFilings(securityId),
    [securityId],
  );

  if (loading) return <Spinner />;
  if (error) {
    return (
      <Unresolved
        what="Filings"
        reason={
          error instanceof ApiError && error.status === 501
            ? "Filings read the research corpus, a different database from the decision record. Start the dashboard with --corpus."
            : error.message
        }
      />
    );
  }
  if (!data) return <Empty>Nothing to show.</Empty>;
  if (!data.available) {
    return <Unresolved what="Filings" reason={data.note ?? "No data."} />;
  }

  return (
    <>
      {/* Above the list, because it changes what the list means. */}
      {data.stops_early && (
        <Caution title="The filing record stops before the security did">
          {data.stops_early}
        </Caution>
      )}

      <StatRow>
        <Stat
          label="Filings recorded"
          value={<Figure value={data.total} kind="count" />}
          context={`${data.first_filed} → ${data.last_filed}`}
        />
        <Stat
          label="Distinct forms"
          value={<Figure value={data.forms?.length} kind="count" />}
        />
        <Stat
          label="Traded until"
          value={data.last_session ?? "—"}
          context={data.stops_early ? "After the record ends" : "Within the record"}
          tone={data.stops_early ? "warning" : "neutral"}
        />
      </StatRow>

      {/* Where the record came from decides what its gaps mean. */}
      <p className="fil__note">
        <strong>{data.issuer?.name}</strong> — {data.source_note}
      </p>

      <Card title="Forms filed" flush>
        <TableScroller>
          <table>
            <thead>
              <tr>
                <th>Form</th>
                <th className="r">Count</th>
                <th>First</th>
                <th>Last</th>
              </tr>
            </thead>
            <tbody>
              {(data.forms ?? []).map((f) => (
                <tr key={f.form_type}>
                  {/* Verbatim: `10-K` is not `10-K405`, and form strings are
                      recorded exactly as filed and never normalised. */}
                  <td className="mono">{f.form_type}</td>
                  <td className="r">
                    <Figure value={f.count} kind="count" />
                  </td>
                  <td className="mono fil__date">{f.first}</td>
                  <td className="mono fil__date">{f.last}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroller>
      </Card>

      <Card title="Most recent" flush>
        <TableScroller>
          <table>
            <thead>
              <tr>
                <th>Filed</th>
                <th>Form</th>
                <th>Period</th>
                <th>Accession</th>
              </tr>
            </thead>
            <tbody>
              {(data.recent ?? []).map((f) => (
                <tr key={f.accession}>
                  <td className="mono fil__date">{f.filed_at}</td>
                  <td className="mono">{f.form_type}</td>
                  <td className="mono fil__date">{f.period_end ?? "—"}</td>
                  <td className="mono fil__acc">{f.accession}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroller>
      </Card>

      {data.truncated && (
        <p className="fil__note">
          Showing the {data.recent?.length} most recent of {data.total}.
        </p>
      )}
      <p className="fil__note">{data.scope_note}</p>
    </>
  );
}
