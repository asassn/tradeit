# The mean of `edge_retained` is dominated by its smallest denominator

**Measured 2026-11-17** against `robustness_record.sqlite`, while building the
walk-forward view. **Nothing has been changed.** `edge_retained`,
`mean_edge_retained` and the ADR-0034 threshold are untouched — this is a
finding and a proposition, because altering a published metric behind an
approved threshold needs an explicit scoped decision.

---

## What was measured

26 walk-forward windows, 21 of which have a positive in-sample CAGR and
therefore a defined `edge_retained`.

| | |
|---|---|
| **Mean** | **4.212** |
| Median | 1.682 |
| Minimum | −3.822 |
| Maximum | **42.381** |
| Clearing the 0.5 bar | **13 of 21 (62%)** |
| **Negative — made money in sample, lost it out** | **8 of 21 (38%)** |
| Mean excluding the three largest | **0.703** |

The three windows driving the mean:

| Window | In-sample CAGR | Out-of-sample CAGR | `edge_retained` |
|---|---|---|---|
| `wf-rolling-7` | **0.5139%** | 21.78% | **42.38** |
| `wf-anchored-7` | **0.7671%** | 21.78% | **28.39** |
| `wf-rolling-11` | 1.0453% | 5.25% | 5.02 |

## Why it happens

`edge_retained` is `oos_cagr / is_cagr`. The denominator is an in-sample CAGR,
and a window that **barely made money in sample** turns an ordinary
out-of-sample year into an enormous ratio. `wf-rolling-7` made 0.51% in sample
— essentially nothing — and 21.78% out, which is not "the edge survived
forty-two times over". It is division by something near zero.

An arithmetic mean of such ratios is dominated by whichever window had the
smallest positive denominator. Removing three of twenty-one windows moves the
mean from **4.21 to 0.70** — a factor of six.

## Why it matters

`readiness.criteria` declares `out_of_sample_degradation` with
`metric="edge_retained"`, `AT_LEAST`, `0.5`, approved by the owner on
2026-11-15 (ADR-0034). If a readiness assessment is ever handed
`WalkForwardReport.mean_edge_retained` as that evidence, it reads **4.21 and
passes comfortably** — on a record where **eight of twenty-one windows lost
money out of sample while making it in sample**.

> "Mean edge retained 4.21, clears the 0.5 bar" is a true sentence and a
> dangerously flattering summary.

No assessment is currently wired to this record, so nothing has been decided
wrongly. The trap is live rather than sprung.

**The existing guard does not cover this.** `edge_retained` already returns
`None` when in-sample CAGR is **not positive**, which is the right fail-closed
rule for a strategy with no edge. It says nothing about a strategy with an
edge of 0.51%, where the ratio is defined, enormous and meaningless.

## What has been done

The API reports the distribution beside the mean and never serves one without
the other: median, count clearing the bar, count negative, and the smallest
in-sample CAGR among the resolved windows — the number that explains the tail.
The walk-forward screen leads with **"13 of 21 windows keeping half the edge"**
and shows the mean as a secondary figure carrying its own warning.

That is a presentation fix. It stops the number misleading a reader; it does
not stop it misleading an automated assessment.

## The decision owed

Three options, and **the second is recommended**.

**1. Leave it.** The metric is honest about single windows and only the mean is
distorted; nothing consumes the mean today. *Against:* the readiness criterion
is the obvious future consumer, and it would read the mean.

**2. Assess the criterion on the share of windows clearing the bar, not the
mean.** "At least half the windows kept at least half the edge" is the same
sentence ADR-0034 approved, applied window-by-window instead of to an average.
A count cannot be dragged by one near-zero denominator, it needs no new
threshold beyond the 0.5 already approved, and it is what a reader checking
the table does by eye. *Against:* it is a second threshold in spirit — how
many windows must clear — even if 50% is the natural reading of "half the
edge must survive".

**3. Require a minimum in-sample edge before the ratio is defined.** A window
whose in-sample CAGR is below some floor becomes UNRESOLVED, exactly as a
non-positive one does. *Against:* it needs a floor nobody has chosen, and
choosing one from this data is fitting the bar to the sample — the thing
ADR-0031 and ADR-0034 were careful not to do.

**Recommendation: option 2.** It introduces no number that is not already
approved, it is robust to the artefact by construction, and it reports the
same fact the owner actually approved — that half the edge must survive —
rather than an average that can be true while most windows fail.

**This was the owner's decision.** It changes how an approved criterion is
evaluated, which the project's rules reserve for an explicit scoped
proposition.

---

## Decided: option 2, 2026-11-17

The owner chose **counting the windows**, recorded in
[`ADR-0038`](adr/0038-count-the-windows-not-the-mean.md).
`readiness.criteria.out_of_sample_degradation` now reads
`edge_retained_window_share`, supplied by
`WalkForwardReport.edge_retained_window_share(bar)`. The bar stays 0.5 and
does both jobs — the share each window must retain, and the share of windows
that must retain it. `mean_edge_retained` is kept and still reported; it is
simply no longer what the criterion is assessed on.

On this record the honest reading is **13 of 21 resolved windows (62%) kept
half their edge**, and 8 lost money out of sample.
