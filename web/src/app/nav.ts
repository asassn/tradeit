/**
 * The information architecture, in one place.
 *
 * **Four areas that have data, and one that honestly reports what does not.**
 * Measured 2026-11-17: roughly thirty tables in this schema are declared and
 * hold zero rows in every database — portfolios, positions, patterns,
 * breakouts, strategies, indicators, regimes, breadth, watchlists, risk. A
 * Portfolios menu would be furniture over nothing.
 *
 * So the unpopulated capabilities live under one honest heading that names
 * what each needs before it can exist, which is the `UNRESOLVED` discipline
 * of the data layer applied to the product surface.
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
    label: "Research",
    items: [{ label: "Securities", to: "/securities", source: "corpus" }],
  },
  {
    label: "Evidence",
    items: [
      { label: "Strategy runs", to: "/evidence/runs", source: "record" },
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
