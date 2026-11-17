import { NotYetProduced } from "@/components/Absence";
import { PageHeader } from "@/components/Primitives";

/**
 * The capabilities that exist in code and have never produced a row.
 *
 * Measured 2026-11-17 across all five databases. This view is not an
 * apology and it is certainly not a roadmap: it is the `UNRESOLVED`
 * discipline applied to the product surface. Each card names what the
 * capability *is*, and what it needs before it can have a screen.
 *
 * The alternative — building the screens and filling them with sample data —
 * is the thing this project refuses everywhere else.
 */

const CAPABILITIES = [
  {
    capability: "Patterns",
    built:
      "Twelve causal chart-pattern families with a six-state lifecycle, append-only history, relationship taxonomy and a scanner (src/tradeit/patterns/, 11,013 lines).",
    needs:
      "A scan run against research-01. The only pattern rows that exist are in the frozen full-01 corpus — 78 instruments, survivor-biased, citable for machinery behaviour and never for how often anything works.",
  },
  {
    capability: "Breakouts",
    built:
      "Thirteen states, four separate scores per event, three confirmation paths under three evidence profiles (src/tradeit/breakouts/, 9,746 lines).",
    needs:
      "Patterns first — a breakout is measured against a pattern's boundary, and no structural boundary exists outside full-01.",
  },
  {
    capability: "Portfolios, positions and risk",
    built:
      "Three mandates, risk-based sizing that names its binding constraint, a ratcheting stop ladder, a diversity-aware ranker (src/tradeit/portfolio/, src/tradeit/risk/).",
    needs:
      "A portfolio that has been run. portfolios, positions, trades, portfolio_snapshots and risk_snapshots are all empty, so there is no equity curve, position list or heat series anywhere on disk.",
  },
  {
    capability: "Strategies",
    built:
      "A versioned, digest-identified StrategyConfig with ~22 sections, a nine-state lifecycle, validation against the real detector registry, and corpus admissibility (src/tradeit/strategy/).",
    needs:
      "Composition from the capability registries and persistence of versions — both named as owed in the roadmap. No strategy has ever been authored or stored.",
  },
  {
    capability: "Screener",
    built:
      "A snapshot scanner that reports instruments with no detections, resumed instruments, skipped detectors and screen rejections with reasons (src/tradeit/scanning/).",
    needs:
      "A read model. A single-security valuation takes ~3.5 s against a 73 GB corpus; a screener cannot scan it per request.",
  },
  {
    capability: "Market analytics",
    built:
      "~58 causal indicators, a transparent regime classifier, breadth, sector strength, multi-benchmark relative strength and a volatility model (src/tradeit/analytics/, 5,369 lines).",
    needs:
      "A computed run. indicator_values, market_regime_states, market_breadth_snapshots, sector_strength, relative_strength_values and volatility_regime_states are all empty.",
  },
  {
    capability: "Day horizon",
    built:
      "The mandate is defined and declines to be scored rather than falling back to Daily bars.",
    needs:
      "The intraday-01 corpus, which is deferred by owner decision (ADR-0030) because it needs paid intraday data and no further data spend is authorised. This one is a decision, not a gap.",
  },
];

export function NotYetView() {
  return (
    <>
      <PageHeader
        title="Not yet produced"
        subtitle="Capabilities that exist in code and have never written a row. Each names what it needs, rather than showing a screen over nothing."
      />
      {CAPABILITIES.map((c) => (
        <NotYetProduced key={c.capability} {...c} />
      ))}
    </>
  );
}
