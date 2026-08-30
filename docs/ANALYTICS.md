# Analytics Methodology

The causal feature layer. Everything here computes from clock-gated inputs and
produces values that carry the definition that made them.

**Companion documents:** [Architecture](ARCHITECTURE.md) ·
[Data model](DATA_MODEL.md) · [Phase 3 report](PHASE_03.md) ·
[ADR-0010](adr/0010-own-indicator-kernels.md) ·
[ADR-0011](adr/0011-point-in-time-cross-sectional-ranking.md) ·
[ADR-0012](adr/0012-transparent-rule-based-regime.md)

---

## The two rules everything obeys

**Causality.** The value at bar *t* uses bars ≤ *t* and nothing else. Formally:
computing a feature over `x[:k]` reproduces exactly the first *k* outputs of
computing it over all of `x`. This is the property that rules out centred
windows, full-sample normalisation, backward-filling interpolation, and the
accidental `shift(-1)` — all of which produce plausible numbers and a backtest
that cannot be repeated live. 274 automated cases enforce it.

**Honest warm-up.** A feature returns `None` until it has enough history, never
a number computed from a short window. A 200-day average of 40 bars is a
different indicator with the same name, and it differs precisely in the region
where instruments enter the universe.

---

## 1. Indicator definitions

All periods come from `StrategyConfig.indicators`; the defaults below are the
shipped `baseline.toml` values. "Warm-up" is the number of bars before the first
defined value.

### Moving averages

| Indicator | Formula | Warm-up | Notes |
|---|---|---|---|
| `sma_N` | mean of last *N* closes | *N* | Cumulative-sum sliding window, O(n) |
| `ema_N` | `α·xₜ + (1−α)·EMAₜ₋₁`, `α = 2/(N+1)` | *N* | **Seeded with SMA of the first *N***, not the first value |
| Wilder smoothing | `(Sₜ₋₁·(N−1) + xₜ)/N` | *N* | `1/N`, not `2/(N+1)` — RSI, ATR and ADX are defined on this |

Defaults: SMA 10/20/50/150/200, EMA 8/21/50.

The EMA seeding choice is the single place implementations most often diverge,
and it changes every subsequent value permanently. It is stated here and tested.

### Momentum and oscillators

| Indicator | Formula | Warm-up | Notes |
|---|---|---|---|
| `rsi_14` | `100 − 100/(1+RS)`, `RS = wilder(gains)/wilder(losses)` | 15 | 100 when average loss is zero; 50 on a flat series |
| `macd_line` | `EMA₁₂ − EMA₂₆` | 26 | |
| `macd_signal` | `EMA₉` of the MACD line | 34 | **Warm-up stacks**: seeded from the first 9 *defined* MACD values |
| `macd_histogram` | line − signal | 34 | |
| `roc_N`, `momentum_N` | `xₜ/xₜ₋ₙ − 1` | *N* | ROC 5/20/60; momentum 20/60/120/250 |

A signal line emitted before the MACD line has nine defined values is computed
from too little data, and the crossings it produces are fictional.

### Volatility and range

| Indicator | Formula | Warm-up | Notes |
|---|---|---|---|
| `true_range` | `max(H−L, \|H−Cₜ₋₁\|, \|L−Cₜ₋₁\|)` | 2 | First bar is `NaN`, not `H−L` — that biases the first ATR low |
| `atr_14` | Wilder smoothing of true range | 15 | |
| `atr_percent` | `ATR / close` | 15 | Scale-free; raw ATR is not comparable across instruments |
| `realized_volatility_N` | `stdev(log returns over N) · √252` | *N*+1 | Log returns, so √time annualisation is valid. Defaults 20, 60 |
| `bollinger_*` | `SMA₂₀ ± 2σ` | 20 | Population σ (`ddof=0`), per Bollinger's definition |
| `bollinger_bandwidth` | `(upper − lower) / middle` | 20 | The scale-free contraction measure downstream actually uses |
| `adx_14` | Wilder ADX from smoothed DX | **29** | Double smoothing: warm-up is ≈2×period, not period |
| `plus_di`, `minus_di` | `100 · wilder(±DM)/wilder(TR)` | 15 | |
| `volatility_percentile` | rank of current realised vol in its own trailing 252 | 272 | *Time-series* percentile, not cross-sectional |

