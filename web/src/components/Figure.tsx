/**
 * Every number in the product passes through here.
 *
 * Formatting lives in one place for one reason: a figure formatted two ways
 * in two views is two different claims about the same fact, and the reader
 * has no way to tell which is the real one.
 *
 * The rules each exist because of a specific way the first version of this
 * dashboard misled:
 *
 *  - `money` renders `$4.73T`, never `4730362129360`. A figure that has to be
 *    counted in digits is not read, it is decoded.
 *  - `score` may **never round a non-zero value to zero**. Two-decimal
 *    formatting once turned 77% of 1,956 real scores into `0.00` on screen,
 *    and the page became the evidence that the scorer produced nothing.
 *  - A missing value renders an em dash, never a blank and never `0`.
 */

export type FigureKind =
  | "money"
  | "percent"
  | "multiple"
  | "score"
  | "count"
  | "ratio";

export const EM_DASH = "—";

export function formatFigure(
  value: number | null | undefined,
  kind: FigureKind,
  precision?: number,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EM_DASH;
  }
  switch (kind) {
    case "money":
      return formatMoney(value);
    case "percent":
      return `${(value * 100).toFixed(precision ?? 2)}%`;
    case "multiple":
      return `${value.toFixed(precision ?? 1)}×`;
    case "ratio":
      return value.toFixed(precision ?? 2);
    case "count":
      return value.toLocaleString();
    case "score":
      return formatScore(value);
  }
}

function formatMoney(value: number): string {
  const sign = value < 0 ? "-$" : "$";
  const size = Math.abs(value);
  const scales: [number, string][] = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [cut, suffix] of scales) {
    if (size >= cut) {
      const scaled = size / cut;
      // Three significant figures, so $4.73T and $98.8B carry the same
      // information density. A fixed two decimals gives $98.77B, which reads
      // as more precision than the underlying figure has and costs a glance.
      return `${sign}${scaled.toFixed(scaled < 10 ? 2 : 1)}${suffix}`;
    }
  }
  return `${sign}${size.toFixed(2)}`;
}

/**
 * A non-zero score never renders as zero.
 *
 * Below the point where fixed notation would swallow the value entirely the
 * format switches to exponential rather than losing the magnitude, and an
 * exact zero prints as a bare `0` so it cannot be confused with a small
 * number that was rounded away.
 */
function formatScore(value: number): string {
  if (value === 0) return "0";
  return Math.abs(value) < 0.0005 ? value.toExponential(2) : value.toFixed(4);
}

export function Figure({
  value,
  kind,
  precision,
  className,
}: {
  value: number | null | undefined;
  kind: FigureKind;
  precision?: number;
  className?: string;
}) {
  const text = formatFigure(value, kind, precision);
  const missing = text === EM_DASH;
  return (
    <span
      className={["num", className].filter(Boolean).join(" ")}
      style={missing ? { color: "var(--ink-3)" } : undefined}
      // A screen reader should hear "not recorded", not "em dash".
      aria-label={missing ? "not recorded" : undefined}
    >
      {text}
    </span>
  );
}
