# Architecture

## The organising idea

The system is a **portfolio manager**, not a stock picker. A great company with
a great chart may still be a poor use of capital right now — because the
portfolio already holds three names that move with it, because the market regime
is hostile, or because the capital is better deployed elsewhere. The
architecture keeps that distinction structural: *opportunity quality* and
*portfolio fit* are separate stages, and only the second one can authorise a
trade.

Underneath that sits a single non-negotiable constraint: **a decision may only
use information that existed when the decision was made.** Everything in the
data layer is shaped by it.

## Layers

```
                       ┌─────────────────────────────────────────┐
                       │  Presentation  (API · dashboard · CLI)  │  Phase 8
                       └────────────────────┬────────────────────┘
                                            │
      ┌─────────────────────────────────────┴─────────────────────────────┐
      │  Evaluation   backtest · walk-forward · Monte Carlo · attribution  │  Phase 7
      └─────────────────────────────────────┬─────────────────────────────┘
                                            │
      ┌─────────────────────────────────────┴─────────────────────────────┐
      │  Portfolio    sizing · heat · correlation · compounding · exits    │  Phase 5-6
      └─────────────────────────────────────┬─────────────────────────────┘
                                            │
      ┌─────────────────────────────────────┴─────────────────────────────┐
      │  Opportunity  screening · patterns · breakout confirm · scoring    │  Phase 3-4
      └─────────────────────────────────────┬─────────────────────────────┘
                                            │
      ┌─────────────────────────────────────┴─────────────────────────────┐
      │  Analytics    indicators · relative strength · regime · sectors    │  Phase 2
      └─────────────────────────────────────┬─────────────────────────────┘
                                            │
      ┌─────────────────────────────────────┴─────────────────────────────┐
      │  Data         providers · ingestion · point-in-time repositories   │  Phase 1 ✅
      └───────────────────────────────────────────────────────────────────┘
```

Each layer depends only on the one below it. Nothing above the data layer talks
to the database directly; everything reads through repositories, which is what
makes the point-in-time guarantee enforceable rather than aspirational.

## The as-of clock

`AsOfClock` is the spine. It carries one instant and one mode, and every
repository read takes one:

```python
clock = AsOfClock.at(datetime(2019, 3, 14, 21, 30, tzinfo=UTC))
bars  = BarRepository(session).history(clock, instrument_id)
```

There is deliberately no way to ask for "the latest" data. A backtest advances
one clock through history; a live session holds a clock pinned to now. The same
code runs under both, which is what makes a backtest evidence about the live
system rather than a separate program that happens to resemble it.

`advance_to()` refuses to move backwards, so a walk-forward loop cannot silently
re-read the future. `assert_fresh()` refuses to let a stale clock drive paper or
live orders.

## Bitemporality in one picture

```
       event_time ──────────────► when the fact is ABOUT
                                  (bar close, fiscal period end, ex-date)

   knowledge_time ──────────────► when we COULD HAVE KNOWN it
                                  (close + publication lag, filing timestamp,
                                   announcement)

        Visible to a clock at T  ⟺  knowledge_time <= T
        Value at T               ⟺  greatest knowledge_time <= T
```

A revision does not overwrite; it inserts a row with a later `knowledge_time`.
So "what did we believe on 14 March 2019?" is a query, not an archaeology
project. See ADR-0002.

## Module map

| Module | Responsibility |
|---|---|
| `tradeit.core.clock` | `AsOfClock` — the leakage guard |
| `tradeit.core.calendar` | Exchange sessions, trading-day arithmetic, early closes |
| `tradeit.core.models` | Validated domain facts; the bitemporal invariant |
| `tradeit.core.money` | Decimal discipline for prices, cash, quantities |
| `tradeit.core.enums` | Persisted controlled vocabularies |
| `tradeit.config` | Settings and the live-trading interlock |
| `tradeit.data.provider` | Vendor-neutral protocols + capability declarations |
| `tradeit.data.providers.*` | Concrete adapters (Phase 1 ships `synthetic`) |
| `tradeit.data.registry` | Name-based adapter lookup |
| `tradeit.ingest.pipeline` | Append-only writes, quarantine, run audit |
| `tradeit.storage.tables` | Physical schema |
| `tradeit.storage.repositories` | **The only sanctioned read path** |

## Data model

**Identity.** `instrument_id` is a permanent surrogate key. Tickers are an
attribute that changes over time, held in `symbol_mappings` as half-open date
intervals — because tickers get recycled, and a screen keyed on a string will
happily splice two unrelated companies' price histories together.

**Universe.** `universe_memberships` stores membership intervals, so a screen run
as of 2015 sees the companies listed in 2015, including those that later went to
zero. This is the survivorship-bias control.

**Facts.** `ohlcv_bars` (raw, unadjusted), `corporate_actions`,
`fundamental_facts` (narrow, one metric per row, with restatement links),
`earnings_events`. All append-only, all bitemporal.

**Audit.** `ingestion_runs` records every load — provider, window, code version,
status, row counts. `quarantined_rows` keeps rejected payloads verbatim with the
reason, so a gap in a price series is always explainable.

## Bias controls, and where each one lives

| Bias | Control | Enforced by |
|---|---|---|
| Look-ahead | `knowledge_time <= as_of` on every read | `AsOfClock` + repositories + DB `CHECK` |
| Survivorship | Universe membership intervals | `universe_memberships` |
| Ticker recycling | Surrogate keys + symbol intervals | `symbol_mappings`, PG `EXCLUDE` constraint |
| Restatement leakage | Append-only revisions, latest-visible query | `uq_*_revision` + window function |
| Adjustment leakage | Raw storage, clock-gated read-time adjustment | ADR-0005 |
| Filing-date leakage | Quarantine `knowledge_time == period_end` | `Ingestor.ingest_fundamentals` |
| Silent data loss | Quarantine, never drop | `quarantined_rows` |
| Stale-state trading | Freshness assertion on live/paper clocks | `AsOfClock.assert_fresh` |

Overfitting, selection bias, and unrealistic fill assumptions are *not* addressed
in Phase 1. They belong to the backtesting phase and need their own controls.

## Technology, and what was left out

Adopted in Phase 1: Python 3.11, Pydantic v2, SQLAlchemy 2.0, Alembic,
PostgreSQL 16, pytest, ruff, mypy (strict), structlog, NumPy,
pandas-market-calendars.

Deferred deliberately, until there is a problem that needs them: Redis (nothing
to cache yet), Celery (no recurring jobs yet), FastAPI (no consumers yet),
Polars (pandas is not the bottleneck), VectorBT (no strategy to backtest),
XGBoost/PyTorch (no features to learn from, and a model before a validated
baseline is a way to overfit faster).

The brief's instruction — do not force every technology into the project unless
it provides value — is treated as binding.
