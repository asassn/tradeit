import { Unresolved } from "@/components/Absence";
import { Figure } from "@/components/Figure";
import {
  Card,
  Empty,
  Pill,
  Spinner,
  TableScroller,
} from "@/components/Primitives";
import { ApiError, getProvenance } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import "./ProvenanceTab.css";

/**
 * Where this security's facts came from.
 *
 * **The content that distinguishes this corpus from a price feed.** Every
 * ticker interval carries the filing text that evidenced it, every split the
 * corpus had to settle carries its verdict and who decided it. A vendor hands
 * you a series; this hands you the series and its argument.
 *
 * Citations are shown in full and never truncated. An interval without its
 * evidence is an assertion, and the evidence is frequently the most
 * interesting thing on the page — "Our common shares are quoted on the OTC
 * Bulletin Board under the symbol AAAB" is how a ticker was bound to a CIK.
 */
export function ProvenanceTab({ securityId }: { securityId: string }) {
  const { data, error, loading } = useAsync(
    () => getProvenance(securityId),
    [securityId],
  );

  if (loading) return <Spinner />;
  if (error) {
    return (
      <Unresolved
        what="Provenance"
        reason={
          error instanceof ApiError && error.status === 501
            ? "Provenance reads the research corpus, a different database from the decision record. Start the dashboard with --corpus."
            : error.message
        }
      />
    );
  }
  if (!data) return <Empty>Nothing to show.</Empty>;
  if (data.available === false) {
    return <Unresolved what="Provenance" reason={data.note ?? "No data."} />;
  }

  return (
    <>
      <Card title="Ticker intervals" flush>
        {data.tickers.length === 0 ? (
          <Empty>No ticker is recorded for this security.</Empty>
        ) : (
          <div className="prov">
            {data.tickers.map((t) => (
              <article className="prov__row" key={`${t.ticker}-${t.first_day}`}>
                <header className="prov__head">
                  <span className="prov__ticker mono">{t.ticker}</span>
                  <span className="prov__dates mono">
                    {t.first_day} → {t.last_day ?? "present"}
                  </span>
                  {t.is_current ? (
                    <Pill tone="accent">current</Pill>
                  ) : (
                    <Pill>ended</Pill>
                  )}
                  <Pill>{t.evidence}</Pill>
                </header>
                {/* In full. An interval without its evidence is an assertion. */}
                {t.citation && <p className="prov__cite">{t.citation}</p>}
              </article>
            ))}
          </div>
        )}
      </Card>

      {/* The read rule that stops a 1990 start being mistaken for a finding. */}
      <p className="prov__note">{data.ticker_note}</p>

      <Card title="Corporate actions" flush>
        {data.action_counts.length === 0 ? (
          <Empty>None recorded.</Empty>
        ) : (
          <TableScroller>
            <table>
              <thead>
                <tr>
                  <th>Kind</th>
                  <th className="r">Count</th>
                  <th>First</th>
                  <th>Last</th>
                </tr>
              </thead>
              <tbody>
                {data.action_counts.map((a) => (
                  <tr key={a.action_type}>
                    <td>{a.action_type.replace(/_/g, " ")}</td>
                    <td className="r">
                      <Figure value={a.count} kind="count" />
                    </td>
                    <td className="mono">{a.first}</td>
                    <td className="mono">{a.last}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroller>
        )}
      </Card>

      <Card
        title="Disputed splits"
        action={<Pill>{data.split_verdicts.length}</Pill>}
        flush
      >
        {data.split_verdicts.length === 0 ? (
          // The empty state says WHICH kind of empty it is. "No split was
          // disputed" and "there was no split to dispute" are different
          // facts, and an unqualified blank reads as a clean bill of health.
          <div className="prov__empty">{data.split_verdict_note}</div>
        ) : (
          <TableScroller>
            <table>
              <thead>
                <tr>
                  <th>Ex-date</th>
                  <th>Verdict</th>
                  <th>Decided by</th>
                  <th className="r">Stored raw</th>
                  <th className="r">Vendor printed</th>
                  <th>Citation</th>
                </tr>
              </thead>
              <tbody>
                {data.split_verdicts.map((v) => (
                  <tr key={v.ex_date}>
                    <td className="mono">{v.ex_date}</td>
                    <td>{v.verdict.replace(/_/g, " ")}</td>
                    <td>{v.decided_by}</td>
                    <td className="r">
                      <Figure value={v.stored_raw_close} kind="money" />
                    </td>
                    <td className="r">
                      <Figure value={v.vendor_printed_close} kind="money" />
                    </td>
                    <td className="mono prov__small">{v.citation ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroller>
        )}
      </Card>

      {data.split_verdicts.length > 0 && (
        <p className="prov__note">{data.split_verdict_note}</p>
      )}
    </>
  );
}
