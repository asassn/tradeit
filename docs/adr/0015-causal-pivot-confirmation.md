# ADR-0015: A pivot is not knowable on the day it happens

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 4

## Context

Every chart pattern is built from swing highs and swing lows. The naive
definition — a bar whose high exceeds its `left_bars` neighbours before and
`right_bars` neighbours after — is trivially computable over a completed series
and is **not computable in real time**, because the bars after it have not
happened yet.

Almost every pattern-detection library gets this wrong, and the failure is
invisible in backtests: a swing high identified on the day it occurred produces
resistance levels, flagpole ends and base origins that a live system could not
have known. The resulting backtest is not optimistic by a little. It is
answering a different question.

The subtler version bites even when the first is handled. Suppose a detector
correctly waits for confirmation but then, once confirmed, *redraws* the
structure using the pivot's original date. The boundary is now anchored to a
session on which nobody could have drawn it, and the pattern's history says it
existed before it was knowable.

## Decision

**A pivot carries two dates and detectors may only use the second.**

- `session_date` — when the extreme occurred.
- `confirmed_date` — when it became knowable, `right_bars` sessions later.

Three rules follow:

1. **Structure is defined only from confirmed pivots.** The last `right_bars`
   sessions of any series are a **provisional tail**. `DetectionInputs.structure_end`
   applies this centrally, so no detector has to remember it.

2. **The provisional tail may inform state, never geometry.** Price closing below
   a stored support level today is a legitimate state change. Price making a new
   high today may not move a flagpole's end, because that high is not yet
   confirmed and may not survive.

3. **Confirmation lag is a cost, not a parameter to minimise.** Raising
   `right_bars` makes pivots more reliable and the detector slower to see them;
   lowering it makes the detector twitchier. It is the price of not looking at
   the future, and tuning it to make a detector "find more" is tuning it to
   leak.

## Consequences

- **Every detector is late.** A structure that completed on Monday is reported on
  Thursday. This is correct and it is what a live system would have had.

- **A forming structure is systematically understated.** The clearest case: the
  final contraction of a VCP base is never confirmed while the base is still
  forming, so the detector always evaluates the progression **one leg behind**. A
  textbook three-leg VCP scores its `contraction_progression` at about 54 while
  forming and about 96 once the last leg confirms. That 42-point jump is not
  instability; it is the confirmation lag becoming visible.

- **Tests that assert on unconfirmed structure are vacuous.** This was found
  during the completion gate: a cohort labelled the VCP "non-monotone" on the
  strength of a widening leg that sat entirely in the provisional tail. The
  detector scored it as a clean two-leg progression and was right. The *cohort*
  was wrong, and the correction was to give it trailing bars.

- **Perturbation results must separate causes.** A newly confirmed pivot changing
  a structure's start is a legitimate discontinuity. `perturbation.py` classifies
  jumps by cause for this reason, and only jumps with *no* structural cause are
  treated as findings.

- **Testable.** `tests/unit/test_causality.py` and
  `tests/unit/test_selection_bias.py::TestBoundaryProvenance` assert that no
  structure starts inside the provisional tail and that a structural start never
  moves once later bars arrive.

## Alternatives considered

**Use unconfirmed pivots and mark them provisional.** Tempting, and it pushes the
decision onto every consumer. In practice a field named `provisional` gets
ignored exactly once, in the code path that matters.

**Shorten `right_bars` to one.** Reduces the lag and produces pivots that reverse
constantly, which surfaces as pattern identities churning day to day — the exact
failure the identity design exists to prevent.

**Confirm by ATR displacement rather than by bar count.** A pivot is confirmed
once price has moved a given multiple of ATR away from it. This is defensible and
was not adopted: it makes confirmation lag data-dependent and therefore
instrument-dependent, which complicates every cross-sectional comparison for a
benefit that has not been demonstrated. Worth revisiting with real data.
