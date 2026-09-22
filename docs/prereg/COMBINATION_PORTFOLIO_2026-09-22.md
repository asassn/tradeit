# Pre-registration — the two strongest signals combined, as a portfolio, 2013–2019

Committed **before the combination existed as code and before any statistic
about it was computed**. 2020–2025 is held out.

## Why these two, and why a portfolio

Every one of the 130 trials so far asked *does this one number predict?* The
wrap-up records the remaining free route as the opposite question: **do signals
combine, and does the combination pay?** Two candidates have the only surviving
evidence in this project:

| | evidence | where |
|---|---|---|
| **low volatility** (`realized_volatility_60`, low favoured) | **passes** a registered out-of-sample signal test | §35 |
| | **fails as a portfolio** — −4.35 pp/yr against random selection, 4 of 4 samples | §37 |
| **earnings surprise** (`sue`, high favoured) | consistent in direction on **every** cut at both horizons; never significant | §40, §41 |

They are economically distinct — one is a risk characteristic, the other is
information — which is the only honest reason to expect a combination to do
something neither does alone. §37 is the specific hope: low volatility selects
calm, dull securities, and an earnings-surprise filter would keep the calm ones
that have *something happening*.

**This goes straight to the portfolio.** §35 and §37 together taught that a
signal that predicts need not pay, so the cross-sectional half is skipped and
the question is asked where it matters: through the platform's own sizer, stop
ladder, costs, fills and delisting handling, against a portfolio that chooses at
random.

## The disclosure that weakens this test, stated first

**There is no clean data left for these two signals.** §41 measured `sue` on
2013–2025 pooled, which contains this window; §35 and §37 measured volatility on
2020–2025, which is the confirmation window. So:

- the **test** window (2013–2019) was seen for `sue`, though never examined
  as a window of its own;
- the **confirmation** window (2020–2025) was seen for both components.

Nothing can undo that. It is recorded because a reader is entitled to discount
the result by it, and it is the reason the hurdle below is not relaxed.

```
PRE-REGISTRATION -- low volatility x earnings surprise, as a portfolio
written 2026-09-22, BEFORE the combined strategy existed as code

WINDOW   2013-01-02 .. 2019-12-31. Fundamentals are usable from 2013 (§10);
         2020-2025 IS NOT READ.

SAMPLES  Four disjoint samples of 500 securities, drawn in proportion from
         those alive and liquid on 2013-01-02, exactly as §37 drew them.
         Four samples because §15 measured that one sample of this corpus
         carries noise of tens of percentage points.

ARMS -- identical in every respect except which securities are nominated
  COMBINED  the average of two within-date ranks: realized_volatility_60
            ascending, and sue descending. Best fifth nominated. Plain
            mean of ranks, no weights -- a weight would be a parameter and
            this registration has none to tune.
  CALM      low volatility alone, the §37 arm, on this window.
  SURPRISE  sue alone, best fifth.
  RANDOM    the control: a seeded hash of (security, date).
  A security missing sue on a date is not nominable by COMBINED or
  SURPRISE on that date. It remains nominable by CALM and RANDOM, because
  removing it from those would change the control as well as the arm.

SIGNALS   sue exactly as §40 defines it, point-in-time, as first filed,
          usable only if filed strictly before the rebalance session.
          realized_volatility_60 from the engine's own restated bars.

PORTFOLIO  the platform's: equal-dollar sizing, participation limits, the
          stop ladder, costs and fills as configured, 63-session hold,
          rebalancing on the same calendar for every arm. Delisting
          recovery 1.0 AND 0.0; a pass requires both.

§0.10     trades whose window crosses a flagged discontinuity are excluded.

--------------------------------------------------------------------
WHAT COUNTS AS A PASS -- ALL FOUR, at recovery 1.0 and 0.0

  1 COMBINED beats RANDOM on CAGR in at least 3 of the 4 samples, and on
    the mean paired difference across them.
  2 COMBINED beats BOTH single-signal arms on the mean paired difference.
    A combination that does not beat its own components has discovered
    nothing -- this is the criterion the whole registration exists for.
  3 The margin over RANDOM exceeds 2.0 percentage points a year, which is
    the smallest gap §37 measured between its arms and therefore the
    smallest this design has ever been able to distinguish from noise.
  4 COMBINED's maximum drawdown is no worse than RANDOM's by more than
    5 percentage points. A return bought with proportionally more
    drawdown is not an improvement, and §38 is the precedent.

WHAT A PASS LICENSES
  ONE confirmation registration on 2020-2025, which must disclose that
  both components were measured there. Nothing else moves; no weight, no
  gate, no live capital.

STOP RULE
  If it fails, **signal combination is closed on this corpus for these two
  signals**: no other weighting, quantile, ordering or pair drawn from the
  measured set is tried. The next question would need a signal this
  project has not yet measured, or data it does not hold.

TRIALS  3 -- COMBINED, CALM and SURPRISE as portfolios on a new window.
        RANDOM is a control and is not charged. Ledger 130 -> 133.
```
