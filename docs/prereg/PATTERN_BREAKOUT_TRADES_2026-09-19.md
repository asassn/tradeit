# Pre-registration — pattern breakouts as complete trades, 2010–2019

Committed **before any statistic on 2010–2019 pattern events existed**. 2020–2025
is held out and not read. The design window, 2003–2009, was read at length and
everything taken from it is disclosed below.

## Why this is a different question from §13 and §31

Both measured the twelve detectors as a **quality score ranked across the
market** and found nothing. That design cannot see what a trader means by a
pattern. A rule that wins two trades in five at three times its risk is
profitable and reads as **zero** in a rank correlation, because rank
correlation describes the middle of a distribution and a trade lives in its
tail and in its exit rule. This registers the first **complete trade** this
project has ever tested: entry, stop, exit, costs, and a control.

## What the design window showed — disclosed in full

Read on 2026-09-19 from 377,603 breakout events on 2003–2009, after the §0.10
guard. **These numbers chose the rule below, and none of them is evidence.**

| exit rule, pattern's own stop | net R/trade | win rate | per trade |
|---|---|---|---|
| target +1R | −0.002 | 51.7% | +0.11% |
| target +2R | +0.034 | 42.4% | +0.32% |
| target +3R | +0.051 | 40.0% | +0.39% |
| trailing 2×ATR after +1R | +0.056 | 47.3% | +0.13% |
| **hold 63 sessions or stop** | **+0.101** | 38.0% | **+0.48%** |

- **Quality grades**, monotonically, which it failed to do as a score:
  +0.078R, +0.097R, +0.107R, +0.123R across quality quartiles.
- **Stop distance matters**: under 6% of price +0.273R, 6–9% +0.107R, 9–13%
  +0.059R, 13–20% +0.024R, **over 20% −0.029R**.
- **The rule is mostly the market.** By entry year: 2003 +0.493R, 2005 +0.203R,
  2006 +0.256R, 2007 −0.068R, **2008 −0.532R**, 2009 +0.390R. A long-only
  breakout rule makes money when the market rises, which is why the control
  below is the primary criterion and not a footnote.

```
PRE-REGISTRATION -- pattern breakouts as complete trades
written 2026-09-19, BEFORE any 2010-2019 pattern statistic existed

THE RULE -- one rule, fixed here, no variants
  UNIVERSE   security_spans_2026-09-18.csv. At the scan session: close >= $5,
             20-session average dollar volume >= $1,000,000 on the corrected
             read (§0.9), atr_percent_14 <= 1.0, 100 sessions of own history.
  SCAN       the twelve enabled D1 detectors, trailing window as the scanner
             requires, every 5th session. Keep instances in state MATURE or
             NEAR_BREAKOUT carrying a resistance boundary -- structures that
             have NOT broken out.
  QUALITY    quality >= 58.5, the design window's median. Chosen there because
             the gradient is monotone across quartiles; disclosed as chosen.
  ONE PER DAY  where several detectors fire on one security on one session,
             the highest-quality instance is the trade. The others are the
             same structure seen twice and would inflate the sample.
  TRIGGER    the first session within 10 sessions whose CLOSE exceeds the
             boundary AS FROZEN AT THE SCAN. A boundary redrawn on later bars
             is hindsight.
  ENTRY      the NEXT session's open.
  STOP       the pattern's invalidation price, and the trade is taken only if
             that stop sits between 3% and 13% below the entry. Outside that
             band the design window is flat to negative and the position size
             a fixed risk implies is either absurd or trivial.
  EXIT       the stop, or the close of the 63rd session after entry,
             whichever comes first. A session touching the stop counts as
             STOPPED even if it also traded higher: daily bars cannot order
             the two and the pessimistic reading cannot flatter the rule.
  COSTS      10 bps per side, charged to entry and exit.
  DEAD NAMES a security that never trades again inside the window exits at
             its last traded close, priced at recovery 1.0 AND 0.0 (§41's
             rule). Both must pass.
  §0.10      any trade whose window contains a flagged discontinuity is
             excluded (RESEARCH_01_DATA_DICTIONARY.md §0.10).

THE CONTROL -- the primary criterion, because the design window says the
rule is mostly the market
  For every trade the rule takes, a PLACEBO trade: the same entry session,
  a security drawn at random (seed 20260919) from that session's eligible
  universe, entered at its open, with the SAME stop distance as a fraction
  of price and the SAME 63-session exit. Same dates, same market, same
  holding period, same costs -- everything except the pattern.
  The placebo set is drawn ONCE and fixed before the rule is measured.

WINDOW   2010-01-04 .. 2019-12-31; every exit on or before 2019-12-31.
         2020-2025 IS NOT READ.

ESTIMATOR
  Trades overlap in time, so per-trade outcomes are not independent. The
  statistic is the mean net R per trade within non-overlapping 63-session
  CALENDAR BLOCKS, then the mean of the block means, with a t from the
  block means and Newey-West lag 1 -- the estimator §33 onward has used.
  The same blocks are used for the placebo, and the criterion is on the
  DIFFERENCE per block, which removes the market the two share.

HURDLE   Ledger 128 -> 130. small_sample_hurdle(130, blocks).

--------------------------------------------------------------------
WHAT COUNTS AS A PASS -- ALL FIVE, at recovery 1.0 AND 0.0

  1 The per-block mean of (rule - placebo) net R is positive and its t
    clears the hurdle. THIS IS THE CRITERION. A rule that beats zero but
    not the placebo has discovered the market, not a pattern.
  2 Mean net R per trade positive on its own, after costs.
  3 Positive against the placebo in BOTH halves: 2010-2014, 2015-2019.
  4 Positive against the placebo with the single best calendar year
    REMOVED. 2008 moved the design window by half an R; a rule that
    needs one year is a bet on that year.
  5 Still positive against the placebo at DOUBLE costs, 20 bps a side.
    An edge that dies at 40 bps round trip is not tradeable by anyone
    who is not a broker.

THE MACHINERY CHECK, judged first
  The placebo's own mean net R must be within 0.05R of the same
  population's buy-and-hold over 63 sessions, minus costs. If the placebo
  does not reproduce the market it is not a control, and the run is VOID
  before any rule statistic is printed.

WHAT A PASS LICENSES
  ONE portfolio registration: the rule run through the platform's own
  engine -- sizing, participation limits, the stop ladder, delisting
  recovery -- against the random-selection benchmark of §37/§38, on the
  same window. §37 is the precedent and the reason: low volatility
  passed as a signal and lost as a portfolio. Nothing else moves.

STOP RULE
  If the rule fails, **pattern breakouts as single-pattern trade rules are
  closed on this corpus**: no other exit rule, target, trailing multiple,
  quality threshold or stop band is tried on daily bars. The next question
  would have to be a different kind of thing -- combinations, or intraday
  data this corpus does not hold.

TRIALS  2 -- the rule against its placebo, and the quality gradient.
        Ledger 128 -> 130.
```
