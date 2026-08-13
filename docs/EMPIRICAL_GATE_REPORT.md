# Empirical gate report — Phase 3/4/5 on real market data

**Scan:** `diag-02`, seven instruments (AAPL, NVDA, SPY, BBBY, NKLA, and
instruments 34 and 60) against snapshot `twelve_data-daily-e3ddc03209bb25b4`.
**Verdict:** the Phase 3/4/5 machinery is **ready** for a full-universe scan.
**The run is not citable as evidence about markets**, and those are two
different statements — see §5.

---

## 1. What this gate is, and what it is not

Phases 3, 4 and 5 were built and characterised against generated series. A
random walk is not a market, and the properties that matter most are exactly the
ones a generator cannot test: whether an indicator behaves the same on a real
gap-riddled series, whether a detector's identity survives a delisting, whether
a boundary drawn on real resistance behaves like one drawn on noise.

This gate measures **structure and self-consistency**: causality, determinism,
prefix-consistency, identity, lifecycle legality, provenance. It measures
**nothing about profitability**. No forward return, win rate, expectancy, P&L,
CAGR, Sharpe, drawdown or trading edge was computed, and
`assert_no_performance_claims` walks every result the runner collects to keep it
that way.

## 2. Result

| | count |
|---|---|
| passed | 18 |
| warned | 5 |
| **failed** | **1** |
| **blocked** | **1** |
| skipped | 1 |
| errored | 0 |

### Structural invariants — all pass

| check | result |
|---|---|
| `phase3.scale_invariance` | PASS — 0 violations |
| `phase4.causality` | PASS |
| `phase5.causality` | PASS — 0 observations before their event opened, 0 duplicated sessions |
| `phase5.lifecycle` | PASS — 220,219 observations, 0 illegal transitions |
| `phase5.monitor_floor` | PASS — 0 below floor at opening, 0 events with no pattern row, 0 without an observation at opening |
| `phase5.boundary_provenance` | PASS |
| `phase5.quality_frozen` | PASS |
| `phase5.state_distribution` | PASS — 28,147 events |

These are the eight that are permitted to fail, and none does. Everything else
is descriptive by design: nobody has a defensible prior for how many
cup-with-handles a decade of a large-cap contains, and inventing one would be
tuning a threshold against the validation set.

### The one FAIL and the one BLOCK are both about data, not machinery

- **FAIL — `data.survivorship_coverage`.** The vendor cannot supply full
  point-in-time history for the delisted controls. This is an external data
  limitation, documented in `docs/SURVIVORSHIP_COVERAGE.md`. It is a real
  failure and is not being explained away; see §5 for what it costs.
- **BLOCK — point-in-time fundamentals.** The fundamentals dataset is absent, so
  the Phase 6 contract check could not run. A blocked check is never counted as
  a pass, which is why the gate reports it separately.

## 3. What this gate found, and what it cost to find

Seven defects surfaced that no synthetic corpus had produced. Each was a real
bug in the platform, not a threshold that needed adjusting.

| # | defect | how it presented |
|---|---|---|
| 1 | Wilder tie decided by the last bit; catastrophic cancellation in `diff(log(close))` | 22,326 scale-invariance violations |
| 2 | A pattern key echoed in vendor prose reached the on-disk journal | credential leak in the Tiingo adapter |
| 3 | Identity key `24 + 11 = 35` chars in a `VARCHAR(32)` | scan crashed on AAPL at session ~4,000 |
| 4 | Float guard defending a `Numeric(18,6)` column | scan crashed on BBBY at session ~3,500 |
| 5 | `structural_end_date` re-measured after detection | 5,849 patterns falsely reported acausal |
| 6 | Monitor keyed events by the instance hash, not the tracked identity | every event on a re-minted identity attributed to a terminated row; events colliding and deduplicating each other |
| 7 | Tracker could not find its own live identity after a re-mint | 37,483 of 39,218 identities re-minted; one structure minted 209 times |

Defects 5, 6 and 7 were each first reported as a *check failure* that turned out
to be pointing somewhere other than where it seemed. That is the gate working:
two of them were defects in the checks themselves, and finding those is worth as
much as finding the other five.

Details: `docs/SCALE_INVARIANCE_INVESTIGATION.md`, `docs/EMPIRICAL_SCAN.md`,
`docs/PATTERN_IDENTITY.md`, `docs/IDENTITY_CHURN.md`.

## 4. Machinery readiness: ready

Every property that a full-universe scan would rely on has now been measured on
real prices rather than assumed:

- **Causality.** No detector can construct a pattern whose geometry ends after
  the session it was detected on — refused at construction, and confirmed
  empirically by re-running eleven detectors over the exact bar prefix available
  at each detection. Point-in-time reads are bounded by an as-of clock
  throughout.
- **Structural breaks.** A 536-session gap in BBBY correctly ends an analytical
  episode. No indicator, pivot, identity, monitor or ATR history crosses the
  boundary, and no bar is manufactured to fill it.
- **Identity.** One structure keeps one identity while it is continuously
  detected; a terminated structure stays terminated; a later distinct setup gets
  its own identity; an episode boundary always forces a new one.
- **Lifecycle.** 220,219 recorded transitions, every one an edge the state
  machine has.
- **Provenance.** What was known at detection is separated from what was
  measured later. Scan scope distinguishes snapshot, requested and completed
  universes, so no rate is computed over instruments that were never scanned.
- **Resumability.** An interrupted scan leaves no half-written instrument and
  resumes without duplicating — exercised twice for real, by two crashes.

There is no known machinery defect blocking a larger scan.

## 5. What a full scan will and will not license

This is the part worth being pedantic about, because the temptation after
eighteen green checks is to treat the output as evidence about markets.

**The survivorship FAIL is not cosmetic.** The universe is not
survivorship-safe: delisted names are under-represented because the vendor
cannot supply their point-in-time history. Any *cross-sectional* statistic later
computed from this corpus — how often a pattern appears, how often one resolves
in a given direction, how one instrument compares with another — inherits that
bias. The corpus is sound for validating machinery and unsound for
characterising the market, and the gate's own conclusion says so: **a run with
any failed or blocked check is not citable evidence.**

**Two open items are characterised rather than closed:**

1. *Residual identity churn.* After the continuity fix, re-minting is dominated
   by the legality fork — a detection proposing an edge the lifecycle does not
   have, overwhelmingly a broken-out structure re-detected as near-breakout,
   which is a failed breakout of the same structure. Whether that should be an
   edge of the state machine is a lifecycle decision, not a defect, and is
   deliberately left open. It does not affect any structural invariant.
2. *`KnowledgeTimePolicy.close_instant` ignores early closes.* Left as-is
   because it is conservative — it can only make data knowable later than
   reality, never earlier — and the scanner was made independent of it.

**Nothing here recommends a trade.** A confirmed breakout is a geometric
observation about a boundary, not a signal, and no component of Phase 4 or 5
computes eligibility, ranking or position size.

## 6. Phase 6 readiness

The remaining open issues do **not** invalidate subsequent development. The
survivorship limitation constrains what may be *claimed* from the data, not
whether the machinery beneath it is correct; the fundamentals BLOCK is a missing
dataset, which is precisely what Phase 6 exists to consume.

Phase 6 is not started, and is not started here. This document records that the
Phase 3/4/5 gate has been run, what it found, and what it does not license.
