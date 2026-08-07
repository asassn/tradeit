# ADR-0014: Structure is discovered from market geometry, not selected by score

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 4

## Context

The same defect appeared independently in the first two detectors built, which
is what makes it an architectural rule rather than two bugs.

**Bull flag.** The first implementation enumerated every (flagpole length, flag
length) split of the recent history, scored each, and applied a tie-break. On a
generated series with a 12-session pole followed by a 9-session flag it reported
a 20-session "consolidation" that had swallowed the pole — and then dutifully
complained that the consolidation was rising.

**VCP.** The first implementation enumerated every confirmed swing low as a
possible base origin and let the quality sort pick the winner. On a generated
18% / 10% / 5% base it reported contractions of 15.8% / 22.5% / 14.4%, because
the highest-scoring window began *inside the prior advance* and measured part of
the rally as a contraction leg.

Both produced plausible output. Neither produced *wrong-looking* output — the
scores were reasonable, the geometry drew fine, and only comparing against a
generator with known ground truth revealed the structure was in the wrong place.

The failure has two distinct costs, and the second is the serious one.

**It is structurally wrong.** A tie-break rule is not a theory of where a
flagpole ends. When the rule rather than the data decides the boundary, the
detector is reporting its own search order.

**It is look-ahead bias.** "Highest-scoring window" is a question that cannot be
answered honestly on historical data. Among a hundred overlapping windows, the
one that scores best is disproportionately the one that *happened to resolve
well* — the consolidation that stayed tight because price went on to break out,
the base whose final leg was shallow because the advance resumed. The detector
is not choosing the best structure; it is choosing the structure the future
rewarded. Nothing in the output announces this, and a backtest built on it looks
better than the strategy is.

## Decision

**A detector must not search many historical candidate windows and select the
winning geometry primarily because it maximises the detector's own quality
score.**

Structural boundaries are determined from causal market structure. The
mechanisms available:

| Boundary | Determined by |
|---|---|
| Impulse termination | The highest high between the origin and the present, computed over confirmed data only |
| Base origin | The earliest confirmed swing high at the peak level |
| Contraction transitions | Confirmed pivot alternation (high → low → high) |
| Support / resistance | Clusters of confirmed pivots, or the window extreme when no cluster exists |
| Trend-line geometry | Least-squares fit through confirmed pivots, with r² reported |
| Prior structural events | A previously detected and stored pattern |

The division of labour is the point: **scoring evaluates a structure that has
already been discovered.** It does not invent the structure by optimising over
all possible historical intervals.

### When multiple candidates are genuinely necessary

Some patterns legitimately admit several readings — a long base containing a
tighter sub-base, or a security hosting both a short flag and a longer one.
Where that is true:

1. **Candidate generation is causal.** Origins come from confirmed pivots, never
   from an arbitrary index.
2. **Candidate generation is bounded.** `max_candidates_per_pattern` caps the
   search, so an unbounded scan cannot creep back in.
3. **All materially plausible candidates are returned**, not the best-scoring
   one. Later stages choose; the detector reports.
4. **Any ordering that survives the cap is stated and depends only on data
   available at the time** — "latest pole start", "earliest high at the peak
   level" — never on the score.

Sorting the *returned* list by quality is fine and is presentation only. The
distinction is whether the score decided which structures exist.

### Documented exceptions

None currently. A future detector needing one must record it here with the
reason, and must carry a test showing what the exception costs.

## Consequences

- Every detector gains a "where does this structure begin?" question that must
  be answered structurally before any scoring is written. That is more work up
  front and it is the work.
- Some real structures are missed. A flagpole whose origin is not a confirmed
  swing low is invisible. This is the correct trade: a detector that finds every
  structure by searching every window finds many that were not there.
- Detectors report fewer, better-anchored instances. On the bull flag, removing
  the window search removed roughly two-thirds of candidates and moved the
  surviving consolidation to exactly where the generator drew it.
- **Testable.** `tests/unit/test_selection_bias.py` asserts, for every detector,
  that reported geometry matches generated ground truth rather than merely
  scoring well, and that shuffling the candidate order cannot change what is
  reported.

## Alternatives considered

**Score-select, but penalise longer windows.** A regularisation term to stop the
search sprawling. It does not address the bias at all — it changes which window
the future-informed choice lands on, and makes the bias harder to see.

**Score-select, and validate against ground truth in tests.** This is what
caught the bug, and it is not a fix: synthetic ground truth exists only in
tests, and the production path would still be choosing windows by outcome.

**Return every candidate and let a later stage decide.** Attractive, and it is
what the multi-candidate path does in the bounded case. As a general rule it
merely relocates the problem: a scorer that receives forty overlapping readings
of one chart and picks the best has performed the same optimisation one layer
up.

**Fit boundaries by optimisation with an out-of-sample split.** The statistically
principled version, and it needs labelled data and a validation harness that
Phase 4 does not have. Phase 9 may revisit it.