### Volume

| Indicator | Formula | Warm-up | Notes |
|---|---|---|---|
| `relative_volume` | `volumeₜ / mean(volume over prior 20)` | 21 | **Excludes the current bar from its own baseline** |
| `obv` | cumulative `sign(Δclose) · volume` | 1 | Level is arbitrary; only slope carries information |
| `obv_slope` | per-bar slope of OBV | 11 | The usable form of OBV |
| `avg_dollar_volume_20` | `SMA₂₀(typical price · volume)` | 20 | Typical price, not close: close×volume misstates turnover on trend days |
| `vwap_20` | `Σ(typical·vol)/Σ(vol)` over 20 bars | 20 | Rolling, **not** intraday session VWAP — see below |
| `volume_momentum` | ROC of average volume | 40 | Accumulation building or fading |

Self-inclusion in relative volume damps exactly the signal being measured: on a
5× volume day, including today pulls the ratio toward 4.2× for a 20-day window.

**On VWAP.** True VWAP is intraday and session-anchored. On daily bars the
honest approximation is a rolling volume-weighted typical price, which is a
useful reference level but is *not* what a trader sees on an intraday chart.
`session_vwap()` computes genuine anchored VWAP when intraday bars exist; it
resets at each session boundary rather than running across the overnight gap.

### Structure

| Indicator | Formula | Warm-up | Notes |
|---|---|---|---|
| `rolling_high_N` / `rolling_low_N` | max/min over trailing *N* | *N* | Defaults 20, 52, 252 |
| `distance_from_high_N` | `close/high_N − 1` | *N* | 0 = at a new high; negative = below |
| `distance_from_sma_N` | `close/SMA_N − 1` | *N* | |
| `sma_20_slope` | `(MAₜ/MAₜ₋₁₀ − 1)/10` | 30 | Normalised by level and lookback, so comparable across price levels |
| `range_contraction` | `SMA₁₀(H−L) / SMA₅₀(H−L)` | 50 | Below 1 = quieter |
| `atr_contraction` | `SMA₁₀(ATR) / SMA₅₀(ATR)` | 64 | |
| `volume_contraction` | `SMA₁₀(vol) / SMA₅₀(vol)` | 50 | Volume dry-up during a base |
| `gap_frequency` | share of last 20 bars opening beyond 2% | 21 | Input to the volatility regime |

The three contraction ratios are the scale-free signature of a consolidating
base, and are the primary inputs Phase 4's pattern detectors will consume.

**58 features total**, with a maximum warm-up of **272 sessions**.

---

## 2. Multi-timeframe methodology

Higher timeframes are **aggregated from daily bars**, never fetched separately.
That makes the weekly bar consistent with its constituents by construction, and
— more importantly — makes *completeness* something the exchange calendar
determines rather than something a vendor asserts.

### The partial-candle rule

At Wednesday noon the current week's bar exists: it has an open, a running high,
a running low, and a last price. It is also wrong to use as a feature, because
on Friday it will be a different bar. A system that treats it as complete has a
"weekly close" that will change twice more before the week ends — which is
reading Thursday's and Friday's prices on Wednesday.

So: **a higher-timeframe bar is emitted as a feature only once the calendar says
its final constituent session has closed.**

```
Mon  Tue  Wed  │  Thu  Fri
 10   20   30  │   40   50
               │
   as_of = Wed │  weekly bar: complete=False  →  NOT a feature
   as_of = Fri │  weekly bar: complete=True, close=50
```

Incomplete bars are still *constructed* — a live dashboard legitimately shows
the week in progress — but `resample_for_feature_use()` filters them, and
`to_ohlcv_bars()` refuses to convert one, because the result would be
indistinguishable from a finished bar.

### Completeness is a calendar question

A period is complete when its last *scheduled* trading session is at or before
`as_of`. The data alone cannot distinguish a finished week from a week missing
its Friday, and treating the second as finished produces a weekly close that
later changes.

Worked examples, both tested:

- **Good Friday 2024-03-29.** That week's last session is Thursday the 28th. A
  rule waiting for Friday leaves the week permanently provisional and the weekly
  feature silently stale.
- **March 2024.** The month's last session is also the 28th (the 29th is Good
  Friday, the 30th–31st the weekend). A rule keyed on the calendar month-end
  never completes it.

