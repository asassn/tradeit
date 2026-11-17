import { formatFigure } from "./Figure";
import "./YieldChart.css";

/**
 * The free cash flow yield, as a shape.
 *
 * This chart exists because a percentile is a number a reader has to trust
 * and a shape is something they can check. "Cheapest it has ever been" and
 * "cheapest since a spike three years ago" are the same percentile and
 * different facts, and only the series distinguishes them.
 *
 * Inline SVG, one series, no library. Per the data-viz method a single series
 * needs no legend — the title names it — and the mark is thin, the grid
 * recessive, the current point ringed against the surface so it reads on top
 * of the line rather than in it.
 */
export function YieldChart({
  series,
  current,
}: {
  series: readonly { session: string; value: number }[];
  current: number;
}) {
  if (series.length < 2) return null;

  const values = series.map((p) => p.value);
  const all = [...values, current];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const span = hi - lo || Math.abs(hi) || 1;

  const W = 560;
  const H = 110;
  const PAD_X = 2;
  const PAD_Y = 14;

  const x = (i: number) =>
    PAD_X + (i / (series.length - 1)) * (W - PAD_X * 2);
  const y = (v: number) =>
    H - PAD_Y - ((v - lo) / span) * (H - PAD_Y * 2);

  const line = series
    .map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`)
    .join(" ");
  const area = `${line} L${x(series.length - 1).toFixed(1)} ${H - PAD_Y} L${x(0).toFixed(1)} ${H - PAD_Y} Z`;
  const cy = y(current);
  const firstYear = series[0]!.session.slice(0, 4);

  return (
    <figure className="yc">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Free cash flow yield from ${firstYear} to today, ranging ${formatFigure(lo, "percent")} to ${formatFigure(hi, "percent")}, now ${formatFigure(current, "percent")}`}
      >
        <defs>
          <linearGradient id="yc-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.2" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#yc-fill)" />
        <path
          d={line}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
          opacity="0.8"
          vectorEffect="non-scaling-stroke"
        />
        {/* Where today sits, carried across the whole history so the eye can
            compare without reading the axis. */}
        <line
          x1={PAD_X}
          x2={W - PAD_X}
          y1={cy}
          y2={cy}
          stroke="var(--accent)"
          strokeWidth="1"
          strokeDasharray="3 3"
          opacity="0.5"
          vectorEffect="non-scaling-stroke"
        />
        <circle
          cx={x(series.length - 1)}
          cy={cy}
          r="4.5"
          fill="var(--accent)"
          stroke="var(--surface)"
          strokeWidth="2"
        />
      </svg>
      <figcaption className="yc__axis">
        <span>{firstYear}</span>
        <span className="yc__range">
          {formatFigure(lo, "percent")} – {formatFigure(hi, "percent")}
        </span>
        <span>today</span>
      </figcaption>
    </figure>
  );
}
