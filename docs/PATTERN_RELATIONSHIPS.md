# Pattern Relationships

How two pattern instances relate, and why the vocabulary is exactly six edges.

---

## 1. The six edges

| Edge | Direction | Meaning |
| --- | --- | --- |
| `CONTAINS` | A → B | A's span holds B's. The multi-timeframe case. |
| `NESTED_IN` | A → B | The inverse. A sits inside B. |
| `OVERLAPS` | symmetric | Shared span, neither contains the other. |
| `RELATED_TO` | symmetric | Two readings of **the same** geometry. |
| `SUPERSEDED_BY` | A → B | A was replaced by B under a new identity. |
| `DERIVED_FROM` | A → B | A is a composite defined by B. |

---

## 2. Why not fewer

The gate invites a simplification argument. Here it is, edge by edge.

**Collapsing `CONTAINS`/`NESTED_IN` into `OVERLAPS` loses direction**, and
direction is the only thing distinguishing a weekly structure holding a daily one
from two structures that happen to share bars. Multi-timeframe analysis is
exactly the case where that distinction carries the meaning: *Weekly VCP #1001
CONTAINS Daily Bull Flag #1874* is a statement about scale, and an undirected
edge cannot make it.

**Collapsing `RELATED_TO` into `OVERLAPS` loses the distinction between "two
structures" and "two names for one structure".** A window that is a tight
consolidation at 78 and a bull flag at 74 is one piece of chart. Calling that
"overlap" says two things share bars, which is true and useless; calling it
`RELATED_TO` says a later scoring stage has a choice to make. Preserving that is
the entire point of the competing-pattern work.

**`DERIVED_FROM` could in principle be `CONTAINS`**, and it is the closest call.
A base-on-base instance does geometrically contain the two bases it describes.
But it is also *defined* by them — the composite exists because they exist — and
one edge cannot say which relationship is meant. Without a separate edge the
composite's provenance is unrecorded and a reader cannot get from the composite
back to the parts that justified it.

**`SUPERSEDED_BY` is a lifecycle event, not a geometric one.** It is the only
edge that closes a history, and merging it with containment would make "this
structure ended" indistinguishable from "this structure is inside another".

---

## 3. Why not more

Every additional edge type is an argument waiting to happen at the point of use,
and an edge nobody can classify confidently is an edge nobody should trust.

Three that were considered and rejected:

- **`PRECEDES`** — derivable from the dates already stored. An edge that adds no
  information adds only the chance of disagreeing with the dates.
- **`CONFIRMS`** — a judgement Phase 4 does not make.
- **`INVALIDATES`** — likewise, and worse: it would encode a causal claim about
  one structure breaking another, which nothing here can establish.

---

## 4. Derivation is causal and reproducible

`derive_relationships(instances, as_of)` is a **pure function** of the instances
it is given. Replaying a session reproduces exactly the edges that session
produced, and no edge can depend on a bar the session could not see — passing
instances that postdate the boundary raises rather than silently backdating.

Every edge carries `as_of`. This is not decoration:

> A relationship discovered on Friday is not backdated to Monday. It did not
> exist on Monday, and recording it as though it did would be the same
> retroactive rewriting the observation history exists to prevent.

`established_at` (when the row was written) and `as_of_session` (the knowledge
boundary it was derived under) are different facts. A backfill written today about
a session six months ago carries today's timestamp and last spring's boundary,
and only the second says whether the edge was legitimately derivable then.

---

## 5. The classification rule

Order matters and encodes the priorities:

1. **Different timeframes with real overlap → containment**, coarser containing
   finer. Checked first because two structures at different scales are never
   "alternative readings" of each other; they are statements at different
   resolutions.
2. **Near-identical spans on one timeframe → `RELATED_TO`.** Two names for one
   piece of chart. Only meaningful across families — the same detector producing
   two near-identical readings is a duplicate, and that is the tracker's problem.
3. **One span largely inside the other → containment.**
4. **Partial overlap → `OVERLAPS`.**

`SUPERSEDED_BY` and `DERIVED_FROM` are **never** inferred from geometry.
Supersession is a lifecycle event the tracker decides; derivation is a fact only
the composite's detector knows and nothing else can reconstruct.

### Thresholds

| Constant | Value | Why |
| --- | ---: | --- |
| `CONTAINMENT_THRESHOLD` | 0.80 | high, because partial overlap is a genuinely different statement and blurring the two makes both useless |
| `OVERLAP_THRESHOLD` | 0.25 | below this the structures merely happen to be on the same instrument, which is not a relationship |
| `SAME_GEOMETRY_THRESHOLD` | 0.85 | required in **both** directions, so a small structure inside a large one is never mistaken for a second name for it |

### Overlap is reported from both sides

`span_overlap` returns a pair, not a number, because **the asymmetry is the
information**. `(1.0, 0.2)` is a small structure wholly inside a large one;
`(0.6, 0.6)` is two structures of similar size sharing half their bars. Those are
different relationships and one number cannot distinguish them.

---

## 6. One direction is stored

Only one direction per pair is emitted; the inverse is derivable via
`PatternRelation.inverted()`. Storing both would create two rows that can
disagree after an edit, and there is no mechanism that would notice.

---

## 7. Multi-timeframe identity

A daily bull flag and a weekly bull flag on the same instrument are **two
patterns**. Timeframe is part of the identity hash, so this holds by design
rather than by anyone remembering it.

Geometry overlap does not merge them. Two structures covering the same calendar
span at different scales are two statements, and merging them would erase the
fact that they are statements at different resolutions — which is the only reason
to compute both.

Cross-timeframe edges are derived at the `scan_timeframes` level, because they
are the one thing no single-timeframe scan can see.

---

## 8. Worked examples

| Situation | Edge |
| --- | --- |
| Weekly VCP spanning a daily bull flag | Weekly `CONTAINS` Daily |
| Daily tight consolidation inside a weekly flat base | Daily `NESTED_IN` Weekly |
| One window read as both a flat base and a bull flag | `RELATED_TO` |
| A later VCP whose span partly covers an earlier tight consolidation | `OVERLAPS` |
| Base-on-base and the two bases that define it | Composite `DERIVED_FROM` each base |
| A small flag absorbed into a larger one of the same family | Small `SUPERSEDED_BY` large |

---

## 9. Storage

`pattern_relationships` holds directed edges with a unique constraint on
`(from, to, relationship)` and a check that a pattern cannot relate to itself.

Edges rather than columns on `patterns`, because the relationships are
many-to-many and directional, and because a weekly VCP containing three daily
flags is a perfectly ordinary situation that a `parent_id` column would model
badly.

**Volume note.** Relationship derivation is quadratic in the instances found per
instrument per session. Measured at roughly five edges per instance on a
twelve-detector daily scan, which projects to ~200k rows/day on a 4,000-name
universe. `PATTERN_PERFORMANCE.md` carries the estimate and the options if that
proves too much.