ISO weeks are used, so a week spanning New Year does not split.

### Alignment into daily features

`align_to_daily()` maps each daily session to the most recent *completed*
higher-timeframe bar. Strictly backward-looking: every day of week 2 sees
week 1's bar, not its own partial week. On Monday of a new week the answer is
still the previous week — which is what a live system would have had.

### Intraday

Buckets are anchored to the **session open**, not the wall clock. A 09:30 open
produces 09:30 / 10:30 / 11:30 hourly bars; clock-aligned resampling would make
the first bar of every session 30 minutes long and the rest 60, which are not
comparable.

The final bucket of a session is usually short — 6.5 hours does not divide into
4-hour buckets — and it is still **complete** once the session closes: it is a
real bar covering the time the market was open. An early close (13:00 ET the day
after Thanksgiving) truncates it further, legitimately. Only a bucket whose
session is still running is incomplete.

Timeframes: 5m, 15m, 30m, 1h, 4h from minute bars; 1w and 1mo from daily. No
intraday data is currently ingested; the logic is implemented and tested against
synthetic minute bars.

---

## 3. Relative strength methodology

**Not RSI.** RSI is a momentum oscillator on one series and lives in the
indicator engine. Relative strength here is a market-structure measure.

### Benchmark-relative

Against **SPY, QQQ and IWM** by default (configurable), over **20, 60, 120 and
250** sessions. Each pair produces:

| Measure | Formula | What it answers |
|---|---|---|
| `relative_performance` | `(1+r_stock)/(1+r_bench) − 1` | Compounding-correct: what a relative position earned |
| `excess_return` | `r_stock − r_bench` | Simple difference, in return points |
| `relative_trend` | per-bar drift of the ratio line | Is outperformance building or fading? |
| `relative_momentum` | ratio now vs ratio at the window midpoint | Recent acceleration |

The first two disagree in the cases that matter. Benchmark −20%, stock −10%:
relative performance is **+12.5%**, excess return is **+10 points**. Both are
stored; neither is derived on demand.

Multiple benchmarks are kept separate rather than blended. A name beating SPY
while lagging QQQ is strong against the market and weak against its peer group,
and collapsing that early makes it unrecoverable.

A lookback of *N* spans *N+1* closes. Series are intersected on shared session
dates before comparison, so a stock that halted for three days is not compared
against the index's other days.

### Cross-sectional

Percentile rank within the **point-in-time eligible universe** — the roster that
was in the universe on that date, including companies that later delisted.

This is the part most easily got wrong. Ranking against today's database
contents silently excludes the eventual casualties, who are disproportionately
poor performers. The resulting rank is a different statistic from the one it
claims to be, and nothing in the output reveals it.

So the roster is a **required argument**, and the engine raises if handed a
value for an instrument outside it. A test measures the effect: the same
instrument on the same date moves more than 10 percentile points depending on
whether the casualties are included.

Ranks are also computed within **sector** and **industry** groups, using the
classification as it stood on that date. An unclassified instrument gets `None`
rather than an "Other" bucket, which would distort that group and hide the gap.

**No rank below a minimum peer count** — 20 for the universe, 5 for a sector. A
percentile over four names is a number, not a rank.

### RS score (0–100)

Weighted blend of universe percentiles across lookbacks:

```
20d: 0.15    60d: 0.25    120d: 0.30    250d: 0.30
```

Longer horizons dominate: a name that has led for a year is a stronger statement
than one that led for a month. Missing lookbacks are dropped and the remaining
weights renormalised, so a young instrument is scored on what exists rather than
receiving a null. The registry records the warm-up so a screen can decide
whether to trust a short-history score.

---

## 4. Sector strength methodology

Per sector, per date, from constituents classified **on that date**:

| Factor | Default weight |
|---|---|
| Relative return vs market | 0.25 |
| Relative momentum | 0.20 |
| Breadth above 50DMA | 0.20 |
| Absolute return | 0.15 |
| Breadth above 200DMA | 0.10 |
| Participation | 0.10 |

Each factor is mapped to [0, 1] by a transparent, stated rule (e.g. a +10%
relative return over the window maps to 1.0), then blended and scaled to 0–100.
Weights renormalise over available factors, so a missing input reduces coverage
rather than silently scoring zero. **None of these mappings was tuned on
historical returns.**

