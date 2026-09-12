# Pre-registration — signed accumulation as an exclusion gate, 2020–2024

Committed before any 2020s run of this signal. §24 found the strongest
replication in the project and refused it: `obv_trend`'s 21-session inverse
relationship held its sign in **eight samples across two decades** and tripled
in strength out of sample, but its compounded edge reversed between halves of
that decade, so it did not survive as a selection signal.

A gate is a different claim from a selection signal. This tests it.

```
THE CLAIM
  Candidates in the TOP quintile of obv_trend -- the ones being bought on
  balance -- are worse than the rest, so refusing them improves what a rule
  trades. Note the direction: §24 measured high signed accumulation predicting
  LOWER returns, so the gate refuses the top, not the bottom.

TWO TESTS, AND THE PRIMARY IS NOT THE PORTFOLIO ONE
  §20 tested a gate by running a strategy twice and comparing. It failed its
  criteria and, measured on its own output, could not have established the
  opposite: the paired difference had sd 24.78pp at recovery 1.0 across four
  samples. Eight samples improves the standard error only as sqrt(n) -- to
  8.76pp, so a resolvable effect is about 17.5pp a year. **A portfolio test at
  this scale cannot see an effect of a few points, and saying so afterwards
  would be an excuse. It is said here.**

  PRIMARY -- the candidate stream. A gate's claim is about the candidates it
  refuses. Every candidate the baseline rule emits is recorded with its
  obv_trend percentile and its forward return, and the question is whether the
  refused ones underperform the admitted ones. Thousands of observations
  rather than eight paired differences, and it tests exactly what a gate does.

  SECONDARY -- the portfolio, run anyway because a candidate-level edge that
  does not survive sizing, costs and position limits is not worth having.
  Eight disjoint samples at cap 150 (150 survived + 150 died each; verified
  disjoint), both recovery assumptions, gated against ungated on identical
  universes. Its resolution is stated above and its result is reported with
  that number attached whichever way it falls.

PERIOD     2020-01-02 .. 2024-12-31, warm-up history from 2019.
           DISCLOSURE: not unseen. §20 ran the 250-session gate over it and
           the volatility-sizing backtest covered 2010-2024. obv_trend has
           never been computed on it.

THE GATE   Refuse a candidate whose obv_trend percentile on the decision
           session is at or above 0.80, ranked across the securities of that
           sample holding a served bar that day. Entry only; a gate refuses
           and can never promote or size. Threshold fixed at 0.80, not tuned;
           if it fails no other threshold is tried on this data.
           A candidate whose rank is unknown is NOT refused, as §20 set.

PRIMARY PASSES ONLY IF, AT EITHER HORIZON
  1. Refused candidates' mean forward return is below admitted candidates',
     with a t-statistic beyond the 42-trial hurdle (|t| > 2.19, measured),
     overlap-corrected.
  2. It holds in BOTH 2020-2022 and 2023-2024.
  3. The geometric mean of admitted beats that of all candidates, in both
     halves -- because a portfolio compounds, and §13 showed arithmetic and
     geometric can disagree in sign on this corpus.

SECONDARY PASSES ONLY IF, UNDER BOTH RECOVERIES
  gated return beats ungated in at least 6 of 8 samples, mean CAGR improves,
  and mean drawdown is not worse by more than 1.0pp.

TRIALS  3 -- the candidate test at two horizons, the portfolio test as one.
        Ledger 39 -> 42.

WHAT A PRIMARY PASS WITH A SECONDARY FAIL WOULD MEAN
  That the gate improves candidate quality by less than this portfolio design
  can resolve. That is a real and publishable outcome, and it is NOT evidence
  the gate works in a portfolio -- it is evidence the question needs a design
  that beats path dependence, which §20 established this family of tests does
  not.
```
