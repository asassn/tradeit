# Identity churn on real data

`phase4.identity_stability` reported that 148,922 of 156,433 pattern identities
from the `diag-01` diagnostic scan (95.2%) carry a re-mint suffix, at a mean of
2.28 observations per identity. This document answers the question that number
raises and cannot itself settle: **is one structure being torn into many
identities, or are many structures genuinely beginning and ending?**

The two call for opposite responses. If identities genuinely end, re-minting is
correct and merging them would be the bug. If one continuous structure is being
fragmented, then every per-identity rate in the platform is computed over an
inflated denominator.

## What was measured

Reproduced on a three-instrument, eight-year synthetic corpus (39,218
identities, 95.6% re-minted — the same regime as the real scan), because the
per-cause tallies needed instrumenting and the user's database is not reachable
from here. `phase4.identity_churn` now emits this table on every gate run, so
the real numbers arrive with the next scan.

| detector | ids | forked | fork% | obs med | p90 | p99 | life med | single% |
|---|---|---|---|---|---|---|---|---|
| ascending_triangle | 3,138 | 2,988 | 95.2% | 2.0 | 3 | 15 | 6 | 22.3% |
| base_on_base | 242 | 231 | 95.5% | 2.0 | 2 | 48 | 6 | 0.0% |
| breakout_retest | 3,835 | 3,635 | 94.8% | 1.0 | 2 | 4 | 0 | 62.5% |
| bull_flag | 7,119 | 6,726 | 94.5% | 2.0 | 3 | 15 | 6 | 26.8% |
| cup_handle | 4,107 | 4,002 | 97.4% | 2.0 | 2 | 8 | 6 | 14.7% |
| double_bottom | 5,910 | 5,825 | 98.6% | 2.0 | 2 | 6 | 6 | 39.6% |
| flat_base | 3,516 | 3,282 | 93.3% | 2.0 | 3 | 18 | 6 | 8.2% |
| inverse_head_shoulders | 1,147 | 1,129 | 98.4% | 2.0 | 2 | 4 | 6 | 10.5% |
| pennant | 1,789 | 1,647 | 92.1% | 2.0 | 3 | 6 | 6 | 20.3% |
| tight_consolidation | 1,142 | 983 | 86.1% | 2.0 | 3 | 14 | 6 | 19.4% |
| vcp | 7,273 | 7,035 | 96.7% | 2.0 | 3 | 10 | 6 | 15.8% |

`life med` is calendar days between first and last observation; `single%` is the
share of identities observed on exactly one session.

Two further numbers decide the question:

- **1,280 base hashes name more than one identity, the most crowded naming 209.**
  A base hash is a content hash of instrument, timeframe, pattern type and
  structural *start*. 209 identities sharing one means the same structure,
  beginning on the same date, was re-minted 209 times over eight years.
- **Re-mints by cause: 36,933 after termination, 550 on an illegal transition.**

## The determination: fragmentation

A structure whose start date is fixed and which is re-minted 209 times, with a
median identity lifespan of six days, is not 209 structures. It is one structure
observed through a lifecycle that keeps declaring it over.

The mechanism is a chain of four rules, each defensible alone:

1. A detector re-measures from scratch each session and reports a state derived
   from the current geometry. That state is **not monotonic** — price
   oscillating around resistance moves a pattern between NEAR_BREAKOUT and
   BROKEN_OUT_UNCONFIRMED freely.
2. The lifecycle **is** monotonic. `BROKEN_OUT_UNCONFIRMED` may only go to
   `{BROKEN_OUT_UNCONFIRMED, EXPIRED, INVALIDATED}`.
3. `PatternTracker` ages a pattern out after `grace_sessions = 3` sessions
   without re-detection, and terminates it.
4. A detection arriving for an identity that has already terminated gets a new
   identity — the `_retired` rule, added because reusing the key merged separate
   lives into one row whose history could not be untangled afterwards.

Rule 4 is right, and it is not the cause. The dominant path is rules 1–3:
**36,933 of 37,483 re-mints (98.5%) follow a termination**, not an illegal
transition. Patterns are ending — mostly by ageing out — and being re-detected
immediately afterwards as new structures.

The `superseded` path (550) is the smaller one and is the clearer illustration:
409 of those identities were in `BROKEN_OUT_UNCONFIRMED` and were re-detected as
`NEAR_BREAKOUT` (275) or `MATURE` (87). A structure that closed above resistance
and fell back is a *failed breakout of the same structure*, not a different
structure that happens to start on the same date.

## What this does and does not invalidate

**Does not:** anything about causality, boundary provenance, lifecycle legality
or point-in-time correctness. Every identity is individually well-formed, every
transition is legal, and `phase5.lifecycle` passes with zero illegal
transitions. Fragmentation makes the corpus *finer*, not wrong.

**Does:** every rate whose denominator is an identity. "Patterns per
instrument-year", "share of identities reaching a breakout", "mean observations
per identity" and anything a later phase computes per pattern are all measured
against a denominator inflated by roughly the fragmentation factor. Breakout
counts are affected the same way, since each re-minted identity can open its own
event.

## What is deliberately not done here

No threshold is changed. `grace_sessions`, `resolution_carry_sessions` and the
detectors' own parameters are exactly where they were, because tuning them
against a real-data result is the selection-bias failure the phase order exists
to prevent — and because the remedy is a lifecycle decision rather than a
number.

The options, for a decision that is not the validator's to make:

1. **Accept it.** Identities are short-lived by design; report every rate per
   *base hash* rather than per identity, and treat the identity as an
   observation episode.
2. **Let the lifecycle absorb a fade.** Give `BROKEN_OUT_UNCONFIRMED` an edge
   back to `MATURE`/`NEAR_BREAKOUT`, so a failed breakout stays the same
   structure. Removes the 550; leaves the 36,933.
3. **Stop ageing a structure out while it is still being detected.** The 36,933
   are patterns that terminated and were then re-detected. If the same geometry
   is still present, the termination was the error, not the re-detection.

Option 3 addresses 98.5% of the churn and is the one the evidence points at.
None of them should be chosen from these numbers alone without deciding what a
pattern identity is *for*, which is a Phase 6/7 question about what a screen
surfaces and what a backtest counts.
