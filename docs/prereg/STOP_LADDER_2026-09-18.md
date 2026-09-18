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

```
AMENDMENT 1 -- 2026-09-18, BEFORE ANY REGISTERED RUN. No return of either
arm has been computed on either window.

WHAT WAS SEEN WHEN THIS WAS WRITTEN
  A mechanics-only pilot of the runner on 60 securities of the 2020-2025
  diagnostic window, printing trade and exit counts and nothing else:
    LADDER  291 trades: stop-loss 99, trailing 0, partial profit 66,
            time stop 125, delisted 1
    HOLD    225 trades: time stop 224, delisted 1
  HOLD behaved as registered: nothing closed it but the clock.

THE DEFECT THE PILOT EXPOSED
  Sixty-six partial profits at 2R and not one trailing exit is not a
  shape the registered ladder can produce: a position that reaches 2R has
  passed 1R, where its stop moves to breakeven, so any later stop-out is a
  trailing exit. The cause is in the engine, not the ladder.
  EventDrivenEngine built every position with stop_price = the INITIAL
  stop and discarded the CyclePlan's stop_updates, so the ladder's
  breakeven and trailing moves were computed and thrown away. Every
  backtest this platform has run held its initial stop for the life of
  the position.

  Fixed in the engine: each lot carries a current stop, the plan's stop
  moves are applied after each session's decision (upward only), and it
  is restated at splits. A test runs a winner past 1R and back through its
  entry and requires a trailing exit above the initial stop; it FAILS
  with the updates discarded.

WHAT THIS CHANGES
  The registered LADDER -- stop-loss, breakeven at 1R, trailing from
  1.5R, partial profit at 2R, time stop -- is now what the engine runs.
  Before the fix the engine ran only three of its five parts. The
  specification does not change; the machine under it was made to do
  what the specification said.

  EVERY EARLIER ENGINE RESULT ran without breakeven and trailing: §7, §28,
  §36 and §37. §37 is the current verdict on low volatility as a strategy,
  so it is re-run on the fixed engine under its own registration, which
  named "the platform's own stop ladder", and both are reported. The
  others are recorded as due.

TRIALS  Unchanged at 2.
```

```
AMENDMENT 2 -- 2026-09-18, AFTER TWO SAMPLES CRASHED, BEFORE ANY VERDICT
WAS READ.

WHAT WAS SEEN WHEN THIS WAS WRITTEN
  The four registered 2010-2019 samples were run. Samples 0 and 1 wrote
  result files; NOTHING IN THEM HAS BEEN READ. Samples 2 and 3 crashed
  while computing their half-period returns, because their LADDER arm's
  equity had gone NEGATIVE -- a long-only portfolio losing more than it
  held. A diagnostic run of those two LADDER arms (base costs, recovery
  1.0) was then examined to find the cause: its minimum equity, and the
  trades with the largest P&L swing and the lowest entry prices. No HOLD
  arm, no paired comparison and no criterion was computed.

THE CAUSE
  Security 9106 entered 2010-07-07 at $0.000100065: 20,582,325 shares
  for about $2,000 of notional. At CostConfig's $0.005 per share the
  entry cost $102,911 in commission. Sample 3: security 6792 at $0.0002,
  77,601,411 shares, $388,007 of commission. Cash went negative on the
  entry date; equity reached -$520,343 and -$650,233. Several other
  entries below $1 were admitted too.

  They passed eligibility because the registered rule -- 100 sessions of
  history, $1M/day of 20-session turnover, atr_percent <= 1.0, a close
  above zero -- has no minimum PRICE. A security collapsing to a
  placeholder print keeps the turnover of its last normal weeks in a
  20-session window, and a flat sub-penny window reads as calm, not as a
  bad print.

THE RULE ADDED
  A security is eligible only if its close on the rebalance date is at
  least $5 -- LiquidityConfig.min_price, the platform's own declared
  minimum tradeable price, read from the config default rather than
  typed here. Point-in-time, applied identically to both arms.

WHY THIS IS NOT OUTCOME-DRIVEN
  1 It is the platform's figure, fixed before this test existed. The
    registration adopted "every other StrategyConfig value at its
    default"; the strategy never read LiquidityConfig, so that default
    was silently absent. The amendment makes the strategy honour what the
    registration already said it would.
  2 It is signal-side and point-in-time: the close on the day of the
    decision, which a live system has.
  3 The alternative -- judging on samples 2 and 3 as they stand -- is
    judging a test decided by one $0.0001 trade's commission, which is
    a property of the cost model, not of stops.
  4 No result has been read. Samples 0 and 1 are re-run too, from
    scratch, so all four samples are judged under one rule.

ALSO RECORDED, NOT FIXED HERE
  The platform's sizer will open a position whose commission is fifty
  times its value. That is trading logic and is raised with the owner
  separately; this test does not depend on it once sub-$5 names are
  ineligible.

  The same eligibility gap was in §37's run, whose registration carries
  the same defaults clause; §37 is re-run with the $5 rule alongside.

TRIALS  Unchanged at 2.
```
