import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Plan, PlanHorizon, PlanPayload } from "@/lib/api";
import { PlanView } from "../PlanView";

/**
 * The home screen, and the properties that make it trustworthy rather than
 * merely useful.
 *
 * A page showing only what a system **would buy** looks identical whether its
 * limits are working or absent. So the tests here are mostly about what the
 * screen refuses to hide: every limit that bound a candidate, the standing it
 * would have had, the controls that cannot fire at all, and a horizon that
 * cannot be planned keeping its reason instead of vanishing.
 */

function plan(overrides: Partial<Plan> = {}): Plan {
  return {
    mandate: "swing",
    timeframe: "1d",
    session_date: "2015-11-27",
    equity: 100000,
    cash: 40000,
    heat: 0.1545,
    position_count: 2,
    candidates_considered: 3,
    capital_deployed: 0,
    holdings: [],
    would_buy: [],
    declined: [],
    would_sell: [],
    refused: [],
    not_armed: [],
    caveats: ["not a recommendation"],
    ...overrides,
  };
}

function horizon(overrides: Partial<PlanHorizon> = {}): PlanHorizon {
  return {
    mandate: "swing",
    available: true,
    declared: {
      mandate: "swing",
      equity: 100000,
      cash: 40000,
      universe_size: 14,
      warmup_from: "2014-01-02",
      as_of: "2015-11-27",
      holdings_declared: 2,
      score_range: { lower: 0, upper: 0.5 },
      fast: 50,
      slow: 200,
      stop_pct: 0.08,
      note: "",
    },
    loaded: {
      unresolved: [],
      carried: [],
      correlation_pairs: 351,
      correlation_over_limit: 8,
      sectors_classified: 14,
      sessions_loaded: 481,
      bars_loaded: 6253,
      seconds: 0.56,
    },
    plan: plan(),
    ...overrides,
  };
}

/**
 * A horizon that could not be planned.
 *
 * `plan` and `loaded` are **absent**, not undefined: `exactOptionalPropertyTypes`
 * is on, and the server omits the keys rather than sending nulls. Building the
 * fixture by deletion keeps it the shape the route actually returns.
 */
function unplannable(mandate: string, note: string): PlanHorizon {
  const base = horizon({ mandate, available: false, note });
  delete base.plan;
  delete base.loaded;
  return base;
}

function serve(payload: Partial<PlanPayload>) {
  const body: PlanPayload = {
    available: true,
    declaration: "portfolios.json",
    generated_at: "2026-11-19T06:00:00Z",
    portfolios: [horizon()],
    ...payload,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response),
    ),
  );
  return render(
    <MemoryRouter>
      <PlanView />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a refusal names every limit that bound it", () => {
  it("renders both vetoes, not just the first", async () => {
    // One veto per candidate would make a second binding limit look slack
    // when it is also binding — which is how a limit gets relaxed on the
    // strength of a measurement that never applied to it.
    serve({
      portfolios: [
        horizon({
          plan: plan({
            refused: [
              {
                instrument_id: 9,
                reference: "TXN",
                reasons: ["sector_limit_reached", "heat_limit_exceeded"],
                ranker: "opportunity_fit",
                standing: 1,
                detail: "fit 0.3455, total 0.1423, sector Manufacturing",
              },
            ],
          }),
        }),
      ],
    });

    await waitFor(() => expect(screen.getByText("TXN")).toBeInTheDocument());
    expect(screen.getByText("sector limit reached")).toBeInTheDocument();
    expect(screen.getByText("heat limit exceeded")).toBeInTheDocument();
  });

  it("states what the limit cost by showing where it would have ranked", async () => {
    serve({
      portfolios: [
        horizon({
          plan: plan({
            refused: [
              {
                instrument_id: 9,
                reference: "TXN",
                reasons: ["sector_limit_reached"],
                ranker: "opportunity_fit",
                standing: 1,
                detail: "fit 0.3455",
              },
            ],
          }),
        }),
      ],
    });

    await waitFor(() =>
      expect(screen.getByText(/would have ranked 1/)).toBeInTheDocument(),
    );
  });

  it("says no candidate hit a limit rather than leaving the section blank", async () => {
    // An empty section and a section that was never populated read the same.
    serve({});

    await waitFor(() =>
      expect(screen.getByText(/No candidate hit an absolute limit/)).toBeInTheDocument(),
    );
  });
});

