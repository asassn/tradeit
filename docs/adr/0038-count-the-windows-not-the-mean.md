# ADR-0038: Count the windows, not the mean

**Status:** accepted
**Date:** 2026-11-17
**Approved by:** the owner, 2026-11-17, after the finding in
[`EDGE_RETAINED_MEAN_2026-11-17.md`](../EDGE_RETAINED_MEAN_2026-11-17.md) was
put to him.
**Amends** [ADR-0034](0034-half-the-edge-must-survive.md) in how its threshold
is **evaluated**. The bar itself — half the edge must survive — is unchanged,
and so is `edge_retained`. ADR-0034 is not superseded.

## Context

ADR-0034 approved `edge_retained >= 0.5`: a strategy keeping less than half
its annualised out-of-sample edge is treated as fragile and does not promote.
It was the last of four thresholds and the only one that could not be derived,
so it was a judgement the owner made.

**Building the walk-forward view surfaced a way for that bar to be passed by a
record that plainly fails it.**

`edge_retained` is `oos_cagr / is_cagr`. The denominator is an in-sample CAGR,
and a window that **barely made money in sample** turns an ordinary
out-of-sample year into an enormous ratio. Measured 2026-11-17 against
`robustness_record.sqlite`:

| | |
|---|---|
| Resolved windows | 21 of 26 |
| **Mean `edge_retained`** | **4.212** |
| Median | 1.682 |
| Largest single window | **42.381** (`wf-rolling-7`: **0.51%** in, 21.78% out) |
| Clearing the 0.5 bar | **13 of 21 (62%)** |
| **Lost money out of sample while making it in sample** | **8 of 21 (38%)** |
| Mean excluding the three largest | **0.703** |

Removing three windows of twenty-one moves the mean from **4.21 to 0.70** — a
factor of six.

> "Mean edge retained 4.21, clears the 0.5 bar" is a true sentence and a
> dangerously flattering summary of a record in which eight of twenty-one
> windows lost money out of sample.

**The existing guard does not cover this.** `edge_retained` already returns
`None` when in-sample CAGR is **not positive**, which is the right fail-closed
rule for a strategy with no edge at all. It says nothing about a strategy with
an edge of 0.51%, where the ratio is defined, enormous and meaningless.

No readiness assessment was wired to this record, so nothing had been decided
wrongly. The trap was live rather than sprung.

## Decision

**`readiness.criteria.out_of_sample_degradation` reads the share of windows
that cleared the bar, not the mean of the ratios.** Its `metric` becomes
`edge_retained_window_share`, supplied by
`WalkForwardReport.edge_retained_window_share(bar)`.

"At least half the windows kept at least half the edge" is the same sentence
ADR-0034 approved, applied window by window instead of to an average.

**It introduces no number that was not already approved.** `0.5` does both
jobs: the share each window must retain, and the share of windows that must
retain it. That is what made this option preferable to the alternatives — a
count is robust to the artefact *by construction* rather than by a second
judgement.

**The denominator is the resolved windows, not all of them.** A window with no
in-sample edge stays `UNRESOLVED`, for exactly the reason `edge_retained`
already gives, and counting it as a failure would penalise a strategy for
windows that say nothing about fragility — the same error as counting it zero
in the mean, in the opposite direction. No resolved window at all returns
`None`: nothing was measured, so nothing failed.

**`mean_edge_retained` is kept and still reported.** It is a published figure
and removing it would rewrite history; it is simply no longer what the
criterion is assessed on.

### Rejected

**Leave it.** The screen already shows the distribution and leads with the
count, so a human reader cannot be misled today. *Against:* the readiness
criterion is the obvious future consumer and it would read the mean. A trap
that depends on nobody wiring an obvious thing is not defused.

**Require a minimum in-sample edge before the ratio is defined.** Cleanest in
principle — a window below some floor becomes `UNRESOLVED`, exactly as a
non-positive one does. *Against:* it needs a floor nobody has chosen, and
choosing one from these twenty-one windows is fitting the bar to the sample,
which is precisely what ADR-0031 and ADR-0034 were careful not to do.

## Consequences

The walk-forward screen leads with **"13 of 21 windows keeping half the
edge"**, and the mean appears beside it carrying its own warning. The API
serves the median, the count clearing the bar, the count that went negative,
and the smallest in-sample CAGR among resolved windows — the number that
explains the tail — and **never serves the mean alone**.

Four mutants hold the change: the share computed as a mean, unresolved windows
counted as failures, no-resolved-window scoring zero instead of `UNRESOLVED`,
and the criterion reading `edge_retained` again.

**This does not make the strategy look better or worse than it is; it makes
the summary match the windows.** On the current record the honest reading is
that 62% of resolved windows kept half their edge and 38% lost money out of
sample — which is a far more useful input to a promotion decision than 4.21.
