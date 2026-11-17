import { useEffect, useState } from "react";
import { Caution, Unresolved } from "@/components/Absence";
import { Figure } from "@/components/Figure";
import {
  Empty,
  PageHeader,
  Spinner,
  TableScroller,
} from "@/components/Primitives";
import { getInstruments, getPanel, type InstrumentRow } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import "./HorizonsView.css";

/** Day, Swing, Retirement — fixed order, and nothing is computed across them. */
const ORDER = ["day", "swing", "retirement", "unrecorded"];

export function HorizonsView() {
  const list = useAsync(getInstruments, []);
  const [picked, setPicked] = useState<number | null>(null);

  useEffect(() => {
    if (picked === null && list.data?.instruments.length) {
      setPicked(list.data.instruments[0]!.instrument_id);
    }
  }, [list.data, picked]);

  const panel = useAsync(
    () => (picked === null ? Promise.resolve(null) : getPanel(picked)),
    [picked],
  );

  if (list.loading) return <Spinner />;
  if (list.error)
    return <Unresolved what="Instruments" reason={list.error.message} />;
  if (!list.data?.instruments.length) {
    return <Empty>No securities have recorded scores in this database.</Empty>;
  }

  const meta: InstrumentRow | undefined = list.data.instruments.find(
    (i) => i.instrument_id === picked,
  );
  const names = panel.data
    ? [
        ...ORDER.filter(
          (n) => n in panel.data!.horizons || n in panel.data!.absent,
        ),
        ...Object.keys(panel.data.horizons).filter((n) => !ORDER.includes(n)),
      ]
    : [];

  return (
    <>
      <PageHeader
        title="Horizons"
        subtitle="One security, each horizon as a separate conclusion"
        aside={
          <select
            value={picked ?? ""}
            onChange={(e) => setPicked(Number(e.target.value))}
            aria-label="Security"
          >
            {list.data.instruments.map((i) => (
              <option key={i.instrument_id} value={i.instrument_id}>
                {i.ticker ?? `Instrument ${i.instrument_id}`} — {i.scores} scores
              </option>
            ))}
          </select>
        }
      />

      {/* Never a ticker the record does not hold. A blank reads as "this
          security has no ticker"; the truth is that nobody wrote one down. */}
      {meta?.ticker_note && (
        <Caution title="Ticker unrecorded">{meta.ticker_note}</Caution>
      )}

      {panel.loading && <Spinner />}
      {panel.error && (
        <Unresolved what="This security" reason={panel.error.message} />
      )}

      {panel.data && (
        <>
          <div className="horizons">
            {names.map((name) => {
              const absent = panel.data!.absent[name];
              const rows = panel.data!.horizons[name] ?? [];
              return (
                <div
                  key={name}
                  className={`horizon${absent ? " horizon--absent" : ""}`}
                >
                  <h3 className="horizon__name">{name}</h3>
                  {absent ? (
                    // Absent-with-a-reason, never an empty list. An empty
                    // list reads as "looked and found nothing"; Day was
                    // never looked at and cannot be.
                    <p className="horizon__why">Not scored. {absent}</p>
                  ) : rows.length === 0 ? (
                    <p className="horizon__why">No scores recorded.</p>
                  ) : (
                    <TableScroller>
                      <table>
                        <thead>
                          <tr>
                            <th>Session</th>
                            <th>Dir</th>
                            <th className="r">Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((r, i) => (
                            <tr key={`${r.session_date}-${i}`}>
                              <td className="mono">{r.session_date}</td>
                              <td>{r.direction}</td>
                              <td className="r">
                                <Figure value={r.total} kind="score" />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </TableScroller>
                  )}
                </div>
              );
            })}
          </div>
          <p className="horizons__note">{panel.data.note}</p>
        </>
      )}
    </>
  );
}
