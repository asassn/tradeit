# ADR-0034: Half the edge must survive out of sample

**Status:** accepted
**Date:** 2026-11-15
**Approved by:** the owner, 2026-11-15.
**Completes** [ADR-0031](0031-a-bar-is-a-test-not-a-number.md), which settled two
of four readiness thresholds, and [ADR-0029](0029-demotion-needs-two-tests.md),
which settled demotion. **This is the fourth and last.**

## Context

`PHASE_12_AUDIT_2026-10-18.md` listed four thresholds the readiness framework
would not invent. Three were settled on 2026-10-23 by expressing each as a test
against something already written, so the number came from the data at
assessment time and nothing was chosen.

**That move was not available for this one, and the reason is the point.**
`out_of_sample_degradation` asks how much fragility is tolerable. Measured
twice — `SIGNAL_SCOREBOARD` §52 and §53 — a strategy kept its direction and lost
roughly two thirds of its magnitude. Two observations describe what happened;
they cannot establish an acceptable limit, and setting one from them is fitting
the bar to the data. There is no chance baseline to standardise against either:
**a no-edge strategy has no characteristic degradation, because it has no edge
to lose.**

So it needed a judgement about tolerable disappointment, and a judgement is the
owner's to make. Asked and answered 2026-11-15.

## Decision

**A strategy that keeps less than half its annualised edge out of sample is
fragile and does not promote.**

### The metric had to change, and that is most of the work

The criterion previously pointed at `WindowResult.degradation`, which is
**in-sample total return minus out-of-sample total return**. A bar on it would
read "lost at most N percentage points", which:

* **means different things at different scales.** Losing 40 points is survivable
  for a strategy that made 60% and fatal for one that made 45%.
* **is dominated by span length.** `annualised_degradation`'s docstring records
  the measurement: on 2003-2019 anchored, mean degradation read **+34.10%**
  against +2.94% rolling, almost entirely because the in-sample span grew to
  fifteen years against a one-year test. A reader would reasonably have taken
  that as severe overfitting in one mode. On CAGR the same windows read +3.45%
  against −1.40%.

So the criterion now reads a new property, `WindowResult.edge_retained` — the
**share of the annualised edge that survived**, `out_of_sample.cagr /
in_sample.cagr`. Threshold: `AT_LEAST 0.5`.

`degradation` itself is **untouched**. Its own docstring says it is what the
harness shipped with and that changing a published definition needs a scoped
proposition. This is an addition, not a redefinition.

### It fails closed on a strategy that never worked

`edge_retained` is `None` when in-sample CAGR is not positive, and the
assessment then reports `UNRESOLVED` rather than a number. A strategy with no
in-sample edge has no edge to retain; near zero the ratio explodes into an
artefact; and a number here would let a strategy that never worked pass a
fragility bar. A negative value is **not** clamped to zero — losing money out of
sample is worse than keeping none of the edge, and the figure should say so.

`mean_edge_retained` **skips** windows without a positive in-sample edge rather
than scoring them zero: counting "no edge to lose" as "lost everything" would
drag the mean down on exactly the windows that say nothing about fragility.

### Pre-registered, which is what makes it a bar

Recorded **before** the next out-of-sample test is designed. A limit chosen after
seeing a result is a rationalisation, and the whole ladder exists to stop that.

This is a bar on **fragility**, not on profitability. `out_of_sample_clears_hurdle`
already requires the held-out result to clear the trial ledger after whatever
degradation occurred. Both must pass; neither substitutes for the other.

## Consequences

**No criterion on the ladder is UNAPPROVED any more.** Promotion to
`PAPER_TRADING` is now blocked only by missing *evidence*, never by a missing
decision — which is the state the framework was built to reach.

A test asserting the report said `OWED` was **reworked, not re-pinned**: it was
right while a threshold was unapproved and became wrong when one was approved.
Re-pinning it with a stale fixture would have made it assert that the project had
not finished something it had. It now asserts that nothing is owed, with a
companion test that builds an unapproved threshold directly so the reporting
machinery cannot go vacuous for want of a real instance — the failure mode that
has hollowed out two tests here before.

## Alternatives rejected

| | why not |
|---|---|
| **A bar on `degradation` as it stood** | Percentage points, so scale-dependent and dominated by span length. |
| **Redefining `degradation` to be a ratio** | Rewrites a published definition; needs a scoped proposition, and its docstring says so. |
| **Deriving the bar from §52 and §53** | Two observations, and fitting the bar to the only data that will test it. |
| **Clamping a negative retention to zero** | Hides the difference between keeping none of the edge and losing money. |
| **Returning 0.0 when there is no in-sample edge** | Lets a strategy that never worked pass a fragility test. |
| **Leaving it UNAPPROVED until a test is designed** | The owner was asked and answered; waiting would have been deferring a decision already made. |
