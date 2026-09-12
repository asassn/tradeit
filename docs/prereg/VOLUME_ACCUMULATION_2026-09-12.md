# Pre-registration — `volume_accumulation` by its real engine

Committed before any run. The last weighted scoring factor never measured
directly. Its stand-in `relative_volume_20` passed in sample and was rejected
out of sample (§8 of `SIGNAL_RESEARCH_01.md`); the factor itself has not been
tested.

```
WHAT THE REAL ENGINE IS -- AND WHAT IT IS NOT
  Unlike relative_strength, this factor has NO engine of its own. There is no
  module, no scorer, and nothing in the codebase consumes the name
  "volume_accumulation" except the weights dict. What exists is the indicator
  registry, and two of its features are accumulation claims in their own
  words:

    volume_momentum  ROC(SMA(volume, 20), 20) -- registered as "change in
                     average volume: accumulation building or fading"
    obv_trend        net signed volume over 10 sessions as a share of the
                     volume traded in it -- the usable, signed form of OBV

  Both are computed by IndicatorEngine itself, not reimplemented here.

  obv_trend exists as of 920f703. The registry previously published
  obv_slope = slope(abs(obv) + 1, lookback), which returned +0.052632 for a
  security closing up every session and for one closing down every session
  alike. That defect was found while looking for this factor's engine, and is
  fixed before measuring rather than measured around.

  NOT TESTED HERE: relative_volume (rejected out of sample already; retesting
  it would be a second bite), and the volume components inside pattern
  detectors and breakout scoring, which belong to pattern_quality (§13) and to
  the breakout gate (§11) and were measured with them.

SIGNALS AND DIRECTIONS, DECLARED
  volume_momentum  POSITIVE    obv_trend  POSITIVE
  The factor carries positive weight, which asserts that accumulation makes a
  better candidate; that assertion is what is being tested.
  Stated in advance: volume_momentum is direction-blind -- it rises when
  volume rises, whether the volume is buying or selling -- so a null for it
  is weaker evidence against the *idea* of accumulation than a null for
  obv_trend, which carries the sign.

PERIOD     2000-01-04 .. 2009-12-31, the period pattern_quality (§13) and
           relative_strength (§18) were measured on, so the three measured
           factors are comparable. Trailing history from 1999 for warm-up
           only; no outcome before the start is used.

UNIVERSE   Four disjoint samples, backtest_survivorship._arms, cap 400:
           400 survived + 400 died each, alive and >= 250 bars on the start
           date. The same universes §18 used.

HORIZONS 21 and 63 sessions   STRIDE 21   QUANTILE 0.2
TRADABILITY  price_series read rule; volume > 0 at both return endpoints.

SCALE INVARIANCE  Both signals are ratios of volumes, or of signed volume to
           volume, so a split adjustment -- which divides price and multiplies
           volume -- leaves them unchanged. For obv_trend that is a test, not
           an argument.

A SIGNAL SURVIVES AT A HORIZON ONLY IF ALL THREE HOLD
  1. Pooled: an ESTABLISHED quantile spread in the declared direction -- past
     OUTLIER_DEPENDENT and SPREAD_NOT_ESTABLISHED -- with spread t above the
     hurdle at 33 trials (|t| > 2.112, measured).
  2. The geometric top-quintile edge over all candidates is positive in BOTH
     2000-2004 and 2005-2009.
  3. The Spearman IC has the declared sign in at least 3 of the 4 samples.

WHAT DOES NOT COUNT
  A significant IC with no established spread. That is what pattern_quality
  and the relative_strength composite both produced, and it licensed nothing.

TRIALS  4 (two signals x two horizons). Ledger 29 -> 33.
```
