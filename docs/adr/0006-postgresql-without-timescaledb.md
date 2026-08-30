# ADR-0006: PostgreSQL with native partitioning; no TimescaleDB

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 2

## Context

The platform stores time series, and TimescaleDB is the obvious default for
time series in PostgreSQL. It deserves an explicit decision rather than an
implicit one, because adopting an extension is easy and removing one is not.

Projected volumes for a 4,000-name US equity universe:

| Table | Rows/year | Ten years |
|---|---|---|
| `ohlcv_bars` (daily) | ~1M | ~10M |
| `indicator_values` | ~40M | ~400M |
| `fundamental_facts` | ~1M | ~10M |
| `system_logs` | ~20M | retention-bounded |

Writes are one batch after the close, not a continuous stream. Reads are
overwhelmingly "one instrument, a date window, bounded by `knowledge_time`" or
"one date, the whole universe, for cross-sectional ranking".

## Decision

**Plain PostgreSQL 16 with native declarative partitioning.**

`indicator_values` is range-partitioned monthly on `session_date`; `system_logs`
on `logged_at`. Both use natural composite primary keys, since PostgreSQL
requires the partition column in the primary key and neither table is referenced
by id. A helper function creates partitions, invoked by the weekly maintenance
job two months ahead of need.

`ohlcv_bars` is left unpartitioned until intraday data or a demonstrated
planning problem justifies the table rewrite.

## Alternatives considered

**TimescaleDB.** Its strengths — continuous aggregates, hypertable compression,
`time_bucket`, high-frequency ingest — address problems this workload does not
have. We do not aggregate over huge ranges (windows are per-instrument and
small), we do not ingest continuously (one batch a day), and 40M rows a year is
not a scale that needs compression.

The costs are concrete: an extension pinned to specific PostgreSQL versions,
constrained managed-hosting options, an upgrade path coupled to a third party,
and hypertables that interact awkwardly with the `EXCLUDE USING gist`
constraints the identity model depends on.

**ClickHouse or DuckDB alongside PostgreSQL.** Excellent analytical performance,
but a second store means two sources of truth, a synchronisation path, and the
loss of transactional consistency between a decision and the data that produced
it. Reproducibility is the point of this system; splitting the data breaks it.

**Everything in one unpartitioned table.** Simplest, and adequate for the first
few years. Rejected because retention then becomes a mass `DELETE` with the
bloat and vacuum load that implies, and because retrofitting partitioning to a
400M-row table is far more expensive than starting with it on the one table
that clearly needs it.

## Consequences

- Partition maintenance is an operational responsibility. `database_maintenance`
  creates partitions ahead of need; a `DEFAULT` partition is a backstop that
  must stay empty, since rows in it block attaching a proper partition later.
- Retention is `DROP TABLE indicator_values_p202409` — instant, no bloat.
- Measured: a date-bounded query prunes 26 of 28 partitions.
- Standard partitioning keeps the door open. Converting a table to a hypertable
  later is a migration, not a redesign.

**Revisit when:** minute bars are ingested across a full universe (~390x the
daily row count), or `EXPLAIN` shows planning time on `indicator_values`
becoming material. Both are triggers, not vague intentions.
