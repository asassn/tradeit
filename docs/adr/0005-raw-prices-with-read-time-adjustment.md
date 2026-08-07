# ADR-0005: Store raw prices; adjust at read time, as of the clock

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 1

## Context

Most vendors offer adjusted price series, and using them is tempting: the
adjustment is already done, and indicators computed on adjusted prices are
continuous across splits.

But an adjusted series is a *function of the future*. The adjusted history of a
stock that split last week is not the history anyone could have seen before the
split. Persisting adjusted prices therefore bakes future information into past
rows — the exact leak ADR-0002 exists to prevent — and it is invisible, because
the numbers look perfectly reasonable.

There is a second, subtler problem: a split changes both price and share count.
A volume series left unadjusted shows a phantom doubling of turnover on the
ex-date, which a volume-confirmation rule will read as institutional
accumulation.

## Decision

**Store raw, unadjusted OHLCV exactly as the exchange printed it.**

**Store corporate actions as bitemporal facts** with their own `knowledge_time`
(the announcement, which normally precedes the ex-date).

**Adjust on read**, using only the actions the clock could see. A split
announced tomorrow does not adjust today's view of history.

Three policies, chosen per call:

| Policy | Adjusts | Intended for |
|---|---|---|
| `NONE` | nothing | reconciliation, order sizing, audit |
| `SPLIT_ONLY` *(default)* | splits, and volume inversely | technical analysis, pattern detection |
| `TOTAL_RETURN` | splits and dividends | performance attribution |

`SPLIT_ONLY` is the default for analysis because a dividend gap is a real price
the market traded through, and smoothing it away distorts support/resistance
levels and gap statistics. Total-return adjustment is for measuring returns, not
for finding patterns.

Volume is scaled inversely to the split ratio, so dollar volume is invariant
under adjustment. This is asserted in
`tests/unit/test_point_in_time.py::test_dollar_volume_survives_a_split_adjustment`.

## Alternatives considered

**Store both raw and adjusted columns.** The adjusted column is stale the moment
a new action arrives, so it must be recomputed across all history — expensive,
and it silently rewrites the past between backtest runs.

**Store a per-bar cumulative adjustment factor.** Same staleness problem, and
the factor is only correct as of the date it was computed.

## Consequences

- Reads cost an extra corporate-actions query and a backward pass over the bars.
  Actions are few, and the pass is O(n) with the bars already in memory.
- Two backtests run at different wall-clock times over the same period can
  legitimately differ if an action was announced in between. This is correct,
  and reports should record the run's as-of instant.
- Vendor-adjusted series must never be ingested into `ohlcv_bars`. An adapter
  that can only supply adjusted prices must declare it in
  `ProviderCapabilities`, and its data is not backtest-grade.
