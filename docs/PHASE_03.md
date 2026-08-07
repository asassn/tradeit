# Phase 3 — Market Analytics Foundation

**Status:** Complete · **Date:** 2026-08-07 · **Roadmap:** [canonical](ROADMAP.md)

The causal analytical feature layer that pattern recognition, breakout analysis,
fundamentals, scoring, portfolio construction and backtesting will build on.

**Companion documents:** [Analytics methodology](ANALYTICS.md) ·
[Vendor evaluation](VENDOR_EVALUATION.md) · [Data model](DATA_MODEL.md) ·
[ADR-0010](adr/0010-own-indicator-kernels.md) ·
[ADR-0011](adr/0011-point-in-time-cross-sectional-ranking.md) ·
[ADR-0012](adr/0012-transparent-rule-based-regime.md)

---

## 1. Architecture decisions

### 1.1 Own indicator kernels rather than a library — [ADR-0010](adr/0010-own-indicator-kernels.md)

The three properties this platform depends on are exactly the ones libraries
differ on and rarely document: EMA seeding (first-value vs SMA — changes every
subsequent value forever), Wilder's `1/n` versus a standard EMA's `2/(n+1)` for
RSI/ATR/ADX, and whether warm-up emits `NaN` or a number from too little data.
A fourth possibility is worse: `center=True` is one keystroke in pandas and is a
straightforward look-ahead leak.

Verifying those for a dependency means reading its source and pinning its
version — most of the work of writing the recursion, without being able to test
it as a first-class artifact. The kernels are under 600 lines including
documentation, and owning them is what makes the causality property test
possible at all.

### 1.2 Multi-benchmark relative strength with an explicit roster — [ADR-0011](adr/0011-point-in-time-cross-sectional-ranking.md)

Securities are compared against SPY, QQQ and IWM separately rather than blended:
a name beating SPY while lagging QQQ is strong against the market and weak
against its peer group, and collapsing that early makes it unrecoverable.

The ranking roster is a **required argument**, and the engine raises if handed a
value for an instrument outside it. Making it a parameter means the survivorship
question is asked visibly at every call site rather than answered implicitly by
whatever the database contains.

### 1.3 Transparent, rule-based regime — [ADR-0012](adr/0012-transparent-rule-based-regime.md)

A weighted sum of seven named signals, with supporting **and** contradicting
evidence recorded. There are perhaps thirty genuine regime changes in the modern
record — not enough to fit anything with meaningful degrees of freedom — and any
model fitted on 2000–2024 has seen every crash in its test set.

### 1.4 Kernels are pure; the clock lives above them

`tradeit.analytics.kernels` takes NumPy arrays and knows nothing about dates,
clocks or databases. That is what makes prefix-consistency testable: there is no
hidden state through which a future value could arrive. Point-in-time
enforcement stays where Phase 1 put it — in the repositories that assemble the
input series.

### 1.5 Volatility regime is separate from market regime

A market can trend strongly with elevated volatility (2020 H2) or drift with low
volatility (2015). One row carrying both would force a single confidence number
for two independent judgements.

---

## 2. Indicator definitions and formulas

