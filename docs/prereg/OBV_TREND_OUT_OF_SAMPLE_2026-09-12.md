# Pre-registration — `obv_trend` out of sample, 2010–2019

Committed before anyone has looked at this signal's out-of-sample numbers.

```
WHY THIS IS BEING TESTED AT ALL, HAVING SAID IT WOULD NOT BE
  The §22 registration said obv_trend "IS NOT CARRIED FORWARD. It failed in
  sample ... and testing a failed signal again on new data buys nothing but a
  trial." That reasoning holds for a signal that failed NEUTRALLY. obv_trend
  did not: at 21 sessions it read IC -0.0116, t -5.60, with the WRONG sign in
  all four samples against a declared POSITIVE direction. A consistent
  negative is a relationship pointing the other way, not an absence of one --
  which is exactly the shape relative_volume_20 had, and §8 tested that out of
  sample rather than assuming it.

THE DATA ALREADY EXISTS, AND HAS NOT BEEN LOOKED AT
  The 2010-2019 run computed BOTH volume_momentum and obv_trend into
  volaccoos_observations_{0..3}.csv; only volume_momentum was passed to the
  verdict script (--signals volume_momentum). obv_trend's out-of-sample
  numbers have never been computed or read by anyone. This registration is
  committed before they are. If that ordering is ever in doubt, the git
  timestamps settle it.

SIGNAL     obv_trend from IndicatorEngine: net signed volume over 10 sessions
           as a share of the volume traded in it. Same code path, same config,
           same observations file as §22.

DIRECTION  NEGATIVE, taken from the in-sample result, as §8 took
           relative_volume_20's. The declared direction the factor's weight
           asserts is POSITIVE; the in-sample measurement contradicted it in
           four samples out of four, and the honest test is of what was
           measured rather than of what was hoped.
           The derivation is charged: 2 trials per horizon, not 1, exactly as
           SignalStudy charges Orientation.DERIVED. Ledger 35 -> 39.

PERIOD     2010-01-04 .. 2019-12-31, four disjoint samples of the 2010
           population, the universes §19 and §22 used.
HORIZONS   21 and 63 sessions    STRIDE 21    QUANTILE 0.2
HURDLE     |t| > 2.159, measured at 39 trials.

IT SURVIVES AT A HORIZON ONLY IF ALL THREE HOLD
  1. An ESTABLISHED quantile spread in the declared (negative) direction --
     past OUTLIER_DEPENDENT and SPREAD_NOT_ESTABLISHED, spread t beyond the
     hurdle.
  2. The geometric edge of the BOTTOM quintile over all candidates is positive
     in both 2010-2014 and 2015-2019. The direction is negative, so the
     bottom of the signal is the side expected to win, and testing the top
     would be testing the opposite claim.
  3. The Spearman IC is negative in at least 3 of the 4 samples.

WHAT A PASS WOULD MEAN
  That signed accumulation predicts returns INVERSELY: names being bought on
  balance underperform. It would license a proposition about the factor's
  direction, not a weight, and would say the factor is misnamed twice over --
  §21 already found the direction-blind measure was the one that worked in
  sample.

WHAT A FAIL WOULD MEAN
  That volume_accumulation has now produced three signals -- relative_volume_20,
  volume_momentum, obv_trend -- that did not survive unseen data, and the
  factor has no evidence of any kind behind its 25% weight.
```