describe("absence is rendered", () => {
  it("lists the controls that cannot fire, and says why that is shown", async () => {
    serve({
      portfolios: [
        horizon({
          plan: plan({
            not_armed: [
              { name: "trailing_stop", reason: "no ATR in any stop context" },
              { name: "CorrelationRule", reason: "not installed" },
            ],
          }),
        }),
      ],
    });

    await waitFor(() => expect(screen.getByText("trailing_stop")).toBeInTheDocument());
    expect(screen.getByText("CorrelationRule")).toBeInTheDocument();
    expect(screen.getByText(/cannot fire/)).toBeInTheDocument();
  });

  it("keeps a horizon that cannot be planned, carrying its reason", async () => {
    // A client receiving two horizons instead of three could not tell a
    // declining Day from a Day nobody declared.
    serve({
      portfolios: [
        horizon(),
        unplannable("day", "day declines to be scored: its primary corpus is intraday-01"),
      ],
    });

    await waitFor(() => expect(screen.getByText("Day")).toBeInTheDocument());
    expect(screen.getByText(/intraday-01/)).toBeInTheDocument();
  });

  it("reports a universe smaller than the one declared", async () => {
    serve({
      portfolios: [
        horizon({
          loaded: {
            unresolved: ["BAC", "JPM"],
            carried: [],
            correlation_pairs: 351,
            correlation_over_limit: 8,
            sectors_classified: 12,
            sessions_loaded: 481,
            bars_loaded: 6253,
            seconds: 0.5,
          },
        }),
      ],
    });

    await waitFor(() => expect(screen.getByText(/BAC, JPM/)).toBeInTheDocument());
  });

  it("explains an empty buy list differently when nothing signalled", async () => {
    // Zero buys from zero candidates and zero buys from three are different
    // facts, and only one of them is about the limits.
    const { unmount } = serve({
      portfolios: [horizon({ plan: plan({ candidates_considered: 0 }) })],
    });
    await waitFor(() =>
      expect(screen.getByText(/No candidate signalled/)).toBeInTheDocument(),
    );
    unmount();
    vi.unstubAllGlobals();

    serve({ portfolios: [horizon({ plan: plan({ candidates_considered: 3 }) })] });
    await waitFor(() =>
      expect(screen.getByText(/refused or declined/)).toBeInTheDocument(),
    );
  });
});

describe("the two refusal mechanisms stay apart", () => {
  it("shows a ranker veto and a risk decline in separate sections", async () => {
    // A refusal came from the ranker before sizing; a decline from the risk
    // engine after it. One list would lose which control acted.
    serve({
      portfolios: [
        horizon({
          plan: plan({
            refused: [
              {
                instrument_id: 9,
                reference: "TXN",
                reasons: ["sector_limit_reached"],
                ranker: "opportunity_fit",
                standing: 1,
                detail: "",
              },
            ],
            declined: [
              {
                instrument_id: 11,
                reference: "AMD",
                quantity: 0,
                entry_price: 10,
                stop_price: 9,
                notional: 0,
                risk_amount: 0,
                risk_fraction: 0,
                binding_constraint: "portfolio_heat_budget",
                weight: 0,
                standalone: 0.4,
                accepted: false,
                declined_by: "portfolio_heat",
                detail: "heat budget exhausted",
              },
            ],
          }),
        }),
      ],
    });

    await waitFor(() => expect(screen.getByText("Refused · 1")).toBeInTheDocument());
    expect(screen.getByText("Declined by risk · 1")).toBeInTheDocument();
    expect(screen.getByText("TXN")).toBeInTheDocument();
    expect(screen.getByText("AMD")).toBeInTheDocument();
  });
});

describe("an entry says what sized it", () => {
  it("names the binding constraint beside the quantity", async () => {
    serve({
      portfolios: [
        horizon({
          plan: plan({
            would_buy: [
              {
                instrument_id: 9,
                reference: "TXN",
                quantity: 108,
                entry_price: 57.55,
                stop_price: 52.95,
                notional: 6215.4,
                risk_amount: 497.23,
                risk_fraction: 0.005,
                binding_constraint: "risk_per_trade",
                weight: 0.0622,
                standalone: 0.0068,
                accepted: true,
                declined_by: null,
                detail: "",
              },
            ],
          }),
        }),
      ],
    });

    await waitFor(() => expect(screen.getByText("TXN")).toBeInTheDocument());
    // "We bought less than we wanted" and "we bought what we wanted" differ.
    expect(screen.getByText("risk per trade")).toBeInTheDocument();
    expect(screen.getByText("0.50%")).toBeInTheDocument();
  });
});

