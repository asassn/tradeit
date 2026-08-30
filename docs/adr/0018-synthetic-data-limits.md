# ADR-0018: Synthetic corpora prove code properties, never market accuracy

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 4

## Context

Phase 4 was built and validated against seeded synthetic series, because market
data egress is blocked in this environment and the Phase 3 data gate remains
open. The corpus is large — 1,000 series per cohort across seven cohort kinds
for each of twelve families — and it produces precise-looking numbers.

Precise-looking numbers are the hazard. A detector scoring 96 on its own
generator has been shown almost nothing: **the generator draws what the detector
looks for**, so agreement on positives is close to tautological. A reader
encountering "cup and handle: 96th percentile 97.3" without context will take it
as accuracy, and it is not.

## Decision

**No claim about real-world precision, recall or profitability may rest on
synthetic data.** Every report says so at the top, and the claim boundary is
explicit:

### What synthetic corpora *can* establish

- **Internal consistency** — the detector finds what its definition describes.
- **Ordering** — clean scores above moderate above borderline above broken, on
  medians.
- **Monotonicity** — a dimension pushed the wrong way never improves the
  component that measures it.
- **Stability** — small input perturbations produce small score changes, and
  large jumps have structural causes.
- **Discrimination against a *stated* alternative** — a V-bottom against a cup, an
  ordinary flag against a high tight flag. This is a claim about the two
  definitions, not about their frequency in markets.
- **Absence of leaks** — causality, selection bias, identity collisions,
  incremental/full-replay equivalence.

All of those are properties of **the code**.

### What they cannot establish

- Precision or recall on real charts.
- Base rates — how often a structure occurs.
- Whether a pattern predicts anything.
- Whether the weights are right.
- Cross-family calibration.

### Corollaries

**Overlapping distributions are reported, not tuned away.** Where an adversarial
cohort scores highly, the finding is published with its explanation. Adjusting a
threshold until the number improved would fit the detector to its own corpus, and
the number would then describe the corpus.

**A synthetic result may not justify a weight change.** Only human labels and
empirical outcomes on real data can. This is why the cup's `bottom_roundness`
weight has not been raised despite a V-bottom compositing at 86: there is
currently no evidence about what the weight should be, only a preference.

**A cohort that asserts something the detector cannot causally see is a defect in
the cohort.** Found twice during the gate — the VCP non-monotone cohort and the
intraday generator that emitted 15-minute bars labelled as minute bars, which
made every downstream aggregation test the identity function.

## Consequences

- The Phase 3 real-data validation gate **stays open**, and Phase 4 completion
  does not close it.
- `PATTERN_LABELING.md` §5 specifies the protocol to run when data is available,
  including sampling rules that keep future returns out of the structural
  labelling decision.
- Every generated report carries the disclaimer in its header rather than in a
  footnote.
- Detector defaults are documented as **defensible starting points, not fitted
  values**, so Phase 9 — which is allowed to fit, with an out-of-sample harness —
  knows what it is arguing against.

## Alternatives considered

**Bootstrap from real data via a vendor with a free tier.** Blocked by the same
egress policy. Recorded in the Phase 3 gate document with the recommended vendor.

**Fit generator parameters to published stylised facts about equity returns.**
Would make the series more market-like without making agreement any less
tautological — the detector would still be finding what the generator drew.

**Skip synthetic validation and wait for real data.** Would have shipped twelve
detectors with none of the causality, selection-bias, identity or stability
defects found. Six real defects were caught by synthetic testing during this
gate alone.
