/**
 * Absence is primary content in this product, not an error state.
 *
 * Reading the whole codebase makes one thing plain: almost every dataclass in
 * it carries an absence field beside its value field — `missing`,
 * `unresolved`, `caveats`, `refused`, `skipped`, `blocked`, `remedy`,
 * `minimum_detectable_shortfall`, `is_our_defect` — and several are
 * explicitly ordered *before* the results they accompany. The validation
 * harness reports blocked checks above passed ones. `reconstruct` returns
 * `missing` beside the chain. `UNRESOLVED` is a first-class answer.
 *
 * **A UI that renders these as errors, footnotes or empty cells inverts the
 * system's design intent.** So they get components of their own, with the
 * same visual weight as a result, and this file is where that is decided
 * once rather than remembered in thirty views.
 */

import type { ReactNode } from "react";
import "./Absence.css";

/**
 * Why a value could not be computed — never a blank cell.
 *
 * A blank reads as "zero" or "not applicable". The truth is almost always
 * something more useful: this company loses money, this balance is stale,
 * two tickers claim this security. That sentence is the deliverable.
 */
export function Unresolved({
  what,
  reason,
}: {
  what: string;
  reason: string;
}) {
  return (
    <div className="absence absence--flat" role="note">
      <div className="absence__title">{what} not computed</div>
      <p className="absence__body">{reason}</p>
    </div>
  );
}

/**
 * The record is silent — rendered ABOVE the results it qualifies.
 *
 * This is the load-bearing distinction in the observability design: a session
 * with empty lists and no `missing` was quiet, and a session with empty lists
 * *and* `missing` was never written down. The second is the dangerous reading,
 * because a reader scanning zero rows concludes the system did nothing that
 * day rather than that nobody recorded what it did.
 *
 * `complete` is deliberately not a health check — a quiet session is
 * legitimately incomplete.
 */
export function MissingRecord({ missing }: { missing: readonly string[] }) {
  if (missing.length === 0) {
    return (
      <div className="absence absence--ok" role="note">
        <div className="absence__title">Walkable end to end</div>
        <p className="absence__body">
          Every link from score to execution is recorded.
        </p>
      </div>
    );
  }
  return (
    <div className="absence absence--critical" role="alert">
      <div className="absence__title">This session cannot be walked end to end</div>
      <p className="absence__body">
        No record of: {missing.join(", ")}. Empty lists below mean nobody wrote
        the record, not that nothing happened.
      </p>
    </div>
  );
}

/**
 * A capability that exists in code and has never produced a row.
 *
 * Roughly thirty tables in this schema are declared and empty — patterns,
 * breakouts, portfolios, positions, risk snapshots, strategies, indicators,
 * regimes, breadth. The honest surface for those is not a coming-soon banner
 * and certainly not sample data: it is a statement of **what the capability
 * needs before it can exist**, which is a fact the reader can act on.
 */
export function NotYetProduced({
  capability,
  built,
  needs,
}: {
  capability: string;
  built: string;
  needs: string;
}) {
  return (
    <div className="absence absence--flat absence--block" role="note">
      <div className="absence__title">{capability} — nothing recorded yet</div>
      <p className="absence__body">
        <strong>Built:</strong> {built}
      </p>
      <p className="absence__body">
        <strong>Needs:</strong> {needs}
      </p>
    </div>
  );
}

/**
 * A warning about a result that did compute — a value trap, a stale input, a
 * figure that may be understated.
 *
 * Distinct from {@link Unresolved} on purpose: "we could not work it out" and
 * "we worked it out and you should be careful" call for different actions.
 */
export function Caution({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="absence absence--warning" role="note">
      <div className="absence__title">{title}</div>
      <p className="absence__body">{children}</p>
    </div>
  );
}

/**
 * The arithmetic behind every figure on the page.
 *
 * Folded, never removed. The roadmap's condition is that a valuation is
 * "never shipped without the reasoning that produced it", and a disclosure
 * one click away satisfies that while putting the arithmetic in front of the
 * number does not — the first version of this dashboard was unreadable for
 * exactly that reason.
 */
export function Provenance({
  lines,
  summary = "Show the working",
}: {
  lines: readonly string[];
  summary?: string;
}) {
  if (lines.length === 0) return null;
  return (
    <details className="provenance">
      <summary>
        {summary} <span className="provenance__count">{lines.length}</span>
      </summary>
      <ul>
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </details>
  );
}