describe("heat explains the rest of the column", () => {
  it("says a heat above its limit is why entries are refused", async () => {
    serve({ portfolios: [horizon({ plan: plan({ heat: 0.1545 }) })] });

    await waitFor(() =>
      expect(screen.getByText(/over the 6% limit/)).toBeInTheDocument(),
    );
  });

  it("does not claim a limit is breached when it is not", async () => {
    serve({ portfolios: [horizon({ plan: plan({ heat: 0.01 }) })] });

    await waitFor(() => expect(screen.getByText("1.00%")).toBeInTheDocument());
    expect(screen.queryByText(/over the 6% limit/)).toBeNull();
  });
});

describe("the plan states its own provenance", () => {
  it("carries the caveats beside the numbers", async () => {
    serve({});

    await waitFor(() =>
      expect(screen.getByText(/What this plan is worth/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/How this plan was made/)).toBeInTheDocument();
  });

  it("reports a refused declaration instead of an empty plan", async () => {
    serve({
      available: false,
      note: "declaration refused: mandate swing: stop_price is required",
      portfolios: [],
    });

    await waitFor(() => expect(screen.getByText(/stop_price is required/)).toBeInTheDocument());
  });
});

describe("what the portfolio holds", () => {
  it("shows each position with the risk that portfolio heat sums", async () => {
    // `open_risk` is what is lost if the stop is hit, and heat is the sum of
    // it. Without the column the heat figure has no visible derivation, and a
    // refusal caused by heat cannot be traced to the positions causing it.
    serve({
      portfolios: [
        horizon({
          plan: plan({
            holdings: [
              {
                instrument_id: 7,
                reference: "IBM",
                quantity: 400,
                average_entry_price: 138.46,
                last_price: 138.46,
                stop_price: 110,
                market_value: 55384,
                open_risk: 11384,
                weight: 0.5538,
              },
            ],
          }),
        }),
      ],
    });

    await waitFor(() => expect(screen.getByText("Holds · 1")).toBeInTheDocument());
    expect(screen.getByText("IBM")).toBeInTheDocument();
    expect(screen.getByText("$11.4K")).toBeInTheDocument();
    expect(screen.getByText("55.4%")).toBeInTheDocument();
  });

  it("says the portfolio is flat rather than leaving the section blank", async () => {
    serve({ portfolios: [horizon({ plan: plan({ holdings: [] }) })] });

    await waitFor(() => expect(screen.getByText(/Flat — no open position/)).toBeInTheDocument());
  });

  it("renders an absent stop as not-recorded, never as zero", async () => {
    // Zero is a stop at zero, which is unlimited risk. They must not look the
    // same on screen.
    serve({
      portfolios: [
        horizon({
          plan: plan({
            holdings: [
              {
                instrument_id: 7,
                reference: "IBM",
                quantity: 400,
                average_entry_price: 138.46,
                last_price: 138.46,
                stop_price: null,
                market_value: 55384,
                open_risk: 0,
                weight: 0.5538,
              },
            ],
          }),
        }),
      ],
    });

    await waitFor(() => expect(screen.getByText("IBM")).toBeInTheDocument());
    expect(screen.getAllByLabelText("not recorded").length).toBeGreaterThan(0);
  });
});


describe("a holding that did not print is carried, not refused", () => {
  it("says which position is marked at an older close, and what that affects", async () => {
    // The engine's rule and its reasoning: a security that did not print today
    // is carried at its last known price, which "is not a guess, it is the last
    // thing anybody knew, and being unchanged from yesterday it cannot trigger
    // a stop that yesterday did not". Refusing the whole horizon over one quiet
    // name — which the plan did until 2026-11-19 — stalls every other stop.
    serve({
      portfolios: [
        horizon({
          loaded: {
            unresolved: [],
            carried: [{ reference: "NVDA", session: "2026-08-12" }],
            correlation_pairs: 351,
            correlation_over_limit: 8,
            sectors_classified: 14,
            sessions_loaded: 489,
            bars_loaded: 16137,
            seconds: 1.2,
          },
        }),
      ],
    });

    await waitFor(() =>
      expect(screen.getByText(/Marked at an older close/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/NVDA \(2026-08-12\)/)).toBeInTheDocument();
    // The consequence, not just the fact: heat is measured against that mark.
    expect(screen.getByText(/heat, is measured against it/)).toBeInTheDocument();
  });

  it("says nothing when every holding printed", async () => {
    serve({});

    await waitFor(() => expect(screen.getByText("Swing")).toBeInTheDocument());
    expect(screen.queryByText(/Marked at an older close/)).toBeNull();
  });
});
