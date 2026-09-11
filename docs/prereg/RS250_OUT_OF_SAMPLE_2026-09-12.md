# Pre-registration — the 250-session relative-strength rank, out of sample

Committed before any run on 2010–2019. The hypothesis comes from a diagnostic
in §18 of the scoreboard: inside `RelativeStrengthEngine`, the 250-session
lookback's percentile alone had IC +0.0189 (t +8.49) at 21 sessions and +0.0252
(t +6.46) at 63, on 2000–2009. That was **seen after the fact in a run registered
for something else**, so it earned no standing there; this is the test that can
give it some.

```
SIGNAL
  pct_250: the percentile RelativeStrengthEngine.rank_cross_section assigns a
  security's 250-session relative performance on each scan date, ranked
  across the securities of its sample holding a served bar that day
  (min_universe_for_rank 20). The engine's own compare -> rank_cross_section
  path, the same code as §18; only the lookback it is read at is chosen.
  NOT the blended rs_score.

DIRECTION  POSITIVE -- the in-sample diagnostic's sign, and twelve-month
           momentum's in the literature. Stated in advance: the stand-in
           momentum_252 flipped sign across the four in-sample specifications
           in SIGNAL_RESEARCH_01, so the in-sample record for this idea is
           itself mixed.

PERIOD     Scan dates 2010-01-04 .. 2019-12-31, forward returns ending no
           later than 2019-12-31. Trailing history from 2009 is loaded for the
           250-session warm-up only; no 2009 outcome is used.

WHY THIS PERIOD IS OUT OF SAMPLE
  No momentum or relative-strength result has been read on it: the
  scoreboard records momentum_252 out-of-sample as "not run". One
  disclosure: the relative_volume_20 out-of-sample run over 2010-2024
  computed the momentum kernels internally, but by design (--only) never
  printed or read them. sector_strength and the volatility-sizing backtest
  have read 2010s data, for different signals.

UNIVERSE   Four disjoint samples via backtest_survivorship._arms with
           start 2010-01-04, end 2019-12-31, --offset 0..3, 400 survived +
           400 died each (population: 2,914 survived, 2,050 died; both arms
           verified pairwise disjoint). 1,019 of the 3,200 securities also
           appear in the 2000s study -- same companies, different decade.

BENCHMARKS QQQ and FLAT, both through the engine, as in §18 amendment 1.
           The verdict must hold under both.

HORIZONS 21 and 63 sessions   STRIDE 21   QUANTILE 0.2
TRADABILITY  price_series read rule; volume > 0 at both return endpoints.

THE SIGNAL SURVIVES AT A HORIZON ONLY IF ALL THREE HOLD, UNDER BOTH BENCHMARKS
  1. Pooled: an ESTABLISHED quantile spread in the declared direction -- past
     OUTLIER_DEPENDENT and SPREAD_NOT_ESTABLISHED, spread t above the hurdle
     at 28 trials (|t| > 2.045, measured).
  2. The geometric top-quintile edge over all candidates is positive in BOTH
     2010-2014 and 2015-2019.
  3. The Spearman IC has the declared sign in at least 3 of the 4 samples.
  It survives overall if it survives at either horizon.

WHAT DOES NOT COUNT
  A significant IC with no established spread -- which is exactly what §18's
  composite produced at 63 sessions, and what did not license anything there.

WHAT A PASS WOULD AND WOULD NOT MEAN
  A pass makes pct_250 the first signal in this project to survive an
  out-of-sample test. It would support a proposition to reweight the RS
  engine's lookbacks; it would not make one. It would not be evidence of
  profitability while the corpus gate reads SURVIVOR_BIASED.

TRIALS   2 (pct_250 x 2 horizons). Ledger 26 -> 28.
```
