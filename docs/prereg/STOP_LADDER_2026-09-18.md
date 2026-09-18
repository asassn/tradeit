# Pre-registration — does the platform's stop ladder beat holding?

Committed **before the switch that disables price exits existed as code** and
before either arm below was run. Requested by the owner ("pre-register the
stop-loss test and run it") after §37.

**Where the question came from, and why that decides the data.** §37's random
portfolio, run through the platform's stop ladder, earned +7.77% a year on
2020–2025 against the low-volatility portfolio's +3.31%, and its exits showed the
ladder at work: 134 stop-losses cutting losers, 99 partial profits on winners.
That suggested the edge on this corpus may sit in the exits rather than the
selection. **The suggestion came from 2020–2025, so 2020–2025 cannot test it.**
The primary test is on **2010–2019**, where nobody has compared the ladder with
holding; 2020–2025 is run as a declared diagnostic that decides nothing.

```
PRE-REGISTRATION -- the stop ladder against holding
written 2026-09-18, BEFORE the hold switch was coded or either arm run

THE COMPARISON -- one change between two arms
  LADDER  the platform's default exits: stop-loss at the 8% initial stop,
          stop to breakeven at 1R, a 3 x ATR trailing stop from 1.5R, a
          33% partial profit at 2R, and the 63-session time stop.
  HOLD    the same position, sized off the same 8% stop, closed ONLY by the
          63-session time stop. No stop-loss, no trailing, no breakeven, no
          partial profit.
  Everything else identical: the same RANDOM selection (FactorTilt,
  seed 20260918, the calmest-agnostic control from §37), the same
  PortfolioCycle, sizer, risk rules, cost and fill models, rebalance
  grid and delisting rule. The stop still sets position size in HOLD --
  so both arms buy the same amounts -- it just never fires.

  RANDOM selection, not a signal, deliberately: the question is what the
  exits do to arbitrary picks, and a selection rule with its own edge
  would confound the two.

HOW HOLD IS BUILT
  ExitConfig gains price_exits (default True, so no existing run
  changes). False makes StopLadder.evaluate_exit return only the time
  stop. Exits planned on a session free their slots for that session's
  entries (PortfolioCycle.plan enters against the post-exit book), so
  HOLD's positions closing on a rebalance date do not strand its cash.
  LADDER's positions stopped out mid-period DO sit in cash until the next
  rebalance: that is part of what a stop costs, and it is measured, not
  corrected.

DATA -- primary
  2010-01-04 .. 2019-12-31, warm-up from 2009-01-02. Rebalance every 63rd
  XNYS session from 2010-01-04 (tradeit.signals.sampling.common_grid).
  Population: security_spans_2026-09-18.csv printing on 2010-01-04, with a
  median session dollar volume across 2009 of at least $1,000,000 on at
  least 100 traded sessions, on the corrected read. Four disjoint samples
  of N = min(500, population // 4), drawn in proportion by a seeded
  shuffle (seed 20260918) -- not balanced by fate.

CAPITAL, EXECUTION, COSTS
  $100,000; participation 2%; risk-free 3.0%; delisting after 10 silent
  sessions; recovery 1.0 primary, 0.0 diagnostic; default CostConfig
  primary, 5x spread and slippage (15 / 25 bps) for criterion 5.
  Every other StrategyConfig value at its default.

HURDLE -- TWO-SIDED, AND CHARGED FOR IT
  Either answer matters: the ladder adding value confirms the platform's
  defaults; the ladder COSTING money says they should be revisited. A
  direction read off the result would make the prior unfalsifiable, so
  both are declared now and both are charged: 2 trials, ledger
  102 -> 104, |t| > 2.5444 = expected_max_of_normals(104).

--------------------------------------------------------------------
WHAT COUNTS AS A RESULT -- in either direction, ALL FIVE in that direction

  "THE LADDER ADDS VALUE" requires:
  1 LADDER's CAGR exceeds HOLD's in all four samples.
  2 The mean paired CAGR difference, LADDER minus HOLD, over its standard
    error across the four samples, exceeds +2.5444.
  3 LADDER's Sharpe exceeds HOLD's in at least 3 of 4 samples.
  4 The mean paired annualised difference is positive in 2010-2014 AND in
    2015-2019 (the walk-forward check: nothing is fit, so it asks whether
    the edge holds through time).
  5 Criterion 1 still holds at 5x spread and slippage. The ladder trades
    more than HOLD, so costs fall harder on it; an edge that dies at
    realistic costs was the default cost model's.

  "THE LADDER COSTS MONEY" requires the mirror of all five: HOLD ahead in
  all four samples; paired t below -2.5444; HOLD's Sharpe ahead in 3 of
  4; the paired difference negative in both halves; and HOLD ahead in all
  four samples at 5x costs.

  Anything else is INCONCLUSIVE, and is reported as that.

WHAT EITHER RESULT LICENSES
  Nothing moves. "Adds value" confirms the default exits as measured.
  "Costs money" licenses a SCOPED PROPOSITION to revisit the exit
  defaults, put to the owner -- exit rules are trading logic and are not
  changed by a measurement.

--------------------------------------------------------------------
DIAGNOSTICS, DECLARED NOW, DECIDING NOTHING
  Maximum drawdown for both arms (a risk control that does not reduce
  drawdown is not doing its job, but drawdown is not the registered
  question); exit reasons; trades; recovery 0.0; and the same comparison
  on 2020-2025 -- the data that suggested the question, which therefore
  can only illustrate it.

RESOLUTION
  Unknown for this pair. The two arms hold the same names on the same
  dates, so their paired difference should vary less than §37's (sd
  1.14 pp for two different selections); if it is about 1 pp, criterion 2
  resolves a mean of about 2.5444 x 1 / 2 = 1.3 pp a year.

TRIALS  2 (two-sided). Ledger 102 -> 104.
```
