# Phase 3 Real-World Acceptance Report

**Status: CONDITIONAL PASS.** The analytics layer is sound enough to build
Phase 4 on. It has not been validated against real market data, because this
environment cannot reach any market-data vendor, and that gap is real rather
than cosmetic. What was validated, and what could not be, is set out below
without rounding either way.

The question at this gate is not "does this strategy make money" — there is no
strategy. It is: *if the system had been running historically, would these
features have represented exactly what it legitimately could have known at that
time, and do the calculations behave correctly on real-world market data?* The
first half is answered yes, structurally and by test. The second half is
answered for the mathematics and unanswered for the data.

---

## 1. The blocker, stated first

**Every market-data host is refused by this environment's egress policy.**

Probed directly: `stooq.com`, `query1.finance.yahoo.com`, `www.alphavantage.co`,
`api.tiingo.com`, `data.nasdaq.com`, `api.polygon.io`, `eodhd.com`,
`www.sec.gov`, `financialmodelingprep.com`. All returned HTTP 000. The proxy's
own status endpoint reports, for each one:

```
"kind": "connect_rejected",
"detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)"
```

This matters for how it gets fixed. **It is not a credentials problem and not a
subscription problem.** The connection is refused at CONNECT, before any
request is sent, so no API key changes the outcome and buying a data plan would
change nothing. The remedy is an allowlist change by whoever administers this
environment's network policy — adding `api.tiingo.com` alone would unblock the
whole of this gate's empirical half.

