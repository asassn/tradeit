# The size penalty that has never been charged — measured, for a decision

Two things are owed from the owner on Track A. This document is the second one,
costed. (The first, the four readiness thresholds, is in
`PHASE_12_AUDIT_2026-10-18.md`.)

## One correction to an earlier statement

An earlier note said *"neither the venue nor the engine's `_settle` passes an
`average_dollar_volume`"* and implied the figure does not exist. **The figure
does exist and is computed in four places** — `baselines.py:254` and `:300`,
`factor.py:321`, and the walk-forward and Monte Carlo drivers — all as
`bar.close * bar.volume`, and it reaches `RiskBasedSizer`, where the **liquidity
participation cap works correctly.**

What is missing is narrower and more fixable than described: `engine._settle`
calls `self.costs.estimate(item.order, item.reference_bar)` and does not pass
the third argument, so the **cost model** never sees participation even though
the **sizer** does. `_settle` already holds `item.reference_bar`, so the change
is one argument at one call site.

## What has therefore never been charged

`ParticipationCostModel` charges half the spread plus a fixed slippage
allowance, and computes market impact **only** when handed a dollar volume:

```
impact = notional × market_impact_coefficient × (notional / average_dollar_volume)
```

Measured against the configured defaults:

| participation | impact | total cost per side | multiple of today |
|---|---|---|---|
| — *(today)* | 0 bps | **6.5 bps** | 1.0× |
| 0.1% | 1.0 bps | 7.5 bps | 1.2× |
| 0.5% | 5.0 bps | 11.5 bps | 1.8× |
| 1.0% | 10.0 bps | 16.5 bps | 2.5× |
| **2.0%** *(the configured cap)* | **20.0 bps** | **26.5 bps** | **4.1×** |

**At the participation cap the platform's own backtests run on roughly a quarter
of the trading cost the cost model was built to charge.** Every result on
`SIGNAL_SCOREBOARD.md` priced orders with no size penalty at all.

And `execution/base.py` already says why that matters, in its own words:

> A strategy that trades often can be profitable at 2 basis points of slippage
> and unprofitable at 15, and the difference between those two numbers is
> entirely a modelling assumption.

## The honest shape of the decision

**Switching it on is one argument. Deciding to is not**, because it is a
backtesting assumption, and this project requires a scoped proposition for
those rather than a commit.

**For switching it on**

* Every archived result is **optimistic by an unknown amount**, and the amount
  is largest exactly where it matters — concentrated positions in thinner names.
* It is already the declared model. The coefficient is configured at 0.1 and has
  simply never been reachable, so today's numbers describe a cost model nobody
  chose.
* §58 showed the archive survives a *diversification* constraint with a mean
  change of +0.04%. **This is the other constraint, and it is not small** — it
  would be the first real stress test of the results.
* It fails closed in the right direction: a result that survives higher costs is
  more trustworthy, never less.

**Against, or at least for waiting**

* **Every published number moves**, and the archive stops being comparable to
  anything run afterwards until it is re-run. §46 reproduced exactly a month
  later — that property breaks the day this changes.
* **The coefficient itself is unvalidated.** 0.1 is a default nobody measured
  against this corpus, so turning it on replaces a known-wrong assumption
  (zero) with an unverified one. Being wrong in a more realistic direction is
  still being wrong.
* Nothing currently depends on it. There is **no validated strategy** and no
  live capital, so no decision is being made today on the flattered numbers.
* Re-running the archive costs roughly a day of compute and the work is pure
  bookkeeping.

## What I would do, and why

**Switch it on, and re-run only what is still load-bearing** — which is very
little, because Track B's pattern program is closed and the combination line is
closed. Nothing in the archive is being used to justify a trade, so the usual
cost of invalidating it does not apply. The window where this is cheap is
exactly now, before anything is built on top of it.

**And measure the coefficient rather than inheriting 0.1**, as a separate and
later question: the paper venue produces real fills against real volumes, so
realised slippage versus modelled is measurable once a strategy runs. Switching
impact on does not depend on that and should not wait for it.

**If the answer is no**, the honest consequence is that every future result
carries the same caveat the archive now does, and the caveat should be stated
wherever a number appears rather than left in this file.

---

## Answered: switched on, 2026-10-23

**The owner approved the recommendation.** Impact is charged. The decision is
recorded as [ADR-0028](adr/0028-market-impact-is-charged.md); this section
records what was actually done and two things the implementation found.

Three call sites passed the dollar volume, not one: `engine._settle` as
described, and also `PaperBroker._fill_resting` and
`scripts/paper_session.py`. **The paper venue mattered more than the document
allowed for** — a strategy promoted on paper fills that paid no size penalty
would have been promoted on exactly the flattered number the backtest had
stopped using, so a paper venue cheaper than its own backtest is worse than
none.

**The 6.5 bps figure is confirmed against the code**, not just the table:
`CostConfig()` carries `spread_bps = 3.0` and `slippage_bps = 5.0`, so friction
is `3/2 + 5 = 6.5`, and a no-impact entry on a $100 reference fills at exactly
`100.0650`. That value is now asserted in
`test_the_engine_charges_market_impact_on_an_entry`, so the number this decision
was made on cannot drift without a test objecting.

### Two things the tests found that the measurement did not

**`trade.costs` is commission only.** Spread and impact are expressed in the
fill *price*, not in that field — it reads `$1.00`, the configured commission
minimum, whatever the coefficient is. The first version of the engine test
asserted on `trade.costs` and passed against unchanged code. Anyone reading a
per-trade cost out of the archive is reading commission.

**Impact is invisible at index-scale volumes, which is correct and worth
knowing.** At a $1bn dollar volume, participation is about 5e-6 and the impact
lands below the sixth decimal of the fill price. The term only bites where the
document said it would — concentrated positions in thinner names — so a test
built on a liquid bar proves nothing, and neither would a spot-check of a
large-cap backtest.

### The residual, stated rather than left to be rediscovered

Impact is zero when a bar's volume is zero, and **6.339% of raw corpus bars
carry volume 0** — 86.5% of those being flat copies of the previous close
(`RESEARCH_01_DATA_DICTIONARY.md` §0.7: a bar can have a price and no trade
behind it). Those are precisely the illiquid bars where impact would be
largest, so the cost model remains optimistic there. Refusing to fill on a
zero-volume bar is a change to *fill* behaviour rather than to cost and needs
its own proposition.

Measuring the coefficient itself against realised paper slippage also remains
open, as the recommendation said it should.
