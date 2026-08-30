# ADR-0011: Multi-benchmark relative strength with an explicit ranking roster

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 3

## Context

Relative strength has two distinct failure modes, and they need different
defences.

*Benchmark choice.* Measuring every security against a single index discards
information. A growth name beating SPY while lagging QQQ is in a different
position from one beating both — the first is strong against the market and
weak against its actual peer group. Collapsing that early makes it
unrecoverable later.

*Cross-sectional leakage.* Percentile ranks are computed across the universe on
a date, and the obvious implementation ranks against the instruments the
database currently holds. That silently excludes every company that later
delisted — disproportionately the poor performers. The resulting ranks are a
different statistic from the one they claim to be, and nothing in the output
reveals it.

## Decision

**Compare against several benchmarks.** `SPY`, `QQQ` and `IWM` by default, all
configurable, all stored separately in `relative_strength_values` dimensioned by
`(instrument, session, benchmark, lookback)`. `score_benchmark` names the one
used for the headline 0-100 score; the others remain queryable.

**Both a ratio and a difference measure.** `relative_performance` is
`(1+r_stock)/(1+r_bench) - 1`, which is compounding-correct; `excess_return` is
the simple difference. Over a period when the benchmark fell 20% and the stock
fell 10%, these are +12.5% and +10 points. They answer different questions and
are stored separately rather than one being derived on demand.

**The ranking roster is an argument, never a query.**
`RelativeStrengthEngine.rank_cross_section` takes the eligible universe for the
date and **raises** if handed a value for an instrument outside it. The roster
comes from `UniverseMembership` intervals, which include the casualties.

That last point is the load-bearing one. Making the roster a parameter means the
survivorship question is asked at every call site, visibly, rather than being
answered implicitly by whatever the database happens to contain.

**Below a minimum peer count, no rank is emitted.** A percentile over four names
is a number, not a rank, and a screen that ranks a security "first out of three"
will happily act on it.

## Alternatives considered

**One canonical benchmark.** Simpler storage and a single score. Rejected
because the multi-benchmark disagreement is signal, and reconstructing it later
would require recomputing history.

**Let the engine fetch the universe itself.** Fewer arguments, and it would put
the survivorship decision inside a function nobody reads, where a later
refactor could quietly change it to "active instruments".

**Rank against a fixed index membership (e.g. S&P 500 constituents).** Better
than today's actives, and still requires point-in-time constituent data — the
same vendor dependency, with a narrower universe.

## Consequences

- Storage is roughly 12x the indicator table for the default 3 benchmarks and 4
  lookbacks, which is why `relative_strength_values` is partitioned monthly.
- Callers must assemble the roster. That is deliberate friction on the operation
  most likely to introduce survivorship bias.
- A test asserts the roster materially changes the answer: the same instrument
  on the same date moves more than 10 percentile points depending on whether the
  eventual casualties are included.
- The first implementation counted the peer set per instrument — O(n²), measured
  at 136 seconds per session across the configured benchmarks and lookbacks,
  roughly 28 hours over a three-year backtest. It now sorts once and binary
  searches: 0.2 seconds per session. A benchmark test guards the regression.
