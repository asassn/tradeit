# Empirical gate report — `full-01`

**Corpus:** scan `full-01`, 78 instruments, snapshot
`twelve_data-daily-e3ddc03209bb25b4`, ~2010-01-04 to 2026-08-07.
**Result:** 18 passed, 5 warned, 1 failed, 1 blocked, 0 errored, 1 skipped.

This report separates four things that are routinely collapsed into one, and the
collapse is the failure mode it exists to prevent.

| | verdict |
|---|---|
| **Machinery validity** | **established** — every structural invariant holds on real prices |
| **Market/economic validity** | **not established, and not attempted** |
| **Data limitations** | one FAIL (survivorship), one BLOCK (point-in-time fundamentals) |
| **Permitted conclusions** | statements about the *platform*; nothing about markets or profitability |

---

## 1. Machinery validity — established

Every check that is permitted to fail passes.

| check | result |
|---|---|
| `phase3.indicator_determinism` | PASS |
| `phase3.indicator_prefix_consistency` | PASS |
| `phase3.scale_invariance` | PASS — 0 violations |
| `phase4.detection_rate` | PASS — 272,537 patterns, 12 detectors, 78/78 instruments |
| `phase4.causality` | PASS — acausal 0 |
| `phase4.score_distribution` | PASS |
| `phase4.identity_stability` | PASS — 2,521,049 observations |
| `phase4.concentration` | PASS — detections on 78/78 |
| `phase5.state_distribution` | PASS — 341,093 events |
| `phase5.boundary_provenance` | PASS |
| `phase5.quality_frozen` | PASS |
| `phase5.monitor_floor` | PASS — 0 below floor at opening, 0 orphaned events, 0 unobserved |
| `phase5.lifecycle` | PASS — 2,696,998 observations, 0 illegal transitions |
| `phase5.causality` | PASS — 0 early observations, 0 duplicated sessions |

Derived ratios, for a later run to be compared against:

| quantity | value |
|---|---|
| distinct structures (base hashes) | 87,704 |
| identities per structure | 3.11 |
| observations per identity | 9.25 (floor 1.50) |
| breakout events per identity | 1.25 |
| breakout observations per event | 7.91 |
| scanned instrument-years | 1,295 |
| patterns per instrument-year | 210.5 (17.5 per detector, ≈ 1 per 14 sessions) |

**What "machinery validity" means here.** Bars reach detectors only through a
point-in-time feed bounded by an as-of clock; no pattern can be constructed
whose geometry ends after the session it was detected on; structural breaks end
an analytical episode and nothing crosses one; identities persist while a
structure is continuously detected and terminate permanently when it ends;
every recorded lifecycle transition is an edge the state machine has; every
breakout event is attributed to the pattern identity the monitor was actually
watching, in the run that produced it; and re-running the scan neither
duplicates nor mutates another run's rows.

**What it does not mean.** None of the above is evidence that any pattern
predicts anything.

## 2. Market/economic validity — not established, and not attempted

No forward return, win rate, hit rate, expectancy, P&L, CAGR, Sharpe, drawdown
or trading edge was computed, at any point, by any check.
`assert_no_performance_claims` walks every result the runner collects and
refuses one that reports such a quantity — it has fired twice in anger during
this gate's development, once correctly and once on a false positive that was
fixed by sharpening the guard rather than loosening it.

Two fields in the report are conditioned on price action *after* a break —
`phase5.lifecycle.terminal_states` and `phase5.state_distribution.states`. They
are statements about the state machine: no position is opened, no entry or exit
price exists, no magnitude is attached, no holding period is defined. They
cannot become a return without supplying all four, which is a later phase's job,
run once, against data nobody has been tuning on.

**No threshold in this repository was tuned against any result in this report.**

## 3. Data limitations

### FAIL — `data.survivorship_coverage`

The vendor cannot supply full point-in-time history for the delisted controls,
so the universe under-represents securities that stopped trading. This is a
property of the data, not of the platform, and it is the single most consequential
limitation in this document. See §5.

### BLOCK — point-in-time fundamentals

The `fundamentals` and `filings` datasets are absent, so the Phase 6 contract
check could not run. A blocked check is never counted as a pass.

### WARN ×5

Descriptive findings, none disqualifying. `phase4.identity_churn` is the one
worth reading: see `docs/IDENTITY_CHURN.md`.

## 4. What may and may not be concluded from this corpus

**Permitted:**

- That the Phase 3/4/5 implementation behaves on sixteen years of real prices as
  its design documents claim: causally, deterministically, with stable identity
  and a legal lifecycle.
- That a regression in any of those properties would be caught — this gate found
  seven real defects that no synthetic corpus produced.
- Baselines for a *future* comparison: the ratios in §1 are a fingerprint. A
  later build that halves the detection rate or trebles identities-per-structure
  has something to explain.

**Not permitted:**

- Any statement about how often a pattern "works". Nothing here measures that.
- Any *cross-sectional* statistic — how often a pattern appears, how one
  instrument compares with another, which detector fires most usefully. The
  universe is not survivorship-safe, so every such number inherits a bias toward
  securities that survived to be in it.
- Any threshold chosen by looking at these numbers. That is the selection-bias
  failure the phase order exists to prevent.
- Citing this run as "a validation" without qualification. By the gate's own
  rule, **a run with any failed or blocked check is not citable evidence**, and
  this run has one of each.

**One subtlety worth stating plainly.** §1 says the machinery is valid; §2 says
market validity is not established. Those are compatible and both are true. The
temptation after eighteen green checks is to treat the corpus as though it had
told us something about markets. It has not, and it cannot until the
survivorship limitation is fixed.

## 5. Readiness

**For Phase 6 development: ready.** The remaining open items do not invalidate
subsequent development. The fundamentals BLOCK is a missing dataset, which is
precisely what Phase 6 exists to consume. The survivorship FAIL constrains what
may be *claimed*, not whether the machinery beneath it is correct.

**For research or backtesting: not ready**, and the blocker is survivorship, not
code. See `docs/PHASE_06_DESIGN.md` §4 for the recommended remedy.

**For trading: out of scope**, and nothing in Phases 3–5 computes eligibility,
ranking or position size.
