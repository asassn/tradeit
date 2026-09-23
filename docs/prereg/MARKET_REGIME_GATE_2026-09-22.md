# Pre-registration — a weekly-trend regime gate: when to be invested at all

Committed **before the gate existed as code and before any statistic about it
was computed**.

## Why this question, and why it is not another signal

Every one of the 138 trials so far asked **which securities to own**. This asks
**whether to be invested at all**, which no test here has touched and which the
evidence keeps pointing at:

- §44's design window: the same pattern rule earned **+0.493R a trade in 2003**
  and **−0.532R in 2008**. Selection barely moved; the year did.
- §46 and §48: the same combination gave **+2.54 pp** over random on 2013–2019
  and **−2.30 pp** on 2020–2025.
- §37, §46, §48: the random control returned 3.19% and 7.71% a year in two
  windows. The market it was long in explains more than any arm did.

This is the Swing mandate's own **context layer** — `MULTI_TIMEFRAME_MANDATES.md`
puts `1w`/`1d` above the daily setup — tested for the first time. It is derived
from daily bars, not bought: weekly structure is aggregation, not new data.

```
PRE-REGISTRATION -- a market-regime gate on the portfolio
written 2026-09-22, BEFORE the gate existed as code

THE MARKET PROXY -- built from the sample itself, nothing imported
  On each session, the equal-weighted index of the sample's own eligible
  securities: the mean of their one-session returns, compounded. Built
  from the sample rather than from SPY deliberately -- an index bought
  in would carry its own survivorship and its own identity questions,
  and the question is whether THIS population's own trend predicts its
  own returns.

THE GATE -- one rule, fixed here, no search
  On a rebalance date, if the proxy index is AT OR ABOVE its own
  50-session simple average, the arm nominates as it always would.
  Below it, the arm nominates NOTHING and the portfolio holds cash
  until the next rebalance.
  50 sessions is ten trading weeks -- the `1w` context layer expressed
  in the daily bars it is derived from. No other lookback is run. A grid
  over lookbacks would be the tuning this registration exists to avoid.

DESIGN   §46's, re-run: 2013-01-02 .. 2019-12-31, four disjoint samples
         of 500, the platform's sizer, stops, costs, fills and delisting
         handling, recovery 1.0 and 0.0, base and stress costs.
         Every arm is run twice, gated and ungated, so the gate is the
         only difference. 2020-2025 IS NOT READ.

--------------------------------------------------------------------
WHAT COUNTS AS A PASS -- ALL FOUR

  1 THE CONTROL IMPROVES. The RANDOM arm's mean CAGR is higher gated
    than ungated, in at least 3 of 4 samples and on the mean, at BOTH
    recoveries. This is the criterion: a regime effect is a fact about
    the market, so it must show on a portfolio that chooses at random.
    If it only helps a selected arm it is selection, not regime, and
    this registration refuses it.
  2 DRAWDOWN FALLS. The RANDOM arm's mean maximum drawdown falls by at
    least 3 percentage points at recovery 1.0. Sitting out declines is
    the entire mechanism claimed; if drawdown does not fall, whatever
    happened was not that.
  3 IT DOES NOT MERELY SIT IN CASH. The gated arm's exposure is at
    least half the ungated arm's. A rule that is flat most of the time
    has not improved returns, it has declined to participate.
  4 IT SURVIVES STRESS COSTS (15 bps spread, 25 bps slippage). A gate
    trades in and out of the whole book; if that round trip eats the
    benefit, there is no benefit.

POWER, stated before the result
  Seven years hold about 28 rebalances and perhaps two or three real
  declines. This test cannot distinguish a small regime effect from
  none, and a pass would rest on a handful of episodes. Said now so a
  pass is read with the same suspicion as a failure.

WHAT A PASS LICENSES
  ONE confirmation registration on the held-out 2020-2025 -- a window
  containing two declines the test window does not, which is the right
  place for this particular claim to be checked and the reason it is
  held back.

STOP RULE
  If it fails, **timing the market by its own trend is closed on this
  corpus**: no other lookback, no other proxy, no volatility-based or
  breadth-based variant. The next question would need information this
  corpus does not hold.

TRIALS  2 -- the gate on the control arm, and the gate on the calm arm.
        Ledger 138 -> 140.
```
