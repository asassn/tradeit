# Validation eligibility: what 78 instruments of split-adjusted prices can prove

The first full universe acquisition produced:

| | |
|---|---|
| Instruments requested | 91 |
| Instruments with price history | **78** |
| Daily bars | ~294,000 |
| Coverage | 2010-01-04 through 2026-08-07 |
| Price representation | **split-adjusted** (Twelve Data) |
| Instruments with a verified split schedule | **34** |
| Instruments whose split source answered HTTP 402 | **44** |

This document is about the last two rows, because they decide what may be
claimed and over how many names.

---

## 1. HTTP 402 is an entitlement answer, not a failure

FMP returned `402 Payment Required` for 44 of 78 symbols, **interleaved with
200s**. That rules out a rate limit and a daily quota — both would produce a
contiguous tail, not an alternating pattern — and leaves per-symbol entitlement.

It is classified `NOT_AVAILABLE_ON_PLAN`, never `PROVIDER_ERROR`, and never
retried. The three consequences:

- `CapabilitySupport.NOT_AVAILABLE_ON_PLAN.is_evidence_of_absence` is `False`,
  so the absence of split rows for those 44 is **not** a claim that they never
  split.
- `FetchStatus.REJECTED.is_retryable` is `False`, and `HttpTransport` does not
  retry a 402, so a re-run spends nothing on it.
- The vendor's own HTTP status and message are preserved per instrument, so
  "402" survives into provenance rather than only this project's reading of it.

The enrichment summary now separates four outcomes, because they call for four
different responses:

```
Outcome by kind      : these are four different situations, not one
  answered with data           : 34
  answered, no splits on record: 0
  NOT AVAILABLE ON PLAN        : 44   <- an entitlement answer. NOT retried, and NOT evidence
  provider or network error    : 0
```

Twelve Data's `403` on `/dividends` is classified the same way, with one extra
signal. A bare 403 cannot distinguish a wrong key from an unentitled endpoint —
so three things are consulted in order: the vendor's own message; **whether the
same credential already returned data for another dataset in this run** (one
run, one key, one account — if `/time_series` answered, a 403 elsewhere is not a
bad key); and failing both, `UNKNOWN`. Never `PROVIDER_ERROR`, and never "this
security pays no dividends".

---

## 2. Per-instrument capability flags

Two responses to the 44 would be wrong: discarding them, or treating all 78
alike. So each instrument carries flags, recorded in the package manifest and
carried into the imported snapshot:

| Flag | Means |
|---|---|
| `PRICE_DATA_AVAILABLE` | Daily bars are present. True for all 78. |
| `SPLIT_SCHEDULE_VERIFIED` | A source was *able to answer* — with records or a valid empty response. Not a claim of completeness. |
| `RAW_RECONSTRUCTION_AVAILABLE` | The raw exchange series can be derived, or the prices were raw already. True for 34. |
| `RAW_RECONSTRUCTION_INCOMPLETE_OR_UNKNOWN` | It cannot. The reason is recorded per instrument. True for 44. |

**An empty capability index means "not recorded", never "nothing is eligible".**
A package imported before these existed says nothing, and validation reports
that as unknown rather than as a sample of zero.

---

## 3. Scale invariance: declared, then proved

A split adjustment is a rescaling of the price series. So a computation whose
answer is unchanged when every price is multiplied by a positive constant gives
the same answer on adjusted prices as on raw ones. **That property is the
licence to use the 44**, and a licence nobody checked is not a licence.

Every classification in `tradeit.validation.scale` is executed against the real
indicator engine at six factors — `2.0, 0.5, 8.0, 0.125, 3.7, 1000.0`, including
reverse-split-shaped ones below 1 — on both a fixture series and the snapshot's
own bars. Volume is deliberately **not** rescaled: this models a price
rescaling, and moving volume too would let a volume feature look invariant for
the wrong reason.

### Scale-invariant — valid on all 78

| Analytic | Why |
|---|---|
| percentage returns | `p1/p0 - 1` cancels the factor exactly |
| RSI | ratio of average gains to average losses |
| relative strength | a ratio of two return series |
| trend geometry | normalised slopes and fractional distances |
| moving-average *relationships* | the averages are sensitive; the relationship is not |
| pattern geometry as percentages | depths and widths as fractions of the pattern's own levels |
| contraction percentages | short window over long window of the same series |
| breakout percentage | penetration as a fraction of the boundary |
| normalized volatility | `atr_percent`, `realized_volatility_N`, `bollinger_bandwidth` |
| ADX, ±DI | directional movement over true range |
| OBV, relative volume | volume-only, untouched by a price rescaling |

### Scale-sensitive — limited to the 34

