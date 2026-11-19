/**
 * The information architecture, in one place.
 *
 * **Portfolio comes first, because that is what the product is.** CLAUDE.md
 * says the system behaves "like a portfolio manager rather than a stock
 * picker", and that only *"is this a great trade for this portfolio right
 * now"* may authorise a trade. The first version of this rail opened on
 * Securities, which made the product a stock research browser: every area had
 * data and none of them answered the question the system is for.
 *
 * **What is still unpopulated stays honestly named.** Measured 2026-11-17,
 * roughly thirty tables in this schema are declared and hold zero rows in
 * every database — positions, patterns, breakouts, strategies, indicators,
 * regimes, breadth, watchlists. `portfolios` is still among them: the plan is
 * built from a declaration the operator writes, precisely because nothing
 * writes that table, and the plan says so on screen rather than implying a
 * persistence it does not have.
 *
 * So the unpopulated capabilities live under one heading that names what each
 * needs before it can exist, which is the `UNRESOLVED` discipline of the data
 * layer applied to the product surface.
 */

export interface NavItem {
  label: string;
  to: string;
  /** What this view reads, shown when a database is not attached. */
  source?: "record" | "corpus" | "forward";
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    label: "Portfolio",
    items: [{ label: "Today's plan", to: "/plan", source: "corpus" }],
  },
  {
    label: "Research",
    items: [{ label: "Securities", to: "/securities", source: "corpus" }],
  },
  {
    label: "Evidence",
    items: [
      { label: "Strategy runs", to: "/evidence/runs", source: "record" },
      { label: "Walk-forward", to: "/evidence/walkforward", source: "record" },
      { label: "Horizons", to: "/evidence/horizons", source: "record" },
    ],
  },
  {
    label: "Audit",
    items: [{ label: "Decisions", to: "/decisions", source: "record" }],
  },
  {
    label: "System",
    items: [
      { label: "Capture health", to: "/system/capture", source: "forward" },
      { label: "Not yet produced", to: "/not-yet" },
    ],
  },
];
