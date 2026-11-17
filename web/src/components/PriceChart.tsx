import { formatFigure } from "./Figure";
import "./PriceChart.css";

/**
 * A multi-decade price series, with its splits marked.
 *
 * **Log by default, and labelled so.** Apple's adjusted close runs from $0.26
 * in 1990 to $339 in 2026 — a factor of about 1,300. On a linear axis the
 * first twenty years are a flat line on the floor, which is not a chart of
 * the data, it is a chart of the last five years with two decades of noise
 * under it. Log is the convention for a series like this *and* the honest
 * choice, but only while the axis says which it is: a reader who takes a log
 * axis for linear reads a steady climb as an accelerating one.
 *
 * **Splits are marked on both bases**, because on raw they explain the
 * cliffs and on adjusted they explain why there are none. A raw chart with an
 * unexplained 7:1 drop looks like a crash.
 */
export function PriceChart({
  series,
  splits,
  scale,
}: {
  series: readonly { session: string; close: number }[];
  splits: readonly { ex_date: string; ratio: number | null }[];
  scale: "log" | "linear";
}) {
  if (series.length < 2) return null;

  const W = 900;
  const H = 260;
  const PAD_L = 8;
  const PAD_R = 8;
  const PAD_T = 14;
  const PAD_B = 22;

  const closes = series.map((p) => p.close).filter((c) => c > 0);
  if (closes.length < 2) return null;
  const lo = Math.min(...closes);
  const hi = Math.max(...closes);

  const toY = (value: number) => {
    if (scale === "log") {
      const l = Math.log10(Math.max(value, 1e-6));
      const a = Math.log10(Math.max(lo, 1e-6));
      const b = Math.log10(Math.max(hi, 1e-6));
      const span = b - a || 1;
      return H - PAD_B - ((l - a) / span) * (H - PAD_T - PAD_B);
    }
    const span = hi - lo || 1;
    return H - PAD_B - ((value - lo) / span) * (H - PAD_T - PAD_B);
  };

  const first = new Date(series[0]!.session).getTime();
  const last = new Date(series[series.length - 1]!.session).getTime();
  const spanMs = last - first || 1;
  const toX = (iso: string) =>
    PAD_L + ((new Date(iso).getTime() - first) / spanMs) * (W - PAD_L - PAD_R);

  const line = series
    .map((p, i) => `${i ? "L" : "M"}${toX(p.session).toFixed(1)} ${toY(p.close).toFixed(1)}`)
    .join(" ");
  const area = `${line} L${(W - PAD_R).toFixed(1)} ${H - PAD_B} L${PAD_L.toFixed(1)} ${H - PAD_B} Z`;

  // Only splits inside the drawn span; one before the series starts would be
  // drawn on the axis and read as an event at the beginning of history.
  const marks = splits.filter((s) => {
    const t = new Date(s.ex_date).getTime();
    return t >= first && t <= last;
  });

  const ticks = [lo, Math.sqrt(lo * hi), hi];

  return (
    <figure className="pc">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Price from ${series[0]!.session} to ${series[series.length - 1]!.session}, ${formatFigure(lo, "money")} to ${formatFigure(hi, "money")}, ${scale} scale`}
      >
        <defs>
          <linearGradient id="pc-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--series-1)" stopOpacity="0.18" />
            <stop offset="100%" stopColor="var(--series-1)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Recessive grid: three gridlines, no more. */}
        {ticks.map((value) => (
          <line
            key={value}
            x1={PAD_L}
            x2={W - PAD_R}
            y1={toY(value)}
            y2={toY(value)}
            stroke="var(--grid)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ))}

        <path d={area} fill="url(#pc-fill)" />
        <path
          d={line}
          fill="none"
          stroke="var(--series-1)"
          strokeWidth="1.8"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />

        {marks.map((s) => (
          <g key={s.ex_date}>
            <line
              x1={toX(s.ex_date)}
              x2={toX(s.ex_date)}
              y1={PAD_T}
              y2={H - PAD_B}
              stroke="var(--series-2)"
              strokeWidth="1"
              strokeDasharray="2 3"
              opacity="0.7"
              vectorEffect="non-scaling-stroke"
            />
            <title>
              {s.ratio ? `${s.ratio}:1 split` : "split"} on {s.ex_date}
            </title>
          </g>
        ))}
      </svg>

      <figcaption className="pc__axis">
        <span>{series[0]!.session.slice(0, 4)}</span>
        {/* The scale is named on the chart, never only in a control. */}
        <span className="pc__scale">
          {formatFigure(lo, "money")} – {formatFigure(hi, "money")} ·{" "}
          {scale === "log" ? "log scale" : "linear scale"}
          {marks.length > 0 && (
            <>
              {" · "}
              <span className="pc__splitkey">splits marked</span>
            </>
          )}
        </span>
        <span>{series[series.length - 1]!.session.slice(0, 4)}</span>
      </figcaption>
    </figure>
  );
}
