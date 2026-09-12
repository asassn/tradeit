# Pre-registration — `volume_momentum` out of sample, 2010–2019

Committed before any 2010s run of this signal. §21 recorded the first signal in
this project to pass all three registered criteria in sample:
`ECONOMICALLY_USEFUL` at both horizons, every sample agreeing, +32.7%/yr net at
21 sessions. **`relative_volume_20` did the same and then died out of sample.**
This is the test that tells the two apart.

```
SIGNAL
  volume_momentum, from IndicatorEngine under the default IndicatorConfig:
  ROC(SMA(volume, 20), 20). The same code path as §21, the same script, the
  same config -- only --start, --end and --history-from differ.

DIRECTION  POSITIVE, taken from the in-sample result, which is what makes this
           one trial per horizon rather than two. It is the direction §21
           measured and the one the factor's weight asserts.

PERIOD     2010-01-04 .. 2019-12-31. Trailing history from 2009 for the
           warm-up only; no outcome before the start is used.

WHAT IS AND IS NOT UNSEEN -- stated precisely
  volume_momentum has NEVER been computed on this period. It is not one of
  the kernels signal_research.py computes, so the relative_volume_20
  out-of-sample run over 2010-2024 did not calculate it even internally.
  But the PERIOD is not unseen: that run, §19's rs250 test, the
  sector_strength decade split and the volatility-sizing backtest have all
  read 2010s returns for other signals. And 1,019 of the 3,200 securities
  here also appear in the in-sample study -- the same companies in a
  different decade, not 3,200 new names.

UNIVERSE   Four disjoint samples, backtest_survivorship._arms, cap 400:
           400 survived + 400 died each, alive and >= 250 bars on 2010-01-04
           (population 2,914 survived, 2,050 died; both arms verified
           pairwise disjoint). The universes §19 used.

HORIZONS 21 and 63 sessions   STRIDE 21   QUANTILE 0.2
TRADABILITY  price_series read rule; volume > 0 at both return endpoints.

obv_trend IS NOT CARRIED FORWARD. It failed in sample -- wrong sign in all
four samples at 21 sessions -- and testing a failed signal again on new data
buys nothing but a trial.

IT SURVIVES AT A HORIZON ONLY IF ALL THREE HOLD
  1. An ESTABLISHED quantile spread in the declared direction -- past
     OUTLIER_DEPENDENT and SPREAD_NOT_ESTABLISHED -- with spread t above the
     hurdle at 35 trials (|t| > 2.136, measured).
  2. The geometric top-quintile edge over all candidates is positive in BOTH
     2010-2014 and 2015-2019.
  3. The Spearman IC has the declared sign in at least 3 of the 4 samples.

REPORTED, NOT A CRITERION
  The in-sample and out-of-sample effect sizes side by side. A signal that
  replicates its sign at a tenth of its magnitude has replicated something;
  a signal that reverses has not. relative_volume_20 went from IC -0.069 to
  +0.009; the 250-session rank went from t +6.46 to t +16.16 and still failed
  its criteria. Both shapes are on the record, and this will be read against
  them.

WHAT A PASS WOULD MEAN
  The first signal in this project to survive in and out of sample. It would
  license two scoped propositions -- to rename the factor to what it measures,
  since §21 found the signed measure fails while the direction-blind one
  works, and to consider its weight -- and it would license neither
  automatically. It would not be evidence of profitability while the corpus
  gate reads SURVIVOR_BIASED.

WHAT A FAIL WOULD MEAN
  That volume_accumulation's only passing signal joins relative_volume_20,
  and that all four weighted factors have now failed out of sample or failed
  in it. The weights would stay equal, for the fourth time, on evidence.

TRIALS  2 (one signal x two horizons). Ledger 33 -> 35.
```
