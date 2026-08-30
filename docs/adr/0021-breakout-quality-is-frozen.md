# ADR-0021: Breakout quality is computed once and frozen; confirmation is not

**Status:** Accepted · **Date:** 2026-08-09 · **Phase:** 5

## Context

Item 18 of the Phase 5 brief asks for two scores and gives the example:

> A technically strong first breakout candle: Breakout Quality = 94,
> Confirmation = 42, because follow-through has not happened yet. Two sessions
> later: Breakout Quality = 94, Confirmation = 88.

Notice what the example asserts: the quality figure is the *same number* on both
days. That is not an accident of the example. If quality drifted as evidence
arrived, the pair would be two views of one blended score and a reader could not
tell which of them had moved — which is the whole distinction the two scores
exist to preserve.

There is a second, larger reason. Item 31 requires that "future volume does not
improve historical breakout quality" and "future retests cannot retroactively
change the original breakout score". Both can be enforced by test, and tests of
this kind are weak: they check the paths someone thought to check. A design in
which the score is recomputed each session from a widening window is one careless
line away from a leak that every existing test still passes.

## Decision

**`breakout_quality` is computed on the session of the first qualifying close,
from that bar and information predating it, and never recomputed.**

The inputs are a closed set — `QualityInputs` — and the type is the place to
look when reviewing whether the guarantee still holds: it contains no field
dated after the breakout bar, and adding one would be the way the guarantee gets
broken. The engine computes it inside a single branch of `_finish`, gated on
`working.state is CLOSED_ABOVE and event.first_qualifying_close_session is None`,
and every later observation carries the stored value forward.

**`confirmation_score` is recomputed every session from post-breakout evidence
only.** `ConfirmationInputs` contains nothing about the breakout bar, and
`ConfirmationWeights` has no component named for one. Folding the bar's own
quality back into confirmation would correlate the two scores by construction.

Two consequences of the freeze are worth stating because they look like bugs and
are not:

- The relative-volume figure the momentum path tests is read from the **frozen
  component**, not from today's measurements. Using today's would let an event
  whose breakout came on half its average volume satisfy a volume requirement
  four days later on an unrelated busy session.
- A pre-breakout event reports `breakout_quality` 0. Not "unknown" and not the
  approach score: no breakout has occurred, so there is nothing to score.

## Consequences

**Good.** Two of the nine causality properties are true by construction rather
than by check. The item-18 distinction is preserved exactly: `test_quality_and_
confirmation_move_independently` asserts equality on quality across a
five-session gap while confirmation rises. The `future_data_trap` scenario — a
weak breakout bar followed by a very strong advance — scores a median quality of
25.8, which is what the bar deserved.

**Costs.** The score cannot improve when better context arrives late. If a
benchmark series is backfilled a week after the breakout, the relative-strength
component stays unavailable on that event and its coverage stays low. That is
the honest outcome — the score describes what was knowable at the breakout — but
it means a coverage figure can be permanently low for a reason that has since
been fixed.

Re-scoring an event under a new scorer version requires replaying it rather than
patching it. The event stores `scorer_version` and both config digests so the
replay is reproducible, and a mismatch is visible rather than silent.

**Rejected.** *Recomputing quality each session over a fixed backward window.*
Superficially similar and materially different: the window would include
post-breakout bars, so a strong follow-through would improve the breakout bar's
own score.

*One blended score.* Every consumer would then have to guess how much of a 68
was the bar and how much was the days after it.