PyPI *is* reachable (it is on the proxy's `noProxy` list), which is what made
the independent cross-validation in §7 possible.

### Provider recommendation

**Tiingo**, on four specific grounds, none of them price:

| Requirement | Why it is non-negotiable | Tiingo |
|---|---|---|
| Raw prices alongside adjusted | ADR-0005: an adjusted-only feed encodes future splits into past prices, so every price-level threshold in a backtest tests a counterfactual | `open`/`adjOpen`, `volume`/`adjVolume` in the same row |
| Corporate actions | A 50% drop is a catastrophe or a 2-for-1, and only the action series distinguishes them | `splitFactor` and `divCash` per row, dated to the ex-date, from the same download as the prices |
| Delisted securities | Without them the universe is survivorship-biased by an unknown amount | Included, with an `endDate` that stops when the company did |
| Depth | 2000, 2008 and 2020 are where a regime model earns or loses credibility | 30+ years |

The corporate actions arriving *in the price file* is the underrated property:
the action series and the price series cannot disagree with each other, because
they are the same bytes.

What Tiingo does **not** solve, and the adapter says so in
`TiingoProvider.limitations()`:

- **No historical index constituents.** It knows a ticker existed and when it
  stopped; it does not know it was in the S&P 500 in 2011. Survivorship bias in
  a universe built from it is *reduced, not solved*. Solving it needs Norgate
  or CRSP.
- **No per-bar publication timestamp.** `knowledge_time` is the session close
  plus the configured lag, applied as our rule and stamped `ESTIMATED`. This is
  why `capabilities.backtest_grade` is `False` even for the recommended vendor.

**Stooq** is also implemented, needs no credential, and is explicitly *not*
backtest-grade: adjusted-only prices, no corporate actions, no delisted names.
It exists so that the moment egress opens, real price behaviour can reach the
pipeline in one command with no account setup — and its `limitations()` names
the exact features (returns-based ones) that survive the adjustment and the
exact ones (price-level thresholds) that do not.

---

## 2. What was validated, and how strongly

| # | Gate item | Status | Evidence |
|---|---|---|---|
| 1 | Scanner warm-up 252; DATA_ELIGIBLE vs FEATURE_READY | **Done** | `analytics/eligibility.py`, 41 tests |
| 2 | Feature registry ≠ model feature set | **Done** | `analytics/feature_sets.py`, ADR-0013 |
| 3 | Real provider through the vendor-neutral abstraction | **Code done, unrun** | `providers/tiingo.py`, `providers/stooq.py` |
| 4 | Heterogeneous validation universe | **Done** | 85 instruments, `config/validation_universe.toml` |
| 5 | Corporate-action validation | **Logic done, unrun on real actions** | `check_corporate_actions`, 6 tests |
| 6 | Missing-bar / calendar validation + fill rules | **Done** | `check_calendar`, `fill_policy()`, 8 tests |
| 7 | Indicator cross-validation | **Done, genuinely** | 48 tests vs two independent libraries |
| 8 | Relative-strength validation | **Invariants done, empirical unrun** | survivorship, ordering, ties, roster enforcement |
| 9 | Market-regime sanity review | **Done — found 4 defects** | 30 tests, §9 |
| 10 | Volatility-regime validation | **Done** | band coverage, refusal to guess |
| 11 | Performance | **Measured on synthetic** | §11 |
| 12 | Data-quality report with quarantine | **Done** | 13 checks, `data/quality.py` |
| 13 | This report | Done | — |

782 tests pass. Lint and `mypy --strict` clean across 56 modules.

---

## 3. Item 1 — Warm-up and the two-state eligibility model

The default history floor moved **250 → 252**. At 250 the annual features are
two sessions short of the year their names claim: a "52-week high" computed
over 250 sessions is a 250-session high wearing the wrong name.

It remains a per-strategy default. `EligibilityPolicy` derives the real
requirement from the strategy's *declared* feature set, so a mean-reversion
system reading a 20-day band is ready in 20 sessions rather than waiting 272
for an ADX it never reads. Lowering the floor forces the relative-strength
lookbacks down with it — the config refuses an incoherent combination rather
than producing a permanently empty screen.

**The two states, and why one was not enough:**

- `DATA_ELIGIBLE` — enough clean history to exist: storable, chartable, and a
  legitimate member of a breadth roster. **Not scannable.**
- `FEATURE_READY` — every declared feature has finished warming.

A security that IPO'd 100 sessions ago is genuinely in the market. It has a
price, it trades, it belongs in an advance/decline count. It is simply not yet
rankable by a strategy needing a 52-week range. Collapsing these into one
condition forces a choice between excluding real market activity from breadth
and ranking on features that are still converging — and the divergence is not
an edge case: the full indicator registry warms in 272 sessions against a
252-session floor, so **every** newly eligible instrument spends 20 sessions in
the gap.

Assessments carry the arithmetic: which features are still warming, how many
sessions remain, and `None` rather than a number when the blocker is not time —
a delisted instrument never becomes ready, and reporting "3 sessions" for it
would invite waiting for an event that cannot happen.

---

## 4. Item 2 — Registry membership is not a licence to consume (ADR-0013)

The registry holds 151 features. `registry.active()` is one keystroke away and
returns exactly the shape a model wants. Taking it costs three things:

1. **Reproducibility breaks silently.** A scorer consuming the active registry
   gains an input the moment anyone registers an indicator. No file it owns
   changed, no review happened — and every result it produced before that
   moment was produced by a thing that no longer exists.
2. **Features get consumed that were never meant for consumption** —
   diagnostics like `relative_volume`, sizing inputs like `atr_percent`.
3. **Correlated variants silently reweight the model.** 60 of the 151 features
   are relative strength (5 measures × 3 benchmarks × 4 lookbacks) and 26 more
   are moving averages and momentum. A model handed all 86 has not received 86
   pieces of evidence about trend; it has received a handful, many times over.

`FeatureSet` makes consumption an explicit, versioned, hashed declaration.
`FeatureView` raises on undeclared access, so the rule is enforced rather than
trusted. Two digests, because two different things change independently: the
*choice* of features, and their *definitions*.

**No features were selected.** Which features belong in a scoring set is a
Phase 4–8 decision; choosing now, with no out-of-sample harness, would be the
overfitting the brief prohibits.

---

## 5. Items 5 & 6 — Corporate actions, calendars, and when a value may be filled

**The fill rule, stated once, in `quality.fill_policy()`:**

| Dataset | Rule |
|---|---|
| Price bars | **Never filled.** A missing session stays missing. |
| Indicator values | **Never filled.** A null carries a `null_behaviour` saying why. |
| Corporate actions | **Never inferred from prices.** |
| Benchmark series | **Never filled**, and a missing benchmark session propagates. |
| Fundamental facts | *Carried forward* — different from filling. |
| Sector classification | Carried forward from the last effective date; never back-filled. |
| Intraday aggregation | A partial period is excluded, never padded. |

Forward-filling a price bar manufactures market activity that did not occur: it
creates a session with a real close and no true volume, which then becomes a
data point in every average, a candidate for a breakout, and a tradable date in
a backtest — at a price at which nobody could have traded.

**Corporate-action cross-examination** works in both directions, and both
failures are silent without it:

- *A jump with no action.* Reported with the split ratio that would explain it,
  and **never repaired**. Inferring the split would rewrite genuine crashes into
  clean adjustments, which is how a backtest ends up unable to lose money. The
  severity stays `SUSPECT` at every magnitude — Enron, Lehman and SVB all
  printed moves in the "surely that's a missing split" range and every one was
  real.
- *An action with no jump.* A recorded 2:1 split with no corresponding move in
  the raw series means the prices arrived pre-adjusted, and applying the
  adjustment again would halve them twice. Run against Stooq, this fires on
  every action — which is the correct finding.

**Calendar validation** runs both ways. A missing session (exchange traded, no
bar) is a halt, a suspension, or a vendor gap. An unexpected session (a bar on a
holiday) is either a vendor error or a hole in our calendar — and the second is
worse, because it silently shifts every session count. Listing dates bound the
expectation, so an IPO's pre-listing history is not reported as a catastrophic
gap.

---

## 6. Item 4 — The validation universe

85 instruments, chosen to break things. Composition: 24 controls, 11 delisted,
11 sector proxies, 6 splits, 6 extreme-move, 5 IPOs, 4 ADRs, 4 liquidity edge
cases, 4 symbol changes, 3 benchmarks, 2 reverse splits, 2 REITs, 2 dual-class,
1 dividend case.

A few chosen for specific reasons:

- **LEH, BSC, WAMUQ** — the survivorship test. A 2007 screen that cannot see
  Lehman is not a 2007 screen.
- **GME, AMC, BBBY** — the false-positive test. Every extreme-value diagnostic
  fires on January 2021 and every one of those bars is correct.
- **BRK-A and SNDL** — six-figure and sub-dollar prices in one universe.
- **ARM** — listed, taken private, listed again; a naive `first_trade_date`
  lookup finds the wrong one.
- **GOOG** — meant the class-A shares before 2014 and the class-C shares after.
  Same string, different security.
- **USO** — tests whether anything in the pipeline assumes prices are positive.
- **ATVI** — 21 months of collapsed volatility while an acquisition was pending.
- **24 controls** (JNJ, KO, PG, …) — without them, a check that flags
  everything looks vigilant.

`coverage_gap(supplies_delisted=False)` returns the 11 tickers a
survivorship-unsafe vendor cannot supply, **by name** rather than as a count,
because losing LEH and BSC removes an entire class of test while losing one
thin-liquidity name removes very little.

---

## 7. Item 7 — Indicator cross-validation *(the strongest evidence in this report)*

48 tests against `ta` 0.11.0 and `pandas-ta-classic` 0.6.52 — two separate
codebases by separate authors. Where our value matches both, a shared upstream
bug is not a plausible explanation.

**Exact agreement** (to 1e-6 or better, after stated warm-ups): SMA, EMA,
Wilder smoothing, RSI, MACD line/signal/histogram, ROC, true range, ATR,
Bollinger upper/middle/lower, ADX, +DI, −DI, OBV increments, typical price,
rolling VWAP.

Seeding transients were *measured*, not assumed. RSI: 1.9e-2 at bar 100, 2.1e-6
at bar 200, 2.8e-14 by bar 500. ATR: 8.2e-7 at bar 150, exactly zero by bar 450.
The comparison skip in each test is where the transient has decayed past the
tolerance, not where the test happens to pass.

**Five deliberate divergences, documented rather than reconciled:**

1. **EMA/Wilder seeding.** We seed from the mean of the first `period` values;
   `ta` smooths from bar zero and masks the warm-up. Both emit at the same
   index; the seed values differ by 0.83 on the test series and converge
   geometrically. Ours makes the early series independent of a single bar.
2. **ADX warm-up.** Ours is `2 × period` (first defined at index `2p − 1`,
   asserted). Libraries emitting from `period` publish values still converging.
3. **OBV origin.** We start at zero; both libraries start at `volume[0]`. A
   constant offset in a series whose absolute level carries no information. The
   test compares increments *and* asserts the offset is constant — which is
   what would break if a single bar's direction convention diverged.
4. **RSI at zero average loss** returns 100 rather than dividing by zero.
5. **ROC units** — fraction, not percent.

**Four tests check the harness itself**, because a comparison that cannot fail
proves nothing: a planted 0.01 error, an off-by-one shift, an all-NaN series,
and a wrong smoothing constant (Wilder vs standard EMA) each fail it.

The input is a deterministic pseudo-random walk with trends, volatility
clustering and two large gaps. Real data would be better — but what is under
test is the arithmetic, and an indexing error or wrong smoothing constant shows
up on any series with enough variation.

---

## 8. Item 8 — Relative strength

**The survivorship control is asserted directly**: removing the failures from a
ranking roster must *lower* a survivor's percentile. If those two numbers were
equal, the roster would not be being honoured, and every historical percentile
would be computed against a universe that excludes the companies that went to
zero — which flatters every survivor.

`rank_cross_section` raises on any instrument outside the supplied roster
rather than including it, because a value for an instrument not yet listed is
either a bug or a leak. Ranking below `min_universe_for_rank` returns `None`
rather than a number: a percentile over four names invites a screen that ranks a
security first out of three and sizes it like a market leader.

Also confirmed: relative strength is computed on returns, not levels (a $500
stock is not stronger than a $5 stock — the error would put every high-priced
name in the top decile, which is exactly the kind of mistake that looks like a
working signal); ties rank identically; misaligned series are refused rather
than compared; and `rs_*_spy_120` and `rs_*_qqq_120` hash differently, so the
stored dataset stays interpretable.

**Not validated:** whether ranks computed from real vendor data are stable
across vendors, and whether the point-in-time universe reconstructed from a real
feed matches a known historical index membership. Both need data.

**Survivorship limitation, stated plainly:** the *mechanism* is survivorship-safe
— interval-based `universe_memberships`, `symbol_mappings` with an exclusion
constraint, no "latest data" read path anywhere. Whether any *particular
historical run* is survivorship-safe depends entirely on whether the vendor
supplied the delisted constituents. On Tiingo it is reduced, not solved. On
Stooq it is not addressed at all. **Survivorship bias has not been solved; it
has been made impossible to introduce accidentally, and possible to solve given
the right vendor.**

---

## 9. Items 9 & 10 — Regime and volatility review *(four defects found)*

Each scenario feeds the classifier the conditions that publicly prevailed on a
date — March 2020, the 2017 grind, the 2023 narrow advance, the 2022 grinding
bear, a mid-2020 transition — and asks whether the answer reads like the market
it describes. **The inputs are stated historical conditions, not values computed
from a price feed**, and the tests say so. What is under test is the
classifier's logic.

Feeding in March 2020 produced an answer that did not read like March 2020.
Unpicking why turned up four defects. **None is a threshold change** — the brief
forbids tuning thresholds here and nothing was tuned. All four are definition
errors or logical inconsistencies, the categories the brief permits fixing.

**1. Breadth silently scored backwards.** `pct_above_200dma` takes a fraction,
matching what `BreadthEngine` emits, but is *named* "pct". Passing `3.0` for 3%
scored **+1.0 — maximally bullish — for the most bearish breadth reading that
exists**, and the evidence string rendered "300% above their 200DMA" without
anything noticing. There is no way to distinguish `0.03` from a genuine 3% at
the boundary, so it now raises rather than guesses.

**2. Zero participating sectors was discarded as missing data.** `if not
sectors_above_fast` treated zero of eleven sectors above their moving average —
the strongest bearish participation reading available — as an absent input, and
dropped the signal entirely. Same falsy-vs-`None` class of bug this project has
hit before. Now `is None`, with a range check.

**3. Unanimity was penalised as ambiguity.** Every per-benchmark observation was
filed as a "divergence" and charged 4 points of confidence — so **the more the
benchmarks agreed, the less confident the classification became.** March 2020
scored 64/100 with all seven signals aligned. Observations now carry a
direction and only *opposing* ones reduce confidence; that scenario now reads
100.

**4. The evidence lists were inverted for every negative regime.**
`RegimeSignal.supports` means "this signal reads bullish", not "supports the
classification". Used directly, it filed all seven bearish signals under
*Contradicting Evidence* on a BEAR reading — an explanation arguing against its
own conclusion. Evidence is now filed relative to the composite's direction.

A fifth improvement fell out of #3: the observation set gained its bullish half.
"IWM still holding its 200DMA" during a downtrend — what an early turn looks
like — was previously impossible to emit.

The 2023 narrow-advance case now reads exactly as intended: **BULL, confidence
68**, with breadth, new highs/lows and sector participation all filed as
contradicting evidence. That is the distinction the model exists for — a
cap-weighted index can rise while most stocks fall, and a regime model
reporting only index direction says "bull" while hiding that a breakout system
has almost nothing to buy.

Also confirmed: classification is deterministic and reproducible; the composite
moves monotonically with each input (catching a sign error in a weight, which is
invisible in any single classification); a 12-name universe cannot produce a
confident breadth reading; missing inputs give `UNKNOWN` with confidence 0
rather than a guess; the explanation always reconciles with its score.

**Volatility** is a separate model, deliberately — 2020 H2 was volatile *and*
rising, and one row carrying both judgements would force a single confidence
number for two independent questions. It refuses to classify without a
percentile: 20% annualised volatility is calm for a biotech and alarming for a
utility, so an absolute level means nothing without its own history. `NaN` is
treated as absent rather than as zero — the failure that would classify every
warming-up instrument as calm. All 101 percentile values from 0.00 to 1.00 land
in a band, monotonically, with none falling through to `UNKNOWN`.

---

## 10. Item 12 — Data-quality diagnostics

13 checks with three severities. **Only `REJECT` quarantines**; `SUSPECT`
attaches a quality flag and stores the row.

That distinction is the whole design. Every diagnostic here produces false
positives on real data, and **the false positives are the most interesting
sessions in the market**. GME on 2021-01-27 trips the price-spike check, the
volume check and the volatility check simultaneously, and every one of those
bars is correct. Dropping anything unusual would systematically delete exactly
the market conditions a breakout system exists to trade — a far worse bias than
the occasional bad tick.

| Check | Severity | Note |
|---|---|---|
| Duplicate bars | INFO if identical, REJECT if conflicting | An exact replay must be free; a genuine conflict makes the stored row arbitrary |
| OHLC inconsistency | REJECT | No legitimate counterexample exists |
| Non-positive price | REJECT | A vendor placeholder for a suspended session |
| Negative / absurd volume | REJECT | |
| Volume spike | SUSPECT | Squeeze names exceed 50× median legitimately |
| Zero-volume *run* | SUSPECT | One quiet session is not a pattern |
| Missing session | SUSPECT | Halt, suspension or vendor gap — never filled |
| Unexpected session | SUSPECT | Vendor error, or a hole in our calendar |
| Extreme price jump | SUSPECT | |
| Unexplained jump | SUSPECT | Reported with the implied ratio, never repaired |
| Action without jump | SUSPECT | Prices are already adjusted |
| Timestamp anomaly | REJECT | `knowledge_time < event_time` is the worst row a vendor can send |
| Symbol-mapping conflict | REJECT | Ambiguous identity: a lookup returns an arbitrary row |
| Stale price run | SUSPECT | A feed repeating its last price makes volatility read zero while the market moves |

`silent_checks()` reports checks that fired on nothing — a check that never
fires on a deliberately hostile universe is more likely broken than vigilant.

**One architectural finding.** Writing the tests revealed that several
structural checks are currently *unreachable through the domain model*:
`OhlcvBar` already refuses an inconsistent bar, a non-positive price and a
prescient `knowledge_time` at construction. The naive test could not even build
its input. The duplication is kept deliberately as defence in depth — rows also
arrive from a database read of older data, a raw vendor payload being triaged
before construction, or a migration — and the tests use `model_construct` to
keep those paths honest. This is documented in the module rather than left for
someone to discover and "clean up".

---

## 11. Item 11 — Performance

Measured, single-threaded, on this container. **These are synthetic-data
numbers**; real data changes the I/O profile, not the arithmetic.

| Measurement | Result |
|---|---|
| 58 indicators × 760 bars | 6.2 ms → **161 instruments/s** |
| Full universe (4,000 × 760 bars) | **~0.4 min** |
| Cross-sectional rank, 4,000 instruments | 11 ms → ~0.1 s/session for all 4 lookbacks × 3 benchmarks |
| Memory, 58 features × 760 bars | 951 KB peak |
| Bar objects vs feature arrays (760 bars) | 1,423 KB vs 983 KB |

**Scaling was measured rather than assumed**, because extrapolating blindly is
how an O(n²) surprise reaches production:

- `sma(200)`: 16× data → **7.0×** time (sub-linear; vectorisation amortises).
- Cross-sectional rank: 16× universe → **16.5×** time (linear, as the
  sorted-array-plus-binary-search design intends).

Extrapolations, stated as extrapolations:

| Universe | Indicator pass | Ranking (per session) |
|---|---|---|
| 500 | ~3 s | ~1.5 ms |
| 1,000 | ~6 s | ~3 ms |
| 4,000 | **~25 s** | ~11 ms |

Against a 90-minute scheduler budget for `compute_indicators`, a 4,000-name
universe uses roughly 0.5% of it single-threaded. **The analytics layer is not
close to being the bottleneck**; ingestion and database I/O will dominate, and
neither was measured here because neither can be measured without a vendor.

Per-kernel costs over 760 bars: ADX 814 µs, RSI 528 µs, percent-rank-252
313 µs, EMA 302 µs, realised-vol 162 µs, Bollinger 133 µs, rolling-max-252
58 µs, relative volume 24 µs, SMA-200 17 µs.

Two bottlenecks were found and **fixed rather than documented** during Phase 3:
the cross-sectional ranking was O(n²) (136 s/session at 4,000 names → 0.2 s,
876×), and three kernels ran Python loops (37.9 ms → 6.8 ms per instrument).

---

## 12. What could not be validated

Stated plainly, because a conditional pass is only honest if the conditions are
explicit:

1. **No indicator has been computed from a real price series.** The mathematics
   is cross-validated against two independent libraries; the *pipeline* from
   vendor bytes to feature value has never run end-to-end on real data.
2. **No corporate action has been ingested.** The adjustment logic is tested
   against constructed splits and dividends. AAPL's 7:1 and NVDA's 10:1 have
   not passed through it.
3. **No delisted security has been fetched.** The survivorship *mechanism* is
   tested; whether Tiingo actually returns LEH's final sessions is untested.
4. **The regime models have never seen a computed input.** Section 9 validates
   the classifier's reasoning given conditions, not the pipeline's ability to
   produce those conditions.
5. **No real trading calendar has been compared against a real vendor's
   sessions.** The check is tested against a constructed gap and a constructed
   holiday bar.
6. **Ingestion and database throughput are unmeasured**, and are the likely
   real bottleneck.
7. **Vendor-to-vendor consistency is unmeasured.** Two feeds disagreeing about
   a close is common and would be worth knowing about.

**What unblocks all seven:** allowlisting `api.tiingo.com` (and ideally
`www.sec.gov`) in this environment's egress policy, plus a Tiingo API key. The
adapter, the universe, the diagnostics and the report structure are all built
and waiting; the work becomes a run rather than a build.

---

## 13. Recommendation

**Proceed to Phase 4, with two conditions.**

The reasoning for proceeding:

- **The causality guarantee is structural, not empirical.** 274 prefix-consistency
  tests prove every indicator depends only on past bars, and they were verified
  non-vacuous against three deliberately planted leaks (a centred rolling mean, a
  full-sample z-score, an off-by-one `close[i+1]`). Real data would not
  strengthen this, because it is a property of the code, not the input.
- **The arithmetic is independently corroborated.** Two separate libraries agree
  with every kernel, with five divergences that are documented choices.
- **The gate did its job.** It found four real defects in the regime model —
  including one that scored the most bearish breadth reading possible as
  maximally bullish. A gate that finds nothing is not evidence of quality.
- **Pattern recognition consumes indicator series, not vendor bytes.** Phase 4
  builds on the layer that *is* validated. Its own correctness will be
  testable on constructed patterns with known answers.

The two conditions:

1. **Phase 4 must not be treated as validating Phase 3.** Pattern recognition
   passing its tests says nothing about whether AAPL's 2014 split adjusts
   correctly. The items in §12 stay open regardless of what Phase 4 achieves.
2. **The data validation runs before Phase 9 (backtesting), not after.** A
   backtest on unvalidated data produces a number that looks authoritative and
   is not, and the temptation to believe it grows with every phase that
   precedes it. Phases 4–8 produce logic that can be tested on constructed
   inputs; Phase 9 produces *results*, and results demand validated data.

**Live trading remains disabled** (ADR-0004) and outside the roadmap until
separately authorised after successful paper trading.

---

## Appendix — Reproducing this report

```bash
make check                       # 782 tests, lint, mypy --strict
.venv/bin/python -m pytest tests/unit/test_cross_validation.py -v   # item 7
.venv/bin/python -m pytest tests/unit/test_regime_sanity.py -v      # items 9, 10
.venv/bin/python -m pytest tests/unit/test_data_quality.py -v       # items 5, 6, 12
.venv/bin/python -m pytest tests/unit/test_eligibility.py -v        # items 1, 2
make bench                       # item 11
```

The network probe in §1 is `tests/unit/test_data_quality.py::TestTransport::
test_an_unreachable_host_is_diagnosed_as_such`, marked `network` and excluded
from the default run because its result depends on the machine's egress policy
rather than on this repository.