**58 features**, full formula table in
[ANALYTICS.md §1](ANALYTICS.md#1-indicator-definitions). Every period comes from
`StrategyConfig.indicators`; none is defaulted in a signature (ADR-0008).

Choices that would otherwise be invisible, all stated and tested:

| Choice | Made | Why it matters |
|---|---|---|
| EMA seed | SMA of the first *N* | Changes every subsequent value; a backtest and a live system seeded differently disagree by an amount that looks like noise |
| RSI/ATR/ADX smoothing | Wilder's `1/N` | Several libraries substitute `2/(N+1)`; both look plausible on a chart |
| ADX warm-up | `2·period + 1` | Double smoothing — treating it as one period exposes converging values |
| MACD signal warm-up | Stacks on the MACD line | A signal emitted before 9 defined MACD values produces fictional crossings |
| True range, first bar | `NaN` | Using `H−L` biases the first ATR low |
| Relative volume baseline | Excludes the current bar | Self-inclusion pulls a 5× day toward 4.2× for a 20-day window |
| Dollar volume price | Typical, not close | Close×volume misstates turnover on trend days |
| Bollinger σ | Population (`ddof=0`) | Bollinger's original definition |
| Realised volatility | Log returns | Additive across time, so √252 annualisation is valid |
| Slope normalisation | By level and lookback | A $20 and a $2,000 stock rising at the same rate get the same number |

**On VWAP.** True VWAP is intraday and session-anchored. On daily bars the
honest approximation is a rolling volume-weighted typical price — a useful
reference level, explicitly *not* what a trader sees on an intraday chart.
`session_vwap()` computes genuine anchored VWAP where intraday data exists.

---

## 3. Multi-timeframe methodology

Higher timeframes are aggregated from daily bars, never fetched separately, so
completeness is a **calendar** question rather than a vendor assertion.

**The rule:** a higher-timeframe bar becomes a feature only once the calendar
says its final constituent session has closed. At Wednesday noon the week's bar
exists and is wrong to use — on Friday it will be a different bar, so using it
reads Thursday's and Friday's prices on Wednesday.

Incomplete bars are constructed (a dashboard wants them) but
`resample_for_feature_use()` filters them and `to_ohlcv_bars()` refuses to
convert one, since the result would be indistinguishable from a finished bar.

Two worked cases, both tested:

- **Good Friday 2024-03-29** — that week ends Thursday the 28th. A rule waiting
  for Friday leaves the week permanently provisional and the feature silently
  stale.
- **March 2024** — the month also ends on the 28th. A rule keyed on the calendar
  month-end never completes it.

Intraday buckets anchor to the **session open**, not the wall clock: a 09:30
open gives 09:30/10:30/11:30 hourly bars. The short final bucket (6.5 hours does
not divide into four-hour buckets; an early close shortens it further) is
**complete** once the session closes — it is a real bar covering the time the
market was open.

Full detail: [ANALYTICS.md §2](ANALYTICS.md#2-multi-timeframe-methodology).

---

## 4. Relative-strength methodology

**Not RSI** — that is a momentum oscillator and lives in the indicator engine.

**61 features**: 4 measures × 3 benchmarks × 4 lookbacks, plus universe, sector
and industry percentiles per lookback, plus the 0–100 score.

Both a ratio and a difference measure are stored, because they disagree where it
matters: benchmark −20%, stock −10% gives **+12.5%** relative performance and
**+10 points** excess return.

Percentiles are computed over the point-in-time eligible roster. A test measures
the consequence: the same instrument on the same date moves more than 10
percentile points depending on whether the eventual casualties are included.

No rank below a minimum peer count — 20 for the universe, 5 for a sector. A
percentile over four names is a number, not a rank.

Full detail: [ANALYTICS.md §3](ANALYTICS.md#3-relative-strength-methodology).

---

## 5. Sector-strength methodology

**12 features.** Six weighted factors (relative return, relative momentum,
breadth above 50/200DMA, absolute return, participation) blended to 0–100, with
rank, trend, breadth status and participation status.

Weights renormalise over available factors, so a missing input reduces coverage
rather than silently scoring zero. Sectors below five classified members are not
scored — three constituents is arithmetic, not a measure of the sector.

`participation_status` separates a broad advance from three large names carrying
an index, which the return alone cannot distinguish.

**Historical GICS requires a vendor.** Until one is licensed, sector strength
falls back to the SPDR sector ETF's own price history — a legitimate
point-in-time series — marked `source="etf_proxy"` so nothing mistakes it for a
constituent aggregate. What the engine will **not** do is apply today's
classification to 2015.

Full detail: [ANALYTICS.md §4](ANALYTICS.md#4-sector-strength-methodology).

---

## 6. Breadth methodology

**13 features.** Advances/declines, A/D line, up/down volume, percent above
20/50/200DMA, new highs and lows, thrust, participation.

Every measure records **the roster it was computed over** — `universe_size` and
`universe_digest` are part of the measurement, not metadata. Computing "percent
above the 200DMA" for 2008 from today's constituents computes it over the
companies that *survived* 2008: the number comes out far too high, the bear
market looks mild, and a regime model calibrated on it fails when it matters.

Details that carry weight: instruments outside the roster are refused rather
than ignored; percent-above-MA counts only instruments with a defined average (a
young name with no 200DMA must not count as "below" it); roster members with no
data are reported rather than dropped; small universes are flagged
`low_confidence`.

Full detail: [ANALYTICS.md §5](ANALYTICS.md#5-breadth-methodology).

---

## 7. Market-regime methodology

Six states from seven weighted signals. Output format is the brief's:

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

The contradicting entries are deliberate engineering. The aggregate agreement
signal scores the *balance* across benchmarks, so a single lagging index gets
outvoted and vanishes — which is exactly what a reader needs to see. Per-
benchmark divergences are appended as **unscored observations**: they enrich the
explanation without touching the composite, so `reconciles` still verifies the
signals sum to the score.

`SEVERE_RISK_OFF` is an override, not a band: extreme volatility with a negative
composite is a different operating environment from an ordinary bear. Extreme
volatility with a *positive* composite is not risk-off — 2020 H2 was volatile
and rising. Both tested.

Confidence falls near band edges (0.251 against a 0.25 threshold is a coin
flip), when signals disagree, when few are computable, and per divergence.

**Thresholds were not fitted to historical returns**, per the brief.

Full detail: [ANALYTICS.md §6](ANALYTICS.md#6-market-regime-methodology).

---

## 8. Volatility-regime methodology

Five states from **percentiles of the market's own trailing volatility**, not
absolute levels — 20% annualised meant something different in 2017 than in 2020,
and a fixed threshold silently reclassifies the whole market when the level
shifts.

With insufficient history the answer is `UNKNOWN` at confidence 0, not a guess
from absolute ATR. An unreliable regime label is worse than an absent one,
because downstream code will act on it.

Cross-sectional dispersion is recorded as contradicting evidence when it is high
while the index reading is LOW or NORMAL: the index is calm because constituents
are cancelling out, which is a distinct state.

Phase 3 computes and explains the regime and deliberately does **not** act on
it. Sizing, breakout quality and exposure limits are Phases 5 and 8.

Full detail: [ANALYTICS.md §7](ANALYTICS.md#7-volatility-regime-methodology).

---

## 9. Feature registry design

**151 features across six engines**, each declaring name, version, calculation
version, kind, timeframe, output type, null behaviour, input datasets, warm-up,
lookback, parameters, availability and dependencies.

| Engine | Features | Max warm-up |
|---|---|---|
| Indicators | 58 | 272 |
| Relative strength | 61 | 251 |
| Sector strength | 12 | 252 |
| Breadth | 13 | 252 |
| Market regime | 3 | 220 |
| Volatility regime | 4 | 272 |
| **Merged** | **151** | **272** |

Three things a naming convention cannot provide:

**Content-addressed digests.** The active set hashes to one digest stored on
every value row. A changed definition produces a different digest, so recomputed
values coexist with the old rather than silently overwriting them.

**Machine-checkable availability.** `max_warmup` is the history a backtest must
skip. Beginning on day one with half the features null does not measure the
strategy.

**Null semantics.** `WARMUP` / `NOT_APPLICABLE` / `MISSING_INPUT` / `UNDEFINED`.
A 200-day average null because the instrument listed forty days ago is a
different fact from one null because the vendor dropped a session, and imputing
zero for either turns missing data into a strong signal.

The registry validates dangling dependencies, cycles, and a feature whose
warm-up is shorter than something it depends on — which would compute from
undefined inputs and present as a data problem rather than a definition problem.

---

## 10. Database changes

**Five new tables** (46 total), migration `0003_phase3_analytics`, applied to
PostgreSQL 16 and validated.

| Table | Purpose | Notes |
|---|---|---|
| `relative_strength_values` | RS per (instrument, session, benchmark, lookback) | **Range-partitioned monthly.** ~12× the indicator table for 3 benchmarks × 4 lookbacks |
| `market_breadth_snapshots` | Daily breadth | Carries `universe_size` + `universe_digest` — part of the measurement |
| `volatility_regime_states` | Daily volatility regime + evidence | Separate from `market_regime_states` |
| `feature_definitions` | Persisted registry, keyed by digest | Makes a two-year-old feature-set digest explainable after the code changed |
| `feature_set_members` | Digest → definitions | The join that resolves a historical feature set |

New CHECK constraints: RS percentile in [0,1], RS score in [0,100], positive
lookback, breadth `evaluated ≤ universe_size`, volatility confidence in [0,100].

`relative_strength_values` uses a natural composite key and gets the same
partition treatment as Phase 2's tables: 28 monthly partitions created ahead of
need, a DEFAULT backstop that must stay empty, and the idempotent
`tradeit_ensure_month_partition()` helper.

**Schema-drift detection was fixed during this phase.** PostgreSQL truncates
identifiers at 63 characters, so a child index named
`relative_strength_values_p202603_instrument_id_benchmark_symbol_idx` arrives as
`relative_strength_values_p202_instrument_id_benchmark_symb_idx` — month digits
mangled and unmatchable by name. The filter now matches on the owning *table*,
which is short enough to survive. Verified still to catch a deliberately added
column.

---

## 11. Repository changes

Three new point-in-time repositories, all clock-gated:

- **`SectorRepository`** — classification as of the clock's date, in single and
  bulk forms, plus `members()` which applies **both** the sector and universe
  intervals. Returns `None` for unclassified rather than a default bucket.
- **`IndicatorRepository`** — series, cross-section and latest-value reads. The
  cross-section takes the roster as an argument so a caller cannot accidentally
  rank against today's universe. `feature_set_digest` is mandatory on every
  read: values computed under a different definition are a different feature and
  must not be mixed into one series.
- **`RegimeRepository`** — market regime, volatility regime and breadth as known
  on a date. Returns the most recent state at or before the clock rather than
  requiring an exact match (a scan running before that evening's regime job
  would otherwise see nothing), and will not return a state computed for a later
  session.

Configuration gained 8 sections (20 total): `benchmarks`, `scanner_profiles`,
`indicators`, `timeframes`, `relative_strength`, `sector_strength`, `breadth`,
`regime`, `volatility_regime`, with the universe and liquidity defaults you
specified.

---

## 12. Tests and coverage

**626 tests**, up from 185.

| Suite | Tests | Covers |
|---|---|---|
| `test_causality.py` | 274 | Prefix consistency, future-price/volume tampering, warm-up, stability |
| `test_kernels.py` | 44 | Numerical correctness against hand-computed values |
| `test_engines.py` | 50 | RS, breadth, sectors, both regimes |
| `test_feature_registry.py` | 26 | Identity, validation, digests, engine registries |
| `test_timeframes.py` | 26 | Partial candles, holidays, intraday bucketing |
| `test_strategy_config.py` | 22 | Config identity and cross-section coherence |
| `test_leakage.py` (integration) | 21 | The ten acceptance criteria, through storage |
| `test_phase2_schema.py` | 18 | Schema, partitioning, drift |
| `test_benchmarks.py` (performance) | 8 | Throughput, scaling, memory |
| Phase 1–2 suites | ~137 | Unchanged and still passing |

```
ruff check           clean
ruff format --check  clean  (67 files)
mypy --strict        no issues in 48 source files
pytest               626 passed  (8 performance excluded by default)
```

Coverage 81%. The analytics modules that carry logic: `kernels.py` 99%,
`regime.py` 98%, `registry.py` 97%, `timeframes.py` 96%, `indicators.py` 94%,
`sectors.py` 93%, `volatility.py` 91%, `relative_strength.py` 87%,
`breadth.py` 86%. The uncovered remainder is largely Phase 2 interface
definitions (`base.py` modules, 0% by construction — they are `Protocol` bodies)
and wiring (`cli.py`, `session.py`).

---

## 13. Point-in-time leakage tests

All ten acceptance criteria implemented and passing.

| # | Requirement | Where | Notes |
|---|---|---|---|
| 1 | Future price bars cannot enter | `test_causality.py` (274), `test_leakage.py` | Prefix consistency plus a storage round trip |
| 2 | Future volume cannot enter | `test_causality.py` | Volume tampering leaves history unchanged |
| 3 | Partial higher-timeframe candles | `test_timeframes.py`, `test_leakage.py` | Wednesday sees no weekly bar |
| 4 | Universe percentiles cannot see future constituents | `test_engines.py`, `test_leakage.py` | Ranking outside the roster **raises** |
| 5 | Sector calculations cannot use future classifications | `test_leakage.py` | Reclassified company keeps its old sector historically |
| 6 | Backfilled data does not alter snapshot-pinned replay | `test_leakage.py` | Bitemporal revision + ingestion high-water mark |
| 7 | Warm-up behaves correctly | `test_causality.py::TestWarmup` | Boundary is exactly `period − 1` |
| 8 | Corporate actions per the PIT policy | `test_leakage.py` | Split invisible before announcement; no phantom momentum |
| 9 | Timezone boundaries cannot leak next-session data | `test_leakage.py` | A 23:59 UTC Friday clock sees Friday only, not Monday |
| 10 | Holidays and shortened sessions | `test_leakage.py`, `test_timeframes.py` | Good Friday week; early-close session is a normal session |

**The causality test was verified not to be vacuous.** Three realistic leak
patterns were introduced deliberately and all three were caught: a centred
rolling mean, a full-sample z-score, and an off-by-one that reads `close[i+1]`.

---

## 14. Performance benchmarks

Measured on this machine; absolute numbers vary but ratios do not.

| Measure | Result | Projection |
|---|---|---|
| 58 features × 760 bars | **6.8 ms** | ~30 s for 4,000 names, single-threaded |
| Cross-sectional rank, 4,000 names | **13 ms** | ~0.2 s per session, all benchmarks × lookbacks |
| Rank scaling | 16× universe → **14×** time | O(n log n) |
| SMA(200) scaling | 16× data → **3.1×** time | Sub-linear (cache effects) |
| Memory, one instrument | 951 KB peak | ~464 MB for a 500-name batch |

Slowest kernels: `adx_14` (1031 µs), `rsi_14` (460 µs), `percent_rank_252`
(351 µs) — all sequential Wilder recursions.

### Two algorithmic problems found and fixed

The brief asked to identify algorithms that would make full-universe scanning
impractical. Two were found, and both were worth fixing rather than documenting:

**Cross-sectional ranking was O(n²).** The first implementation counted the peer
set for every instrument: 11.4 seconds for 4,000 names, which is **136 seconds
per session** across 3 benchmarks × 4 lookbacks — roughly **28 hours** over a
three-year backtest. Sorting once and binary-searching made it 13 ms, an
**876× improvement**. A benchmark test now guards the regression.

**Three kernels used Python-level window loops.** `realized_volatility`,
`bollinger_bands` and `percent_rank` were rewritten with strided views:
9,745 → 229 µs, 8,317 → 167 µs, 3,899 → 351 µs. Full indicator computation went
from 37.9 ms to 6.8 ms per instrument, a **5.6×** improvement. The causality
tests re-passed unchanged, which is what made the rewrite safe.

### Known limitation, measured rather than asserted

Incremental recomputation does not exist: adding one session costs a full
recompute (6.8 ms). For daily operation that is ~30 seconds universe-wide and
does not matter. It would matter for intraday recomputation across the universe,
and the recursive kernels could carry state to make it O(1) per bar. Recorded as
a measured number so the decision can be made against evidence.

---

## 15. ADRs created

| ADR | Decision |
|---|---|
| [0010](adr/0010-own-indicator-kernels.md) | Write the indicator kernels rather than import them |
| [0011](adr/0011-point-in-time-cross-sectional-ranking.md) | Multi-benchmark RS with an explicit ranking roster |
| [0012](adr/0012-transparent-rule-based-regime.md) | First-generation regime classifier is rule-based and explainable |

No existing ADR was modified. ADR-0008 (no strategy constant in code) was
applied throughout: every period, weight and threshold in Phase 3 comes from
`StrategyConfig`.

---

## 16. Known limitations

1. **No intraday data is ingested.** The 15m/1h/4h logic is implemented and
   tested against synthetic minute bars, but nothing populates minute bars. The
   default `timeframes.enabled` is `("1d", "1w")`.
2. **Sector strength runs on ETF proxies** until historical GICS is licensed.
   Constituent breadth (percent of a sector above its 50DMA) and sector-relative
   ranking are unavailable in that mode, and results are labelled `etf_proxy`.
3. **VIX is configured but unused.** `benchmarks.volatility_symbol` is declared;
   no provider supplies VIX history yet. The volatility regime works without it.
4. **No incremental recomputation** (measured above).
5. **Regime thresholds are unvalidated** — deliberately, per the brief. They are
   documented hypotheses until Phase 9 tests them out-of-sample.
6. **The 250/252 warm-up gap.** You specified 250 sessions minimum history;
   52-week extremes need 252. A newly eligible instrument lacks that feature for
   two sessions. This is ordinary warm-up, not a broken configuration, so it is
   reported by `StrategyConfig.coherence_warnings()` rather than raised. If you
   would prefer every eligible instrument to have every feature from day one,
   raise `min_trading_history_sessions` to 252.
7. **Single-threaded.** The 30-second universe projection assumes one core. The
   work is embarrassingly parallel per instrument; Phase 4 may want a worker
   pool, and nothing in the design prevents it.
8. **Cross-sectional dispersion needs ≥20 instruments** to be emitted at all.

---

## 17. Outstanding data requirements

| Requirement | Blocks | Workaround in place |
|---|---|---|
| **Historical GICS classification** | Constituent sector aggregation | ETF proxies, clearly labelled |
| **Delisted securities** | Honest backtests (Phase 9) | Schema ready; no data |
| **Historical universe membership** | Survivorship-safe scanning | Schema and repositories ready |
| **Historical symbol mappings** | Ticker-recycling correctness | Schema + `EXCLUDE` constraint ready |
| **Point-in-time fundamentals** | Phase 6 | None — genuinely blocking |
| **Filing timestamps** | Phase 6 | `assumed_filing_lag_days` degrades explicitly |
| **VIX history** | Optional volatility input | Regime works without it |
| **Benchmark price history** (SPY/QQQ/IWM) | RS against real benchmarks | Engines work; need the actual bars |
| **Sector ETF history** (11 SPDRs) | ETF-proxy sector strength | Same |

The last two are worth emphasising: **Phase 3's engines are complete but have
never seen real market data.** They have been exercised against synthetic
series only. That is by design — the vendor decision was deferrable — but it
means the first real ingestion will surface data-quality issues the synthetic
provider cannot produce.

---

## 18. Recommendations before Phase 4

**Ingest real price data for a small universe first.** Benchmarks (SPY, QQQ,
IWM), the 11 sector ETFs, and perhaps 50 liquid stocks. This is a few hundred
API calls and would validate the whole Phase 3 layer against reality before
Phase 4 builds pattern detection on top of it. Real data has gaps, halts,
mis-stamped corporate actions and stale series that synthetic data does not.

**Run the vendor acceptance test** in
[VENDOR_EVALUATION.md §6](VENDOR_EVALUATION.md#6-acceptance-test-to-run-against-a-trial)
against one or two trials. It answers more than any datasheet, and the results
map directly onto `ProviderCapabilities` fields.

**Decide on `min_trading_history_sessions`** — 250 as specified, or 252 so every
eligible instrument has a defined 52-week range (limitation 6).

**Consider a worker pool for Phase 4.** Pattern detection is more expensive per
instrument than indicators, and the work parallelises trivially.

**Do not tune the regime thresholds yet.** They will look wrong on individual
historical dates, and adjusting them before Phase 9's out-of-sample harness
exists is precisely the overfitting the brief warns against.

---

## 19. What Phase 4 requires

**Available now:**

| Capability | Where |
|---|---|
| 58 causal indicators with declared warm-up | `IndicatorEngine` |
| Rolling highs/lows, distance-from-high | `rolling_high_N`, `distance_from_high_N` |
| Range / ATR / volume contraction | `range_contraction`, `atr_contraction`, `volume_contraction` |
| MA structure and slopes | `sma_N`, `distance_from_sma_N`, `sma_20_slope` |
| Causal multi-timeframe bars | `resample_for_feature_use`, `align_to_daily` |
| Relative strength, 3 benchmarks × 4 lookbacks | `RelativeStrengthEngine` |
| Sector and market context | `SectorStrengthEngine`, `MarketRegimeEngine` |
| Volatility regime | `VolatilityRegimeEngine` |
| Feature registry + digests | `FeatureRegistry` |
| Phase 2 interfaces for detectors | `strategy/base.py`: `PatternDetector`, `DetectedPattern` |

**Nothing blocks Phase 4.** Pattern geometry needs price, volume and the
contraction features, all of which exist. The vendor decision improves the
universe but does not gate the work.

**Constraints Phase 4 must honour:**

- Detectors receive a **clock-gated series** and must not reach for more.
- Every parameter from `StrategyConfig.patterns` — no defaults in signatures.
- `pivot_price` and `stop_price` are the deliverables: they determine entry and
  position size, and a detector vague about the pivot produces a strategy that
  cannot be sized.
- Detected patterns carry `run_manifest_id` (Phase 2 schema).
- A pattern is **not** a signal. `TRIGGERED` and `CONFIRMED` are Phase 5.
- Extend the causality suite to cover detectors: a pattern detected on day *t*
  must not change when day *t+1* arrives.

---

## 20. Vendor comparison

Delivered as [VENDOR_EVALUATION.md](VENDOR_EVALUATION.md), covering the tier
landscape, three realistic combinations with indicative costs, the nine
questions that remain unresolved, and a six-test acceptance protocol to run
against trials.

**Headline recommendation:** a Tier-B price vendor with delisted-security
support now (tens of dollars per month), upgrading to add as-reported
fundamentals plus SEC EDGAR filing dates before Phase 6 (low hundreds per
month). Phases 4 and 5 need only price, volume, corporate actions and a
survivorship-safe universe — the cheapest things to buy correctly.

**Pricing figures in that document are indicative and must be verified.** Vendor
terms change frequently and are often negotiated rather than listed; the
document says so at the top rather than presenting estimates as quotations.

---

**Phase 4 is not started and will not be started without explicit
authorisation.**
