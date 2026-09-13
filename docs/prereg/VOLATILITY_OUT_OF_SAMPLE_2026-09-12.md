# Pre-registration — the volatility relationship, 2010–2019

Committed before `realized_volatility_60` or `atr_percent` has been computed on
any session after 2009-12-31. If that ordering is ever in doubt, the git
timestamps settle it.

```
WHAT IS BEING TESTED, AND WHY IT IS NOT THE TEST THAT WAS ALREADY RUN
  The volatility relationship is the only one in this project that has never
  changed sign, and it is the largest effect found: realized_vol_60 at
  IC -0.060 (t -10.01) and atr_percent_14 at IC -0.078 (t -7.46) in sample,
  significant at every specification tried, filtered and unfiltered, at both
  horizons, always negative.

  It has never been tested out of sample AS A SIGNAL. What was tested, on
  2026-09-09, was a PORTFOLIO: equal-risk sizing with an ATR stop against
  equal-dollar sizing, which beat it in both periods (+8.08pp and +5.12pp,
  Sharpe +0.10 and +0.07) and graded MIXED out of sample because drawdown
  worsened. That answers "can it be monetised by sizing", not "does it
  predict". SIGNAL_RESEARCH_01.md §7 is the reason the second question was
  never answered, and it is a structural reason, not a shortage of data.

DISCLOSURE -- THIS PERIOD IS NOT UNSEEN, AND CANNOT BE MADE SO
  The sizing experiment covered 2010-2024. There is therefore NO unseen window
  left for the volatility hypothesis: 2000-2009 is the in-sample decade and
  everything after it has been looked at in portfolio form. 2020-2024 would be
  no cleaner, and is additionally spent by §20 and §25.

  What survives that contamination is that the two questions have different
  estimands. A sizing benefit can be pure volatility drag with no predictive
  relationship at all -- that is §7's own argument -- so knowing B beat A tells
  us little about the IC. The result is therefore reported as a REPLICATION ON
  PARTIALLY SEEN DATA and must not be written up as out-of-sample evidence.
  Stating this in advance is the point; discovering it afterwards would be an
  excuse.

SIGNALS    realized_volatility_60 and atr_percent from IndicatorEngine, the
           config's own periods (volatility_periods (20, 60), atr_period 14).
           Both are ratios -- annualised return dispersion, and ATR over price
           -- so both are immune to split adjustment, which a price-level
           signal would not be.

DIRECTION  NEGATIVE for both: a HIGHER volatility reading predicts a LOWER
           forward return. Taken from the in-sample measurement, which agreed
           in sign at every specification. The favoured quintile is therefore
           the BOTTOM one, and testing the top would be testing the opposite
           claim.

PERIOD     2010-01-04 .. 2019-12-31, warm-up history from 2009. Four disjoint
           samples of the 2010 population, cap 400 (400 survived + 400 died
           each), the universes §19, §22 and §24 used.
HORIZONS   21 and 63 sessions    STRIDE 21    QUANTILE 0.2
TRIALS     4 -- two signals at two horizons. Ledger 42 -> 46.
HURDLE     |t| > 2.2442, measured: expected_max_of_normals(46) = 2.2441708.

THE AGGREGATOR IS GEOMETRIC, AND THAT IS DECIDED HERE RATHER THAN AFTER
  Criterion 1 below is a GEOMETRIC edge where every previous registration used
  an ESTABLISHED arithmetic quantile spread. Two reasons, both written down
  before this run and neither derived from it:

    * §7 measured the arithmetic estimator STRUCTURALLY unfit for this
      particular signal. Sorting on volatility sorts on the variance of the
      thing being averaged, so the high-volatility bucket holds the extreme
      outcomes by construction and the mean-spread estimator has its worst
      variance exactly where the signal is strongest. In sample this produced
      mean spreads of +0.69% and +1.03% against medians of -5.54% and -5.28%
      -- the mean and the median disagreeing in SIGN -- and both signals
      graded OUTLIER_DEPENDENT. A criterion built on that estimator would be
      testing the estimator.
    * §13 measured that arithmetic and geometric quantile means disagree in
      sign on this corpus generally, and that a portfolio compounds the
      geometric one.

  The arithmetic spread and its verdict are still COMPUTED AND PUBLISHED for
  both signals at both horizons. They are diagnostics, not criteria, and they
  are published whichever way they fall so that swapping the aggregator cannot
  hide a result.

DIAGNOSTICS, DECLARED NOW SO THEY CANNOT BE MISTAKEN FOR TRIALS LATER
  Published with the result, charged no trials, and NOT promotable from this
  run whatever they show:
    * the arithmetic quantile spread and its SignalStudy verdict, above;
    * the geometric mean of the TOP (most volatile) quintile against all
      candidates. §19 found the 250-session rank's information sat entirely in
      AVOIDANCE -- both ends lost money compounding and the signal only said
      which end lost more -- and criterion 1 above, which compares the
      favoured quintile against all, is blind to exactly that shape. If this
      signal has the same shape the number will show it, and acting on it
      would need its own registration;
    * each signal's IC by sample and by half, so a pass resting on one sample
      or one half is visible rather than buried in a pooled figure.

WHAT THIS TEST CAN RESOLVE -- COMPUTED BEFORE IT RUNS
  §25 failed on this. It named a test primary for having "thousands of
  observations" when the overlap correction left 228 effective ones, and could
  not have detected the effect it was registered to look for. The same
  quantity, computed here in advance from the OUTCOME distribution of the
  existing 2010-2019 panel (volaccoos_observations_{0..3}.csv -- the forward
  returns, which are the same whatever signal is laid beside them; the
  volatility signal is not touched):

    horizon 21   n 233,232   overlap 1x   n_eff 233,232
                 forward-return sd 25.23%
                 smallest IC resolvable at the hurdle:  0.0046
    horizon 63   n 227,277   overlap 3x   n_eff  75,759
                 forward-return sd 38.28%
                 smallest IC resolvable at the hurdle:  0.0082

  The in-sample effects were -0.060 and -0.078. This test can resolve an IC
  roughly THIRTEEN TIMES SMALLER than the one it is looking for at 21 sessions
  and NINE TIMES smaller at 63. Stride 21 equals the 21-session horizon, so
  those observations do not overlap at all at the short horizon -- the
  opposite of §25's design, and the reason the numbers are this favourable.

  For the arithmetic diagnostic, a quintile spread of 0.37% (21) and 0.99%
  (63) would be resolvable IF both tails had the pooled standard deviation.
  They will not -- that is §7 -- so the realised figure will be larger and the
  run reports it. The geometric criterion's resolution is a bootstrap and is
  reported by the run rather than predicted here.

  So a null from this test is INFORMATIVE, and that is the point of computing
  this first.

IT SURVIVES AT A HORIZON ONLY IF ALL THREE HOLD
  1. The geometric mean of the BOTTOM quintile beats the geometric mean of all
     candidates in BOTH 2010-2014 and 2015-2019, bootstrap CI excluding zero
     in both halves. This is the criterion that retired pattern_quality
     (+6.45pp/yr then -1.33, §13), relative_strength (+6.85%/yr then -0.88,
     §18) and obv_trend (+8.00%/yr then -2.51, §24), each of which reversed
     sign between halves of its own window. It is not relaxed because
     volatility is the favourite.
     Two others died differently and are named so this is not read as a
     single failure mode: volume_momentum and relative_volume_20 reversed
     between IN SAMPLE and OUT OF SAMPLE rather than between halves, and the
     250-session rank failed criterion 2 for a third reason -- its favoured
     quintile was roughly the universe average, so it beat nothing to compare
     against, while its half-period behaviour was stable (+24.8 and +23.7).
  2. The Spearman IC is NEGATIVE with |t| beyond 2.2442, overlap-corrected.
  3. The IC is NEGATIVE in at least 3 of the 4 samples.

  Each signal is judged separately. A signal surviving at one horizon and not
  the other survives at that horizon and is reported that way, as §24 was.

NO TUNING
  Quantile 0.2, stride 21, horizons 21 and 63, cap 400, four samples: every
  one inherited from §19/§22/§24 rather than chosen here. The two signals are
  the two the scoreboard already names. If this fails, no third volatility
  measure, no other quantile and no other period is tried on this data.

WHAT A PASS WOULD MEAN
  That the first relationship in this project has survived its criteria -- on
  partially seen data, with the disclosure above attached. It would license a
  scoped proposition: either a volatility-aware selection rule, or a second
  reading of the equal-risk sizing result that is no longer alone. It would
  NOT license a weight. Volatility carries none today, and §12's argument for
  equal weights is about the four factors that do.

WHAT A FAIL WOULD MEAN
  That the largest and most stable relationship in the corpus does not survive
  the half-period test either, and that every signal this project has measured
  -- fifteen of them -- has now failed. That would make the equal-risk sizing
  result the only surviving evidence of anything, and would say the value in
  volatility is in RISK CONTROL rather than in SELECTION: a conclusion the
  system's architecture already separates, and one worth having in writing.