Also produced: sector rank, trend (`improving` / `stable` / `deteriorating`),
breadth status (`strong` / `mixed` / `weak`), and participation status
(`broad` / `moderate` / `narrow`).

The participation distinction matters: a sector can post a strong return with a
quarter of its members participating or with three quarters, and those are
materially different signals. `leadership_participation` — the share at or near
52-week highs — separates a broad advance from three large names carrying an
index.

**Sectors below `min_members` (5) are not scored.** Three constituents is
arithmetic, not a measure of the sector.

### The classification problem, and the honest fallback

Historical GICS constituent data requires a commercial vendor. Until one is
licensed:

- Sector strength can be measured from the **SPDR sector ETF's own price
  history**, which is a legitimate point-in-time series.
- Results carry `source = "etf_proxy"` and a note, so nothing downstream
  mistakes them for a constituent aggregate.
- What the engine will **not** do is apply today's constituent list to 2015 and
  present the result as historical sector strength.

Default proxies: XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLRE, XLC —
configurable.

---

## 5. Breadth methodology

Every breadth measure records **the roster it was computed over**.
`universe_size` and `universe_digest` are part of the measurement, not metadata.

The failure this prevents: compute "percent above the 200DMA" for 2008 using
today's index constituents and you have computed it over the companies that
*survived* 2008. The number comes out far too high, the bear market looks mild
in the breadth series, and any regime model calibrated on it fails exactly when
it matters.

Measures: advances, declines, unchanged; advance/decline spread and cumulative
A/D line; advancing versus declining volume; percent above the 20/50/200DMA; new
52-week highs and lows and their spread; thrust (mean advancing share over 10
sessions); participation.

Details that carry weight:

- **Instruments outside the roster are refused**, not ignored — otherwise the
  recorded universe and the computed value disagree.
- **Percent-above-MA counts only instruments with a defined average.** A young
  instrument with no 200DMA must not count as "below" it.
- **Instruments in the roster with no data are reported**, not silently dropped:
  `evaluated` versus `universe_size`, with a note.
- **Small universes are flagged** `low_confidence` rather than trusted, so "40%
  above their 200DMA" out of 12 names is distinguishable from the same figure
  out of 4,000.
- The **A/D line level is arbitrary** — like OBV, it depends on where the series
  starts. Only its slope and divergence from price carry information.

---

## 6. Market regime methodology

Six states, from a weighted sum of seven named signals each scoring in [−1, +1]:

```
STRONG_BULL   ≥  0.60
BULL          ≥  0.25
NEUTRAL       ≥ -0.10
WEAK          ≥ -0.35
BEAR          ≥ -0.65
SEVERE_RISK_OFF — override, not a band
```

| Signal | Weight | Measures |
|---|---|---|
| `primary_trend` | 0.30 | SPY vs 50/200DMA and their ordering |
| `benchmark_agreement` | 0.15 | Do SPY, QQQ and IWM agree? |
| `ma_slope` | 0.15 | Is the trend improving or rolling over? |
| `breadth_above_200dma` | 0.15 | 50% neutral, 80% firmly bullish |
| `new_high_low` | 0.10 | Highs vs lows, normalised |
| `sector_participation` | 0.10 | Sectors above their 50DMA |
| `volatility` | 0.05 | Volatility regime |

`ma_slope` earns its place: a market above a *falling* 200-day average is a
different state from one above a rising average, and price-versus-average alone
cannot see it.

### SEVERE_RISK_OFF

An override rather than a band. Extreme volatility **with a negative composite**
is a different operating environment from an ordinary bear — one where sizing
should shrink regardless of what the trend says. Extreme volatility with a
*positive* composite is not risk-off: 2020 H2 was volatile and rising. Both
cases are tested.

### Explainability

Every classification records supporting **and** contradicting evidence, in the
format the brief specifies:

```
Market Regime: STRONG_BULL
Confidence: 92
Supporting Evidence:
  SPY above 200DMA, above 50DMA, averages stacked bullishly
  all benchmarks above 200DMA
  SPY 50DMA rising (0.120%/session)
  68% of 3900 eligible above their 200DMA
  new highs (180) exceed new lows (40)
  8 of 11 sectors above their 50DMA
  volatility regime NORMAL
Contradicting Evidence:
  IWM below 50DMA
  IWM 50DMA falling
```

