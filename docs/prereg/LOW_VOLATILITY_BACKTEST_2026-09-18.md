# Pre-registration — a low-volatility portfolio against a random one, 2020–2025

Committed **before the strategy below existed as code** and before any backtest
of it was run. Requested by the owner ("pre-register the backtest") after §35
found `realized_volatility_60` to pass a registered out-of-sample signal test on
tradeable securities.

**The question is different from §35's, and the difference is the point.** §35
asked whether low volatility *predicts* returns across securities. This asks
whether a portfolio built on it, run through this platform's own portfolio
machinery — its sizer, risk rules, stop ladder, cost and fill models, next-session
execution, delisting handling — *makes money after costs*, and more than an
otherwise identical portfolio that chooses at random. §35 said: *"a factor that
predicts is not yet a trade that pays."* This is the test of the second half.

```
PRE-REGISTRATION -- low-volatility portfolio backtest, 2020-2025
written 2026-09-18, BEFORE the strategy was coded or any backtest run

THE COMPARISON -- one change between two arms
  CALM    on each rebalance date, nominate the least-volatile fifth of the
          sample's tradeable securities by realized_volatility_60,
          scored so the calmest rank first.
  RANDOM  on the same dates, nominate the same NUMBER of the same sample's
          tradeable securities, ranked by a seeded hash of (security,
          date) -- a selection that knows nothing.
  Everything else is identical: the platform's PortfolioCycle, sizer,
  risk rules, stop ladder, cost model, fill model and delisting rule.
  The portfolio takes as many nominees as its own rules allow, best
  score first. Any difference between the arms is the selection.

WHAT IS FIXED, AND WHY EACH DEPARTS FROM A DEFAULT WHERE IT DOES
  Sizing: EQUAL DOLLAR. Every nominee's stop is 8% below entry -- the
    platform's max_initial_stop_pct -- instead of min(2 x ATR, 8%).
    The default gives calm names tighter stops and so larger positions,
    which would make CALM more concentrated and more invested than RANDOM.
    §28 measured that sizing dial on its own; this test isolates selection.
  Holding: time_stop_sessions = 63, the horizon §35 measured, so a
    position is held to the next rebalance unless a stop fires first.
  Pyramiding: OFF. A tilt rebalances; it does not add to winners.
  Rebalance: every 63rd XNYS session from 2020-01-02, from
    tradeit.signals.sampling.common_grid -- the same calendar for both arms.
  Every other StrategyConfig value: the platform default, untouched --
    max_positions 12, risk_per_trade 0.5%, heat 6%, gross 100%, the
    trailing/breakeven/partial-profit ladder, and CostConfig.

ELIGIBILITY ON A REBALANCE DATE -- point-in-time, the same for both arms
  100 of the security's own sessions seen; 20-session average dollar
  volume >= $1,000,000 on the corrected read; atr_percent_14 <= 1.0
  (DATA_DICTIONARY §0.8); a close above zero.
  The strategy sees RAW bars, as every strategy in the engine does, so
  it rescales its own stored history at each split the corpus records
  for the ex-date. Without that, a 60-session volatility spanning a split
  reads the split as a price move.
  History before 2020-01-02 is read as warm-up only; nothing trades then.

UNIVERSE AND SAMPLES
  Population: securities in security_spans_2026-09-18.csv printing on
  2020-01-02, whose median session dollar volume across 2019 (the corrected
  read) is at least $1,000,000 on at least 100 traded sessions.
  FOUR DISJOINT SAMPLES, drawn from that population by a seeded shuffle
  (seed 20260918), each of N = min(500, population // 4) securities.

  Population-proportional, NOT balanced by fate. Earlier backtests drew
  equal numbers of survivors and dead companies. That doubles the death
  rate relative to the market, and deaths fall mostly on volatile names
  -- so it would handicap RANDOM and flatter CALM. Drawn in proportion,
  each sample dies at the rate the population did.

  The universe is the population on 2020-01-02; later listings are not
  added. Stated, not hidden: this is a fixed universe that loses names to
  failure and acquisition over six years and gains none.

DELISTING RECOVERY -- the assumption least favourable to CALM
  Primary: 1.0. A holding that stops printing is closed at its last
  price. CALM's advantage over RANDOM partly comes from not holding
  volatile names that fail; a recovery of 1.0 makes failure free, which
  REMOVES that part of the advantage. A recovery of 0.0 would hand it
  back in full. 0.0 is run and reported as a diagnostic only.

CAPITAL, EXECUTION
  $100,000; participation 2% of the session's volume; risk-free 3.0%;
  delisting after 10 silent sessions. Decisions at a close execute at
  the next session, as the engine always does.

--------------------------------------------------------------------
WHAT COUNTS AS A PROFITABLE STRATEGY -- ALL FIVE

  1 CALM's CAGR exceeds RANDOM's in ALL FOUR samples.
  2 The mean paired CAGR difference, CALM minus RANDOM, over its standard
    error across the four samples, exceeds 2.5376 =
    expected_max_of_normals(102).
  3 CALM's CAGR exceeds the 3.0% risk-free rate in at least 3 of 4
    samples -- profitable in absolute terms, net of costs, not only
    relative to a random portfolio that may itself lose.
  4 WALK-FORWARD. The strategy fits nothing -- lookback, quintile,
    horizon and floor are all fixed above -- so walking forward reduces
    to asking whether the edge holds through time: the mean paired
    difference in annualised return is positive in 2020-2022 AND in
    2023-2025, each measured from the arms' own equity curves.
  5 COST STRESS. Criteria 1 and 3 still hold with spread and slippage at
    FIVE TIMES the defaults (spread 15 bps, slippage 25 bps). §35 found
    the effect concentrated in cheap stocks, whose real spreads the flat
    default of 3 bps understates; a result that dies at five times the
    default is a result the default was flattering.

DIAGNOSTICS, DECLARED NOW, DECIDING NOTHING
  Sharpe and maximum drawdown for both arms; turnover and cost paid;
  trades taken; the delisting-recovery-0.0 variant. None may be
  substituted for a criterion after the fact.

--------------------------------------------------------------------
RESOLUTION -- what four samples can and cannot see

  The paired CAGR standard deviation is not known for this design. §28
  measured 1.22 pp for a related paired comparison on 2010-2019; if this
  one is similar, criterion 2 resolves a mean difference of about
  2.5376 x 1.22 / 2 = 1.5 pp/yr. §35's geometric edge was +13.96%/yr
  before costs, stops and position limits -- a portfolio of 12 names
  capturing a fraction of it could still clear 1.5 pp; one capturing
  almost none could not, and a fail would then say so.

STOP RULE -- declared now
  If the strategy fails any criterion, the low-volatility factor stays a
  measured signal (§35) and does NOT become a candidate strategy on this
  corpus. No other lookback, quintile, holding period, position count,
  sizing rule or cost assumption is tried to rescue it. A pass licenses
  a scoped proposition for paper trading under the readiness framework --
  RESEARCH -> ROBUST_BACKTEST -- and nothing else. It moves no live
  parameter and no money.

TRIALS  1. Ledger 101 -> 102.
```

