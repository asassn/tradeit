# ADR-0017: Pattern quality and evidence coverage are two numbers

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 4

## Context

A detector scores a structure on several components — geometry, volume,
volatility, relative strength, context. In production, some of those components
are routinely unavailable: no benchmark series for a newly listed instrument, no
volume for a thin one, insufficient history for a prior-trend measurement.

Three ways to handle a missing component, two of them wrong:

**Score it zero.** A flag with no benchmark data becomes a bad flag. The
detector has confused *absence of evidence* with *evidence of absence*, and the
instrument is penalised for a data gap.

**Drop it from the denominator silently.** The composite then means "good on
whatever we happened to measure", and a flag scoring 88 on two components is
indistinguishable from one scoring 88 on eight.

**Fold coverage into the score.** Multiply quality by coverage and report one
number. A structure that is excellent on partial evidence and one that is
mediocre on complete evidence both arrive as 70, and nothing downstream can tell
which — including the human trying to decide whether to trust it.

## Decision

**Two numbers, 0–100, deliberately not multiplied.**

- **Quality** — how good is this structure, on the components that could be
  measured? Unavailable components are excluded from both numerator and
  denominator.
- **Evidence coverage** — what fraction of the intended evidence weight was
  actually available?

Every unavailable component carries a **mandatory reason**. The constructor
refuses an unexplained gap, and a helper exists specifically to make supplying
one the path of least resistance.

**Coverage gates state, not score.** Below a detector's declared
`minimum_evidence_coverage`, an instance stays FORMING regardless of its quality,
with contradicting evidence saying why. A composite computed from a fifth of its
intended evidence is describing something other than the pattern, and should not
claim maturity — but it is still reported, with the low coverage on its face.

**Required versus optional components are a separate axis.** A missing
*required* component suppresses the instance entirely; a missing *optional* one
reduces coverage. The first says the pattern cannot be classified; the second
says it was classified on less than we wanted.

## Consequences

- Every consumer receives two numbers and must decide what to do with both. That
  is the intent: the decision belongs to whoever is acting on the pattern, not
  to the detector.

- A later phase **may** choose to combine them. This ADR does not forbid that; it
  forbids doing it here, where the information needed to choose the combination
  does not exist.

- Coverage is a useful review-queue axis in its own right. `LOW_COVERAGE` selects
  examples where quality and coverage diverge, which is where human labels are
  most informative.

- **Testable.** Every detector's suite asserts that every unavailable component
  states a reason, that coverage is strictly positive, and that required
  components are marked as such.

## Alternatives considered

**One number, quality × coverage.** Loses the distinction that motivates the ADR.

**One number, with coverage as metadata nobody reads.** Functionally the same as
folding, with extra ceremony.

**Refuse to emit an instance below full coverage.** Would make the system blind
to exactly the instruments where data is thin, which is a large fraction of any
real universe and disproportionately the interesting part of it.

**Impute missing components from cross-sectional averages.** Produces a score
containing information the instrument did not supply, and the imputation would
be indistinguishable from measurement in the stored output.