| Analytic | Why |
|---|---|
| absolute dollar-price filters | "above $5" is a different rule on a rescaled series |
| raw quoted historical price | the number that was quoted; nothing derived substitutes |
| absolute dollar ATR (`atr_N`) | range in currency units — use `atr_percent` instead |
| raw share volume | a split changes the share count, so pre-split prints are not comparable |
| dollar volume (`avg_dollar_volume_N`) | price times volume |
| position sizing on historical price | a rescaled price gives a different position and different risk |
| exact historical raw-price replay | the printed series is the point |
| `sma_N`, `ema_N`, `vwap_N`, `macd_*`, `bollinger_*` bands, `rolling_high/low_N` | price levels, in the currency they were quoted in |

### The default is refusal

An unclassified feature is `UNKNOWN`, and `UNKNOWN` is **not eligible** without
raw prices. A test fails when the engine emits a feature no rule covers, so
adding a feature also requires deciding. A second test fails when a feature
declared *sensitive* turns out not to move — an over-cautious rule silently
shrinks the sample for no reason.

### What this does not claim

Split adjustment is **piecewise** constant: the factor changes at each split, so
a window straddling a split is rescaled non-uniformly and even an invariant
feature reads a series nobody could have seen. That is a separate limitation,
recorded separately, and nothing here softens it. What is established is
narrower and still worth having: for the 44, the scale-invariant analytics are
usable and the scale-sensitive ones are not.

---

## 4. Sample sizes are reported separately, always

Every validation run prints both, and the payload carries both:

```
Sample size
------------------------------------------------------------------------------
  real-price-eligible instruments        78   (scale-invariant analytics)
  raw-reconstruction-verified            34   (absolute-price analytics)

  These are two different samples and are never combined.

  Why instruments lack a verified raw price series:
      44  fmp returned not_available_on_plan for this symbol …
```

---

## 5. Rehearsal result

The import and Phase 3 gates were exercised end to end against a package built
to the same shape as the live one (78 instruments, 325,762 rows, 34 enriched /
44 refused). Findings:

| Check | Result |
|---|---|
| `data.rows_present`, `data.quarantine_rate`, `data.duplicate_facts` | PASS |
| `data.session_continuity` | **PASS after a fix** — see below |
| `data.instrument_capability` | PASS, 78 / 34 reported separately |
| `data.adjustment_declared` | WARN — prices are split-adjusted, so corporate-action artefact checks cannot run |
| `data.survivorship_coverage` | PASS in the rehearsal; **FAIL on the real snapshot** — see `docs/SURVIVORSHIP_COVERAGE.md` |
| `data.point_in_time_fundamentals` | BLOCKED — no fundamentals; that is Phase 6 |
| `data.unexplained_price_jumps` | SKIP — adjusted prices, so absent artefacts prove nothing |
| `phase3.indicator_determinism` | PASS — 1,160 series reproduce exactly |
| `phase3.indicator_prefix_consistency` | PASS — 1,160 prefix comparisons agree exactly |
| `phase3.indicator_warmup` | WARN — 20 of 290 series defined earlier than declared, the safe direction |
| `phase3.scale_invariance` | PASS in the rehearsal; **FAILED on the real snapshot** with 32 violations, now fixed — see `docs/SCALE_INVARIANCE_INVESTIGATION.md` |
| `phase4.*`, `phase5.*` | **SKIP — see §6** |

### A second calendar bug, found by the rehearsal

`data.session_continuity` compared bar counts against **weekday** counts with a
2% tolerance. US markets close about ten weekdays a year, which is ~3.6% of
weekdays — above the threshold — so over a sixteen-year window **all 78 of 78
series were reported as gappy**. A check that fires on everything teaches the
reader to skip it, which is how a real gap would have been missed.

It now counts trading sessions using the project's own `TradingCalendar`: 0 of
78 flagged. The same class of defect as the acquisition-side start/end date
checks fixed earlier.

---

## 6. What blocks Phase 4 and Phase 5

**Every Phase 4 and Phase 5 check SKIPPED**, with:

> no patterns have been persisted for this snapshot; run the scanner before the
> Phase 4 checks

The gates read `patterns` and `breakout_events` from the database. Import
populates prices; nothing between import and validation runs the pattern scanner
or the breakout engine over an imported snapshot and persists the results.
`PatternRepository` and `BreakoutRepository` exist, so the missing piece is the
command that drives them over a snapshot's bars — not the persistence layer.

**Phase 3 and the data gates are genuinely runnable on the real package today.
Phase 4 and Phase 5 are not, and no amount of re-importing changes that.** That
scan step is the next piece of work, and it is not started.