```
AMENDMENT 1 -- 2026-09-18, after §37 was reported, recorded as such.

TWO THINGS UNDER §37'S RESULT WERE NOT WHAT THIS REGISTRATION DESCRIBED

  1 THE STOP LADDER WAS INCOMPLETE. The engine discarded the ladder's stop
    moves, so breakeven and trailing never happened (found by the
    stop-ladder registration's pilot; STOP_LADDER Amendment 1). Re-run on
    the fixed engine, §37's verdict held: CALM -4.14 pp a year against
    RANDOM, t -3.68, the same four criteria failing.

  2 THERE WAS NO MINIMUM PRICE. This registration adopted "every other
    StrategyConfig value at its default", and LiquidityConfig.min_price
    defaults to $5, but the strategy never read it. In the stop-ladder
    test that let $0.0001 securities in, and per-share commission on tens
    of millions of shares drove two portfolios to negative equity
    (STOP_LADDER Amendment 2). §37's runs did not crash, but the same
    rule admitted sub-$5 names to both arms.

WHAT IS DONE
  §37 is re-run a third time, on the fixed engine and with the $5 minimum
  price, under this registration's unchanged criteria, and all three
  runs are reported. This amendment is written AFTER §37's first two
  results were read, and says so: it cannot claim to be blind to them.
  What protects it is that the rule is the platform's own fixed default,
  applied to both arms, and that the verdict it could change was already
  a clear failure -- the amendment can only confirm it or narrow it.

TRIALS  Unchanged.
```
