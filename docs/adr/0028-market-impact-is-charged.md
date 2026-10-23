# ADR-0028: Market impact is charged, so order size costs something

**Status:** accepted
**Date:** 2026-10-23
**Supersedes:** nothing. **Amends:** nothing. Records a change to a backtesting
assumption authorised by the owner on 2026-10-23, as the scoped proposition the
standing rule on cost assumptions requires.

## Context

`ParticipationCostModel` charges commission, half the spread and a slippage
allowance, and computes market impact as:

```
impact = notional x market_impact_coefficient x (notional / dollar_volume)
```

It computes impact **only when handed a dollar volume**, and three call sites
never passed one — `engine._settle`, `PaperBroker._fill_resting`, and
`scripts/paper_session.py`. The coefficient has been configured at `0.1`
throughout, and was unreachable. Measured in
`COST_MODEL_IMPACT_DECISION_2026-10-19.md`:

| participation | impact | total per side | multiple |
|---|---|---|---|
| — *(as it stood)* | 0 bps | **6.5 bps** | 1.0× |
| 1.0% | 10.0 bps | 16.5 bps | 2.5× |
| **2.0%** *(the configured cap)* | **20.0 bps** | **26.5 bps** | **4.1×** |

So every result on `SIGNAL_SCOREBOARD.md` priced orders with **no size penalty
at all**, at roughly a quarter of the trading cost the cost model was built to
charge. The liquidity cap in `RiskBasedSizer` worked correctly the whole time —
the sizer saw participation and the cost model did not, which is why nothing
looked wrong.

`execution/base.py` had already written down why this matters:

> A strategy that trades often can be profitable at 2 basis points of slippage
> and unprofitable at 15, and the difference between those two numbers is
> entirely a modelling assumption.

## Decision

**Pass the dollar volume, so impact is charged.** One argument at each of the
three call sites, using the **reference bar** — the bar that sets the fill
price — because `notional` is computed from `reference_bar.close` and measuring
participation against a different bar would mix two sessions into one ratio.

`close * volume` is one bar's dollar volume, which is what the parameter means
here despite being named `average_dollar_volume`. That is already the
convention in `baselines.py` and `factor.py`, where the sizer's own cap is
applied, and the two **must** agree or the sizer and the cost model would
disagree about the same order.

**The paper venue is charged too, not only the backtester.** A strategy
promoted on paper fills that paid no size penalty would be promoted on exactly
the flattered number the backtest no longer uses. A paper venue cheaper than
the backtest it exists to validate is worse than no paper venue.

## Alternatives rejected

**Leave it off and caveat every number.** This was the honest fallback, and the
decision document said so: every future result would carry the caveat the
archive now does. Rejected because the caveat is unenforceable — it lives in
prose and the numbers travel without it — and because the window where
switching it on is cheap is exactly now, before anything is built on top.

**Measure the coefficient first, then switch on.** `0.1` is an inherited
default nobody has validated against this corpus, so this replaces a
known-wrong assumption (zero) with an unverified one. Rejected as a reason to
wait: being wrong in a more realistic direction is strictly better than being
wrong in the flattering one, and the measurement needs realised paper fills
that do not exist yet. **Measuring the coefficient against realised slippage
remains open and is not settled by this ADR.**

**Charge a flat 20 bps instead of a participation-scaled term.** Simpler, and
wrong: it would overcharge a small order in a liquid name and undercharge the
concentrated position in a thin one, which is the case the term exists for.

## Consequences

**Every archived result is now incomparable to anything run afterwards.**
§46 reproduced exactly a month later; that property ends here. Results are not
retracted — they were correct under the assumption stated — but a comparison
across this boundary is invalid and the scoreboard says so.

**The direction of the error is known and favourable.** A result that survives
higher costs is more trustworthy, never less. A strategy that stops clearing
the hurdle under realistic costs was never going to clear it in the market.

**Re-running is deliberately not automatic.** Track B's pattern program and the
combination line are closed, no validated strategy exists, and no live capital
is deployed, so nothing in the archive is justifying a trade. What is
load-bearing gets re-run when it is next relied upon, under the hurdle in force
then.

**A known residual under-charge stays.** Impact is zero when a bar's volume is
zero, and 6.339% of raw corpus bars carry volume 0 — of which 86.5% are flat
copies of the previous close (`RESEARCH_01_DATA_DICTIONARY.md`, and §0.7: a bar
can have a price and no trade behind it). Those are exactly the illiquid bars
where impact would be largest, so the cost model is still optimistic there.
Not fixed here because refusing to fill on a zero-volume bar is a change to
fill behaviour rather than to cost, and it needs its own proposition.

## Tests

- `test_the_engine_charges_market_impact_on_an_entry` — the entry price moves
  when the coefficient is zeroed, and the zero-impact price is exactly 6.5 bps
  above the reference, which pins the figure this decision was made on.
- `test_the_venue_charges_market_impact_and_it_scales_with_size` — impact is
  non-zero, **quadratic in quantity** (notional times a participation that is
  itself proportional to notional), and reaches the venue's fill price. This
  replaces `test_market_impact_is_inert_without_average_dollar_volume`, which
  pinned the finding; asserting zero impact would now pin the bug.

Both fail against the call sites as they stood.