That last part is deliberate. The aggregate agreement signal scores the
*balance* across benchmarks, so a single lagging index can be outvoted and
vanish from the output — which is exactly what a reader needs to see. Per-
benchmark divergences are appended as unscored observations: they enrich the
explanation without touching the composite, and `reconciles` still verifies the
signals sum to the score.

**Confidence** falls near band edges (0.251 against a 0.25 threshold is a coin
flip), when signals disagree, when few signals are computable, and with each
divergence. A regime label that says it is uncertain is far more useful than one
that does not.

**Thresholds were not fitted to historical returns.** Phase 9 tests them
out-of-sample; that is where tuning belongs.

---

## 7. Volatility regime methodology

Five states from **percentiles of the market's own trailing volatility**, not
absolute levels:

```
LOW       < 20th percentile
NORMAL    < 60th
ELEVATED  < 80th
HIGH      < 95th
EXTREME   ≥ 95th
```

Percentile-based because 20% annualised volatility meant something very
different in 2017 than in 2020. A fixed threshold silently reclassifies the
whole market when the volatility level shifts, and a model calibrated on one era
misfires in the next.

Inputs: realised volatility (20-session, annualised), its percentile over 252
sessions, ATR percent, volatility expansion (recent versus baseline), gap
frequency, and cross-sectional dispersion.

**No percentile, no regime.** With insufficient history the result is `UNKNOWN`
with confidence 0, not a guess from absolute ATR — an unreliable regime label is
worse than an absent one, because downstream code will act on it.

**Cross-sectional dispersion is a distinct state.** High dispersion with low
index volatility means constituents are moving and cancelling out; the index is
calm for a reason that will not persist. It is recorded as contradicting
evidence when the headline reading says LOW or NORMAL.

Phase 3 computes and explains the regime. It deliberately does **not** act on
it — position sizing, breakout quality and exposure limits are Phases 5 and 8,
and wiring them here would put trading behaviour in the analytics layer.

---

## 8. Feature registry

Every feature carries the metadata needed to reproduce, interpret and version
it:

```python
FeatureSpec(
    name="rs_universe_percentile_120",
    version=1, calculation_version=1,
    kind=FeatureKind.CROSS_SECTIONAL,       # leaks across instruments if misused
    timeframe=Bartimeframe.D1,
    output_type=OutputType.PERCENTILE_0_1,
    null_behaviour=NullBehaviour.WARMUP,    # what a null MEANS
    input_datasets=("ohlcv_bars", "corporate_actions", "universe_memberships"),
    warmup_periods=121,
    parameters={"lookback": 120, "universe": "point_in_time"},
)
```

Three things this provides that a naming convention cannot:

**A content-addressed feature-set digest.** The active definitions hash to one
digest, stored on every value row. A changed definition produces a different
digest, so recomputed values live alongside the old ones instead of silently
overwriting them.

**Machine-checkable availability.** `warmup_periods` says when a feature is
computable at all; `registry.max_warmup` is the history a backtest must skip
before trading. Beginning on day one with half the features null does not
measure the strategy.

**Null semantics.** `WARMUP` / `NOT_APPLICABLE` / `MISSING_INPUT` / `UNDEFINED`.
A 200-day average null because the instrument listed forty days ago is a
different fact from one null because the vendor dropped a session — and imputing
zero for either turns missing data into a strong signal.

The registry validates itself: dangling dependencies, cycles, and a feature
whose warm-up is shorter than something it depends on (which would compute from
undefined inputs and look like a data problem rather than a definition problem).

`FeatureRegistry.diff()` reports added, removed and changed definitions between
two registries — the review tool for a feature change.

---

## 9. What Phase 3 deliberately does not do

No pattern recognition, no breakout detection, no trade logic, no sizing, no
portfolio construction. Phase 3 produces the reusable features those systems
consume:

| Phase 4 will use | From |
|---|---|
| Rolling highs/lows, distance-from-high | Indicator engine |
| Range / ATR / volume contraction | Indicator engine |
| Moving-average structure and slopes | Indicator engine |
| Multi-timeframe alignment | Timeframe engine |

| Phase 5 will use | From |
|---|---|
| Relative volume | Indicator engine |
| Volatility regime | Volatility engine |
| Market regime | Regime engine |
| Relative strength | RS engine |
