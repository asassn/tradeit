# Pre-registration — a volatility-scaled stop, compared before it is adopted

Committed **before the volatility stop existed as code and before any statistic
about it was computed**. Authorised by the owner on 2026-09-22 as a scoped
proposition (CLAUDE.md: stop rules are trading logic and do not change without
one). **Nothing changes in the platform on this registration alone** — it
compares, and adoption is conditional on what it finds.

## The defect this addresses

§46 measured exit reasons by arm on 2013–2019, sample 0:

| arm | delisted exit | stop loss | time stop |
|---|---|---|---|
| **calm** | **6.2%** | 12.6% | 75.0% |
| combined | 0.6% | 18.1% | 65.8% |
| surprise | 0.5% | 28.1% | 45.5% |
| random | 0.5% | 33.8% | 40.2% |

The platform's stop is a fixed **8% of entry price**. A security selected for
moving less than everything else rarely travels 8% inside a holding period, so
its stop sits outside its own range and never binds: the position is not
stopped, it is *delisted*, and at zero recovery the whole position is lost. A
first diagnostic ruled out the alternative explanation — dying securities are
not quiet beforehand (median volatility percentile 61.8% at 63 sessions before
the last bar; 17.9% in the calmest fifth against chance's 20%).

**A stop expressed as a percentage of price is not a risk control for a
low-volatility position.** That is the claim being tested, and the test is
whether replacing it improves the *control* arm, not the arm that suffers most.

```
PRE-REGISTRATION -- volatility-scaled stop vs fixed 8%
written 2026-09-22, BEFORE the rule existed as code

THE RULE -- one candidate, fixed here, no search
  stop = entry - 2.5 x ATR(14) at the nomination session,
         floored at 3% of entry and capped at 13%.
  2.5 is a practitioner's multiple, taken as given rather than chosen by
  search: no other multiple is run, and a grid over multiples would be
  the tuning this registration exists to avoid. The floor and cap are
  §44's registered stop band, reused so the two studies stay comparable.
  ATR(14) is the platform's existing kernel; nothing new is introduced.

DESIGN   §46's, unchanged and re-run: 2013-01-02 .. 2019-12-31, four
         disjoint samples of 500, arms COMBINED / CALM / SURPRISE /
         RANDOM, the platform's own sizer, costs, fills and delisting
         handling, recovery 1.0 and 0.0, base and stress costs.
         2020-2025 IS NOT READ.
         The only difference between this run and §46 is the stop.

--------------------------------------------------------------------
WHAT ADOPTION REQUIRES -- ALL FOUR. Any failure and the fixed stop stands.

  1 THE CONTROL IMPROVES. The RANDOM arm's mean CAGR across the four
    samples is no worse under the volatility stop than under the fixed
    stop, at BOTH recoveries. This is the criterion that separates
    "fixed a defect" from "flattered one signal": a stop rule that only
    helps the calm arm has been tuned to §46's result, and is refused
    however good that result looks.
  2 THE MECHANISM MOVES. The CALM arm's delisted-exit share falls below
    3.1% -- half its measured 6.2%. If the stop still does not bind for
    calm securities, the rule has not addressed what it was written for,
    whatever it does to returns.
  3 NO ARM PAYS IN DRAWDOWN. No arm's mean maximum drawdown worsens by
    more than 5 percentage points at recovery 1.0.
  4 IT DOES NOT TRADE ITSELF POOR. No arm's trade count rises by more
    than 50%. §42 measured costs deciding results before significance
    did; a stop that fires constantly is a cost engine.

WHAT ADOPTION WOULD THEN REQUIRE, SEPARATELY
  §37, §38, §39 and §46 were all run with the fixed stop. If the rule is
  adopted they are re-run and their sections amended, because a result
  produced by machinery that has since changed is a historical record and
  not a current claim. That work is part of adoption, not optional after it.

IF IT FAILS
  The fixed stop stands, the defect is recorded as a known limitation of
  every low-volatility portfolio result, and no further stop variant is
  tried without new evidence about the mechanism -- not a different
  multiple, not a different floor, not a trailing variant.

TRIALS  2 -- the rule on the control arm, and the rule on the calm arm.
        Ledger 133 -> 135.
```
