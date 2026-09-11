# Pre-registration — `relative_strength` by its real engine

Committed before any run on this data. Every earlier test of this factor used
stand-in price kernels (`momentum_21/126/252`); this runs
`tradeit.analytics.relative_strength.RelativeStrengthEngine` itself.

```
SIGNAL
  rs_score from RelativeStrengthEngine with the default RelativeStrengthConfig:
  lookbacks 20/60/120/250 sessions, weights 0.15/0.25/0.30/0.30,
  min_universe_for_rank 20. Relative performance against the benchmark is
  percentile-ranked on each scan date across the securities of that sample
  holding a served bar that day, per lookback, then blended by score().
  engine.compare -> rank_cross_section -> score, the engine's own path.

BENCHMARK
  QQQ, not SPY. SPY is absent from research01 (measured 2026-09-12: no
  symbol alias). A benchmark common to every security on a date divides
  every return by the same (1 + r_bench), which preserves order, so the
  cross-sectional rank -- the only thing score() reads -- cannot depend on
  which benchmark it is. Not assumed: the run verifies, on its first scan
  date, that ranks against QQQ equal ranks of raw returns, and stops if not.

DIRECTION  POSITIVE -- ScoringConfig weights the factor positively.
           Noted in advance, not acted on: 15% of the weight sits on the
           20-session lookback, whose stand-in momentum_21 had a stable
           NEGATIVE sign.

HORIZONS   21 and 63 sessions      STRIDE 21      QUANTILE 0.2

UNIVERSE   Four disjoint samples: backtest_survivorship.py --offset 0..3,
           400 survived + 400 died each, alive and >= 250 bars on
           2000-01-03, window to 2009-12-31. Each sample ranked within itself.

TRADABILITY  price_series read rule (§14), plus volume > 0 at both return
             endpoints (data dictionary §0.7).

THE FACTOR SURVIVES ONLY IF ALL THREE HOLD
  1. Pooled across samples: an ESTABLISHED quantile spread in the declared
     direction at either horizon, not OUTLIER_DEPENDENT, clearing the
     multiple-testing hurdle at 26 trials (|t| > 2.01, measured).
  2. The geometric top-quintile edge over all candidates is positive in
     BOTH 2000-2004 and 2005-2009. (§13: pattern_quality's geometric edge
     lived in one half and reversed in the other.)
  3. The Spearman IC has the declared sign in at least 3 of the 4 samples.

WHAT DOES NOT COUNT
  A significant IC with no established spread; a geometric edge in one
  sub-period; a pooled result that one sample drives.

TRIALS   2 (rs_score x 2 horizons). Ledger 24 -> 26.
         The four per-lookback percentiles are reported as diagnostics and
         are NOT trials: none of them can be promoted from this run.
```

```
AMENDMENT 1 -- 2026-09-12, after a 30-security smoke run, before any
forward return was computed or read.

WHAT WAS WRONG
  The invariance argued above is false. The engine compares a security with
  the benchmark over the security's OWN sessions -- align_series matches
  the benchmark window to it -- so a security with gaps is measured against
  the benchmark's return over a different calendar span from its
  neighbours'. The benchmark then enters each rank differently, and the
  choice of benchmark CAN move it. The run's own check found it: on
  2000-12-29, lookback 20, 4 of ~30 securities ranked differently against
  QQQ than by the engine's own aligned security_return.

  (A first version of the script also truncated gappy securities' windows
  by giving the benchmark only 291 sessions. Fixed before this amendment;
  the 4 differ with the fix in place, which is how the real cause was
  isolated.)

WHAT CHANGES
  rs_score is recorded under two benchmarks, both through the engine:
    QQQ    -- the configured benchmark the corpus holds;
    FLAT   -- a constant series on the same calendar, under which relative
              performance reduces to each security's own aligned return.
  Their agreement is reported (per-date rank correlation, and how many
  observations change quintile).

  THE SURVIVAL CRITERIA MUST HOLD UNDER BOTH. If they hold under one and
  fail under the other, the result depends on the benchmark: no verdict
  is issued, and SPY must be obtained first.

TRIALS  Unchanged at 2. Requiring both is stricter than either alone, so it
        buys no extra chance of a false pass.
```
