import { useEffect, useState } from "react";
import { MissingRecord, Unresolved } from "@/components/Absence";
import { Figure } from "@/components/Figure";
import {
  Card,
  Empty,
  PageHeader,
  Pill,
  Spinner,
  TableScroller,
} from "@/components/Primitives";
import { getSession, getSessions } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";

export function DecisionsView() {
  const list = useAsync(getSessions, []);
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    if (!picked && list.data?.sessions.length) {
      setPicked(list.data.sessions[0]!.session_date);
    }
  }, [list.data, picked]);

  const detail = useAsync(
    () => (picked ? getSession(picked) : Promise.resolve(null)),
    [picked],
  );

  if (list.loading) return <Spinner />;
  if (list.error) return <Unresolved what="Sessions" reason={list.error.message} />;
  if (!list.data?.sessions.length) {
    return <Empty>No sessions recorded in this database.</Empty>;
  }

  const session = detail.data;

  return (
    <>
      <PageHeader
        title="Decisions"
        subtitle="One session, walked from score to execution"
        aside={
          <select
            value={picked ?? ""}
            onChange={(e) => setPicked(e.target.value)}
            aria-label="Session"
          >
            {list.data.sessions.map((s) => (
              <option key={s.session_date} value={s.session_date}>
                {s.session_date} — {s.scores} scores
              </option>
            ))}
          </select>
        }
      />

      {detail.loading && <Spinner />}
      {detail.error && (
        <Unresolved what="This session" reason={detail.error.message} />
      )}

      {session && (
        <>
          {/* ABOVE the results, always. A session with empty lists and no
              `missing` was quiet; one with empty lists AND `missing` was
              never written down, and a reader scanning zero rows would
              otherwise conclude the system did nothing that day. */}
          <MissingRecord missing={session.missing} />

          <Card title="Scores" action={<Pill>{session.scores.length}</Pill>} flush>
            {session.scores.length === 0 ? (
              <Empty>None recorded.</Empty>
            ) : (
              <TableScroller>
                <table>
                  <thead>
                    <tr>
                      <th>Security</th>
                      <th>Direction</th>
                      <th className="r">Total</th>
                      <th className="r">Rank</th>
                      <th>Components</th>
                    </tr>
                  </thead>
                  <tbody>
                    {session.scores.slice(0, 50).map((s, i) => (
                      <tr key={`${s.instrument_id}-${i}`}>
                        {/* instrument_id, not a ticker: the decision records
                            hold zero symbol mappings, and instrument_id is a
                            different id space from the corpus's security_id.
                            Nothing silently converts between them. */}
                        <td className="mono">#{s.instrument_id}</td>
                        <td>{s.direction}</td>
                        <td className="r">
                          <Figure value={s.total} kind="score" />
                        </td>
                        <td className="r">
                          <Figure value={s.rank} kind="count" />
                        </td>
                        {/* The breakdown always travels with the total: a
                            total alone is the rationalisation the score's own
                            reconciliation check exists to prevent. */}
                        <td className="mono" style={{ font: "var(--t-caption)" }}>
                          {Object.entries(s.components)
                            .map(([k, v]) => `${k} ${v.toPrecision(3)}`)
                            .join(" · ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroller>
            )}
          </Card>

          <Card title="Orders" action={<Pill>{session.orders.length}</Pill>} flush>
            {session.orders.length === 0 ? (
              <Empty>None recorded.</Empty>
            ) : (
              <TableScroller>
                <table>
                  <thead>
                    <tr>
                      <th>Security</th>
                      <th>Side</th>
                      <th className="r">Quantity</th>
                      <th>Status</th>
                      <th>Intent</th>
                    </tr>
                  </thead>
                  <tbody>
                    {session.orders.map((o) => (
                      <tr key={o.client_order_id}>
                        <td className="mono">#{o.instrument_id}</td>
                        <td>{o.side}</td>
                        <td className="r">
                          <Figure value={Number(o.quantity)} kind="count" />
                        </td>
                        <td>{o.status}</td>
                        <td>{o.intent ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroller>
            )}
          </Card>

          <Card
            title="Executions"
            action={<Pill>{session.executions.length}</Pill>}
            flush
          >
            {session.executions.length === 0 ? (
              <Empty>None recorded.</Empty>
            ) : (
              <TableScroller>
                <table>
                  <thead>
                    <tr>
                      <th>Security</th>
                      <th className="r">Quantity</th>
                      <th className="r">Price</th>
                      <th className="r">Commission</th>
                      <th className="r">Slippage</th>
                      <th>Venue</th>
                    </tr>
                  </thead>
                  <tbody>
                    {session.executions.map((x, i) => (
                      <tr key={`${x.order_id}-${i}`}>
                        <td className="mono">#{x.instrument_id}</td>
                        <td className="r">
                          <Figure value={Number(x.quantity)} kind="count" />
                        </td>
                        <td className="r">
                          <Figure value={Number(x.price)} kind="money" />
                        </td>
                        <td className="r">
                          <Figure value={Number(x.commission)} kind="money" />
                        </td>
                        <td className="r">
                          <Figure value={Number(x.slippage)} kind="money" />
                        </td>
                        <td>{x.venue}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroller>
            )}
          </Card>

          <Card title="Journal" action={<Pill>{session.journal.length}</Pill>} flush>
            {session.journal.length === 0 ? (
              <Empty>None recorded.</Empty>
            ) : (
              <TableScroller>
                <table>
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>Summary</th>
                      <th>Rationale</th>
                    </tr>
                  </thead>
                  <tbody>
                    {session.journal.map((j, i) => (
                      <tr key={i}>
                        <td>{j.entry_type}</td>
                        <td>{j.summary}</td>
                        <td>{j.rationale ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroller>
            )}
          </Card>
        </>
      )}
    </>
  );
}
