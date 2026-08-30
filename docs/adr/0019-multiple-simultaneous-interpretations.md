# ADR-0019: One chart may carry several pattern readings at once

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 4

## Context

Twelve detectors run over the same bars. They overlap by construction: a bull
flag and a pennant differ in one dimension; a flat base and a VCP differ in
whether the tightening is progressive; a tight consolidation shares geometry with
almost everything.

The instinct is to force exclusivity — pick the best-scoring family and report
one label. It is the wrong instinct, for two reasons.

**The premise is false.** A window that is a tight consolidation at 78 and a bull
flag at 74 genuinely is both. Chart patterns are not a partition of chart space;
they are overlapping descriptions with different emphases, and a structure
satisfying two definitions satisfies two definitions.

**The choice would be made in the worst possible place.** A detector picking
"the" label is choosing on quality alone, with no view of the portfolio, the
regime, the instrument's liquidity, or what the other eleven detectors found. A
later stage has all of that. Deciding early throws it away and cannot be undone,
because the discarded readings are not stored.

## Decision

**Every materially plausible reading is reported. Nothing forces exclusivity.**

- The scanner returns all qualifying instances from all enabled detectors.
- Coexistence is recorded as a `RELATED_TO` relationship edge when two families
  describe nearly the same span (ADR-0016, `PATTERN_RELATIONSHIPS.md`).
- Sorting the returned list by quality is **presentation only**. Selection among
  structures already happened causally in discovery (ADR-0014), and sorting
  afterwards cannot change what exists.
- **Scores are not comparable across families.** Each is relative to its own
  structural definition. No cross-family calibration exists, and establishing one
  requires labelled real data.

### Where exclusivity *is* enforced

Only where the definitions are genuinely incompatible, and only structurally:

- **Same family, one containing the other.** `merge_overlapping` supersedes the
  smaller — two rows describing one structure rather than two readings of it.
- **A double bottom whose level did not hold.** A confirmed low between the two
  that breaks the level means the structure is something else, most obviously an
  inverse head and shoulders. This is a definitional exclusion, not a score one.
- **A high tight flag that is merely an ordinary flag.** The magnitude floor is a
  discovery gate, so a 40% advance produces no instance rather than a weak one.

## Consequences

- **Consumers must handle multiple readings.** A screen taking "the pattern" for
  an instrument needs its own rule, and that rule is now visible in the screen
  rather than buried in a detector.

- **Instance counts are higher than a single-label system.** ~10 per instrument
  per daily scan across twelve families. Relationship edges are ~5 per instance,
  which is the fastest-growing storage term (`PATTERN_PERFORMANCE.md` §7).

- **The competing-pattern matrix becomes a first-class artefact** rather than a
  diagnostic. It records which families coexist, what each scored, and — the part
  that matters — which component readings separate them. Two families agreeing is
  uninteresting; two families agreeing while disagreeing about roundness is the
  whole story.

- **A margin is reported.** How far the top reading sits above the next is what a
  later stage most needs, because a small margin says the family label is the
  least reliable part of the reading.

- **What would be a defect**: a competitor scoring highly with *no* component
  separating it. That would mean two definitions are the same definition under
  two names, and the matrix is where it would show up.

## Alternatives considered

**Winner-takes-all by quality.** Compares incomparable scores and discards
information irreversibly.

**A hand-written precedence order.** ("A cup beats a double bottom.") Encodes a
preference as though it were a fact, and would need re-deciding for all 66 pairs.

**Merge overlapping readings into a single composite "structure" object.** Loses
the component breakdowns, which are the only thing that distinguishes the
readings.

**Train a classifier to pick the family.** Needs labelled data. When that exists
it becomes a reasonable proposal, and the stored multi-reading history is exactly
the training input it would want — which is another reason not to discard it now.
