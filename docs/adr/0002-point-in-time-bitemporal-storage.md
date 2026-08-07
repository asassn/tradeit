# ADR-0002: Bitemporal, append-only storage with clock-gated reads

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 1

## Context

The brief requires that "everything that affects a trading decision must be
reproducible historically" and names look-ahead bias and data leakage as things
to avoid. Those are not incidental bugs; they are the default outcome of the
obvious design.

The obvious design stores one row per (instrument, date) and updates it when the
vendor revises. Under that design:

- A backtest reading a 2019 bar sees the value the vendor settled on in 2024,
  not what a trader could have seen in 2019.
- Fundamentals are keyed on fiscal period, so a screen run on 1 January "knows"
  a quarter that was not filed until mid-February. Six weeks of hindsight on
  every fundamental factor is worth several points of fictitious annual return.
- Restatements erase the original filing, so the audit trail for a past decision
  no longer exists.

Every one of these produces a backtest that looks better than reality, which is
the failure mode least likely to be noticed.

## Decision

**1. Every fact is bitemporal.** Facts carry `event_time` (when it happened) and
`knowledge_time` (the earliest instant a decision-maker could have acted on it),
plus `knowledge_source` recording whether that timestamp was reported by the
vendor or estimated by us.

**2. Fact tables are append-only.** A revision inserts a new row with a later
`knowledge_time`. Nothing is updated in place. Uniqueness is on
`(business key..., knowledge_time)`, which makes replaying a load idempotent
while preserving genuine revisions as history.

**3. Reads are gated by a clock.** Every repository method takes an `AsOfClock`
and filters `knowledge_time <= as_of`. There is no method that returns "the
latest" data. The canonical query is *latest visible revision*: among rows
visible to the clock, take the greatest `knowledge_time` per business key.

**4. Identity and membership are interval-based.** Ticker-to-instrument and
universe membership are stored as half-open date ranges, so a historical screen
sees the companies that existed then — including those that later delisted.

**5. The invariant is enforced in three places.** Pydantic validators reject
`knowledge_time < event_time` at construction; PostgreSQL `CHECK` constraints
reject it at write; the ingestion pipeline quarantines fundamentals whose
`knowledge_time` equals their period end, since that is a vendor telling us it
has no filing dates.

## Alternatives considered

**Snapshot the whole database daily.** Conceptually simple and fully auditable,
but storage grows with universe × history × days, and answering "what did we
believe on date X?" means restoring a snapshot rather than running a query.

**Store only knowledge_time, drop event_time.** Loses the ability to align facts
to the periods they describe, which growth and seasonality factors need.

**Apply a fixed reporting lag at query time** (e.g. "assume all filings land 45
days after period end"). Cheap, and it is what the `assumed_filing_lag_days`
setting provides as a *fallback*. But the real lag varies from 20 to 90 days,
and a fixed offset is wrong in both directions — sometimes hiding data that was
public, sometimes revealing data that was not.

## Consequences

- Storage is larger, and every read carries a window function. The indexes lead
  with `knowledge_time` so the correct query is also the fast one.
- Backtest results will be worse than a naive implementation would report. That
  is the point.
- Reports can distinguish results built on `REPORTED` timestamps from those
  built on `ESTIMATED` ones and qualify their confidence accordingly.
- Any future write path that bypasses the repository layer defeats this. The
  database-level `CHECK` constraints are the backstop.
