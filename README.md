# tradeit

A stock screening, breakout-confirmation, portfolio-construction and
compounding platform, built to behave like an intelligent portfolio manager
rather than a stock-picking bot.

The distinction it is organised around:

> **"A great stock"** and **"a great trade for this portfolio right now"** are
> not the same thing.

**Status: Phase 1 complete.** The foundation — domain model, point-in-time data
layer, ingestion, migrations, CI — is built and tested. Screening, patterns,
portfolio construction and backtesting are later phases and are not yet
implemented. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

Live trading is disabled and stays disabled until separately authorised
([ADR-0004](docs/adr/0004-live-trading-safety-interlock.md)).

## What Phase 1 gives you

- **A point-in-time data layer.** Every fact carries when it happened *and* when
  it became knowable. Every read is gated by an as-of clock. There is no API for
  reading "the latest" data, because that is precisely what a backtest must not
  see.
- **Survivorship-safe identity.** Instruments have permanent surrogate keys;
  tickers and universe membership are date intervals, so a screen run as of 2015
  sees the companies that existed in 2015 — including the ones that later went
  to zero.
- **Raw prices with clock-aware adjustment.** Splits and dividends are applied on
  read, from actions that were already announced at the as-of instant.
- **Auditable ingestion.** Append-only writes, revisions preserved as history,
  rejected rows quarantined verbatim rather than dropped.
- **A safety interlock** that makes live trading require a deliberate,
  machine-local act rather than an environment variable.

## Quick start

```bash
make install          # venv + dependencies
make up               # postgres + redis via docker compose
make migrate          # apply migrations
make demo             # load synthetic data, read it back point-in-time
make check            # lint + strict types + tests
```

No API key or network access is needed: the default provider generates
deterministic synthetic data offline.

```
$ tradeit config
environment          : development
trading mode         : paper
orders simulated     : True
price provider       : synthetic
calendar             : XNYS
```

## The core idea, in code

```python
from datetime import datetime, UTC
from tradeit.core.clock import AsOfClock
from tradeit.storage.repositories import BarRepository, FundamentalRepository

clock = AsOfClock.at(datetime(2019, 3, 14, 21, 30, tzinfo=UTC))

# Only bars published by 2019-03-14 21:30 UTC, split-adjusted using only the
# corporate actions announced by then.
bars = BarRepository(session).history(clock, instrument_id)

# The revenue figure as it had been *filed* by that date -- not the value a
# restatement settled on years later.
revenue = FundamentalRepository(session).latest_metric(clock, instrument_id, "revenue")
```

The clock cannot be rewound, and a paper or live clock that has drifted more
than fifteen minutes behind wall time refuses to act.

## Layout

```
src/tradeit/
  core/        clock, calendar, money, enums, domain models
  data/        vendor-neutral provider protocols + adapters
  ingest/      append-only ingestion with quarantine and run audit
  storage/     schema, sessions, point-in-time repositories
  config.py    settings + live-trading interlock
migrations/    Alembic
tests/         unit (SQLite) + integration (SQLite and PostgreSQL)
docs/          architecture, roadmap, ADRs, phase reports
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — layers, data model, bias controls
- [Roadmap](docs/ROADMAP.md) — the eight phases and what each needs
- [Phase 1 report](docs/PHASE_01.md) — decisions, risks, open issues, acceptance
- [ADRs](docs/adr/) — the decisions that constrain later phases

## Testing

```bash
make test      # unit + SQLite integration
make test-pg   # additionally runs the PostgreSQL-specific tests
```

The tests in `tests/unit/test_point_in_time.py` are the ones that matter most:
if they regress, every backtest this platform produces is fiction.
