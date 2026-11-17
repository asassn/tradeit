import { useState } from "react";
import { Caution, Unresolved } from "@/components/Absence";
import { Figure, formatFigure } from "@/components/Figure";
import {
  Card,
  Empty,
  Spinner,
  Stat,
  StatRow,
} from "@/components/Primitives";
import { PriceChart } from "@/components/PriceChart";
import { ApiError, getPrices } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import "./OverviewTab.css";

type Basis = "raw" | "total";
type Scale = "log" | "linear";

/**
 * The price history, on the basis the reader chooses.
 *
 * **Neither basis is the default answer to every question**, which is why
 * both are offered with the difference stated rather than one being picked
 * silently. Raw is what the market printed — it is what a multiple needs, and
 * it jumps at every split. Adjusted is comparable across time and is **not
 * point-in-time**: the adjustment embeds every corporate action since, so an
 * old close is a number computed today rather than one anybody saw.
 *
 * On Apple the contrast is the whole lesson: raw runs $37 in 1990 to a $702
 * peak in 2012; adjusted runs $0.26 to $340. Same company, four splits, a
 * factor of 112 between the two pictures.
 */
export function OverviewTab({ securityId }: { securityId: string }) {
  const [basis, setBasis] = useState<Basis>("raw");
  const [scale, setScale] = useState<Scale>("log");
  const { data, error, loading } = useAsync(
    () => getPrices(securityId, basis),
    [securityId, basis],
  );

  if (loading) return <Spinner />;
  if (error) {
    return (
      <Unresolved
        what="Price history"
        reason={
          error instanceof ApiError && error.status === 501
            ? "Prices read the research corpus, a different database from the decision record. Start the dashboard with --corpus."
            : error.message
        }
      />
    );
  }
  if (!data) return <Empty>Nothing to show.</Empty>;
  if (!data.available) {
    return <Unresolved what="Price history" reason={data.note ?? "No data."} />;
  }

  const series = data.series ?? [];
  const splits = data.splits ?? [];

  return (
    <>
      <Card
        title="Price history"
        action={
          <div className="ov__controls">
            <div className="ov__seg" role="group" aria-label="Price basis">
              {(["raw", "total"] as const).map((b) => (
                <button
                  key={b}
                  type="button"
                  className={basis === b ? "is-on" : ""}
                  aria-pressed={basis === b}
                  onClick={() => setBasis(b)}
                >
                  {b === "raw" ? "As printed" : "Adjusted"}
                </button>
              ))}
            </div>
            <div className="ov__seg" role="group" aria-label="Axis scale">
              {(["log", "linear"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  className={scale === s ? "is-on" : ""}
                  aria-pressed={scale === s}
                  onClick={() => setScale(s)}
                >
                  {s === "log" ? "Log" : "Linear"}
                </button>
              ))}
            </div>
          </div>
        }
      >
        <PriceChart series={series} splits={splits} scale={scale} />
        {/* Which basis this is, in words, every time. A chart that silently
            picked one would teach the reader the wrong thing about their own
            data. */}
        <p className="ov__basis">{data.basis_note}</p>
      </Card>

      <StatRow>
        <Stat
          label="Sessions"
          value={<Figure value={data.sessions} kind="count" />}
          context={`${data.first_session} → ${data.last_session}`}
        />
        <Stat
          label="High"
          value={<Figure value={data.high} kind="money" />}
          context={
            data.peak_close
              ? `Highest close ${formatFigure(data.peak_close.close, "money")} on ${data.peak_close.session}`
              : undefined
          }
        />
        <Stat label="Low" value={<Figure value={data.low} kind="money" />} />
        <Stat
          label="Splits"
          value={<Figure value={splits.length} kind="count" />}
          context={
            splits.length
              ? splits.map((s) => `${s.ratio ?? "?"}:1 ${s.ex_date.slice(0, 4)}`).join(" · ")
              : "None recorded"
          }
        />
      </StatRow>

      {/* The sample is named, and the extremes above are not sampled. */}
      {data.sampled && (
        <Caution title="The line is sampled; the figures are not">
          {data.sampling_note}
        </Caution>
      )}
    </>
  );
}
