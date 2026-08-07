# ADR-0010: Write the indicator kernels rather than import them

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 3

## Context

Mature indicator libraries exist (TA-Lib, pandas-ta, and others). Using one
would have saved a few hundred lines. The brief says not to rely blindly on a
third-party library whose initialisation behaviour, missing-data handling or
look-ahead characteristics cannot be verified — so the question is whether they
can be.

Three properties this platform depends on are exactly the ones libraries differ
on, and none of them is usually documented:

**Seeding.** An EMA can be seeded from the first observation or from the SMA of
the first *n*. The choice changes every subsequent value, forever. A backtest
seeded one way and a live system seeded the other produce different signals from
identical data, and the discrepancy is small enough to look like noise.

**Wilder vs standard smoothing.** RSI, ATR and ADX are defined with Wilder's
`1/n` smoothing. Several libraries substitute a standard EMA's `2/(n+1)`,
shifting every value. Both look plausible on a chart.

**Warm-up honesty.** Some libraries emit a value as soon as one observation
exists. A 200-day average computed from 40 bars is a different indicator with
the same name, and it differs precisely in the region where instruments enter
the universe — the region a survivorship-safe backtest spends most of its time
in.

A fourth, worse, possibility: a centred rolling window is a one-word difference
in pandas (`center=True`) and produces a straightforward look-ahead leak.

Verifying these for a dependency means reading its source and pinning its
version against silent changes — which is most of the work of writing the
recursion, without the ability to test it as a first-class artifact.

## Decision

**Implement the kernels in `tradeit.analytics.kernels`**, as pure NumPy
functions with two enforced properties:

1. **Causality.** Output *i* depends only on inputs `0..i`. Asserted by prefix
   consistency: computing over `x[:k]` reproduces exactly the first *k* outputs
   of computing over all of `x`. Every kernel, four cutoffs, plus
   future-price and future-volume tampering — 274 cases in
   `tests/unit/test_causality.py`.
2. **Explicit warm-up.** `NaN` until the recursion has enough data. Never a
   number from a short window.

Seeding and smoothing choices are stated in each docstring, because the
important thing is not which convention was chosen but that it is written down
and tested.

NumPy is still a dependency, and its `mean`, `std` and `sliding_window_view` are
trusted. That is a different kind of trust: those are general numeric primitives
with no financial-domain semantics to get wrong.

## Alternatives considered

**Use TA-Lib and test its behaviour.** Would require the same tests, plus a C
dependency, plus version pinning against silent numerical changes. The tests are
the expensive part; the recursions are not.

**Use pandas rolling.** Convenient, and `center=True` is one keystroke from a
look-ahead leak that no type checker catches.

**Wrap a library behind our own interface and test the wrapper.** Sound, and
what would have been done had the kernels been genuinely complex. They are not —
the whole module is under 600 lines including documentation.

## Consequences

- The causality property test is possible at all, because we control the
  functions being tested. Verified to catch centred windows, full-sample
  z-scores, and off-by-one reads.
- Numbers may differ slightly from a charting package's, since those make their
  own seeding choices. Ours are stated; a comparison is a documentation lookup
  rather than an investigation.
- New indicators must be added here, with a warm-up declaration and a registry
  entry. That is friction, and it is the point: an indicator with an undeclared
  warm-up is one whose backtest cannot be trusted.
- Performance is ours to own. Measured at ~7 ms for 58 features over 760 bars,
  which projects to about 30 seconds for a 4,000-name universe single-threaded.
