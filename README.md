# tradeit

A stock screening, breakout-confirmation, portfolio-construction and
compounding platform, built to behave like an intelligent portfolio manager
rather than a stock-picking bot.

The distinction it is organised around:

> **"A great stock"** and **"a great trade for this portfolio right now"** are
> not the same thing.

**Status: Phase 3 complete; its acceptance gate is a conditional pass.** The
point-in-time data layer, the system architecture, and the causal analytics
foundation are built and tested — 58 indicators, multi-benchmark relative
strength, sector strength, market breadth, and transparent market- and
volatility-regime models. The gate validated the mathematics against two
independent libraries and found four real defects in the regime model; it could
not validate against real market data, because this environment's network
policy refuses every market-data vendor. See
[`docs/PHASE_03_GATE.md`](docs/PHASE_03_GATE.md). Pattern recognition,
breakout confirmation, scoring, portfolio construction and backtesting are
Phases 4–9. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the canonical
twelve-phase plan.

Live trading is disabled and stays disabled until separately authorised
([ADR-0004](docs/adr/0004-live-trading-safety-interlock.md)).

## What exists today

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
- **A validated 41-table schema** applied to PostgreSQL 16, with monthly
  partitioning on the high-volume tables and a drift test that fails if the ORM
  and the migrations disagree.
- **Content-addressed reproducibility.** Strategy configs, data snapshots,
  feature sets and models are identified by content hash and pinned together in
  a run manifest, so two runs sharing a digest provably used the same rules.
- **Configuration-driven strategy.** Every threshold, weight and limit lives in
  TOML, validated across sections, with no strategy constant in code.
- **Vendor-neutral interfaces** for market data, fundamentals, earnings, news,
  macro and brokers — plus the domain contracts every later phase builds
  against. Tiingo and Stooq adapters implement them, and both declare in code
  what they cannot supply.
- **Thirteen data-quality diagnostics** with a severity split that matters:
  impossible rows are quarantined, merely *unusual* ones are flagged and kept.
  Dropping everything unusual would delete precisely the market conditions a
  breakout system exists to trade.
- **A causal analytics layer.** 58 indicators written rather than imported, so
  their seeding, smoothing and warm-up behaviour can be tested; multi-benchmark
  relative strength ranked against the point-in-time universe; sector strength,
  breadth, and explainable regime models that record contradicting evidence as
  well as supporting.
- **274 causality tests.** Every indicator is proved to depend only on past
  bars, by asserting that computing over a prefix reproduces the prefix of
  computing over everything.

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
config/strategies/  every strategy parameter, identified by content hash
src/tradeit/
  core/             clock, calendar, money, enums, domain models
  data/             vendor-neutral provider protocols + adapters
  ingest/           append-only ingestion with quarantine and run audit
  storage/          schema, sessions, point-in-time repositories
  reproducibility/  content hashing and run manifests
  analytics/        58 causal indicators, RS, sectors, breadth, regimes
  strategy/         screening, patterns, scoring   (interfaces + config)
  portfolio/        state, sizing, allocation      (interfaces only)
  risk/             limits and the veto gate       (interfaces only)
  execution/        orders, costs, fills           (interfaces only)
  backtesting/      backtest, walk-forward, MC     (interfaces only)
  scheduling/       22 declared jobs
migrations/         Alembic
tests/              unit (SQLite) + integration (SQLite and PostgreSQL 16)
docs/               architecture, data model, API, roadmap, ADRs, phase reports
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — the complete technical architecture
- [Analytics methodology](docs/ANALYTICS.md) — indicator formulas, regime rules
- [Data model](docs/DATA_MODEL.md) — 46 tables, ERDs, partitioning, indexes
- [API specification](docs/API.md) — the endpoints Phase 10 will implement
- [Vendor evaluation](docs/VENDOR_EVALUATION.md) — options and an acceptance test
- [Roadmap](docs/ROADMAP.md) — the canonical twelve phases
- Phase reports — [1](docs/PHASE_01.md) · [2](docs/PHASE_02.md) · [3](docs/PHASE_03.md)
- [Phase 3 acceptance gate](docs/PHASE_03_GATE.md) — what was validated, what could not be, and why
- [ADRs](docs/adr/) — the decisions that constrain later phases

## Testing

```bash
make test      # unit + SQLite integration
make test-pg   # additionally runs the PostgreSQL-specific tests
```

The tests in `tests/unit/test_point_in_time.py` are the ones that matter most:
if they regress, every backtest this platform produces is fiction.

782 tests today. The 274 in `tests/unit/test_causality.py` are the analytics
layer's equivalent: they assert that computing an indicator over a prefix of the
data reproduces the prefix of computing it over everything, which is the formal
statement of "no look-ahead". Verified to catch centred windows, full-sample
z-scores and off-by-one reads.

`tests/unit/test_cross_validation.py` checks every kernel against two
independent libraries (`ta` and `pandas-ta-classic`). Four of its tests check
the comparison itself, because a cross-validation that cannot fail proves
nothing.

```bash
make bench     # throughput, scaling and memory benchmarks
```
