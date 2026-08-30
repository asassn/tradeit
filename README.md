# tradeit

A stock screening, breakout-confirmation, portfolio-construction and
compounding platform, built to behave like an intelligent portfolio manager
rather than a stock-picking bot.

The distinction it is organised around:

> **"A great stock"** and **"a great trade for this portfolio right now"** are
> not the same thing.

**Status: Phases 1–5 complete, the empirical gate closed at Outcome B, and
Phase 6 in progress.** The point-in-time data layer, the system architecture, the
causal analytics foundation, twelve chart-pattern detectors and the thirteen-state
breakout engine are built and tested. See the phase reports below.

**Phase 6 milestone 0b is complete: 30 of 30 control securities are fully
adjudicated.** Every control's identity rests on a cited primary regulatory
filing, no identifier was guessed, and the fixture in `controls.py` still carries
none. This is the *measuring instrument* for vendor validation, not a claim about
any vendor's coverage — run `tradeit edgar controls` for the measured state
rather than quoting a number from here.

**Three limits remain open, and each matters.** Phase 3's acceptance gate is a
conditional pass: the mathematics was validated against two independent libraries
and four real defects were found in the regime model, but it could not be
validated against real market data because this environment's network policy
refuses every market-data vendor
([`docs/PHASE_03_GATE.md`](docs/PHASE_03_GATE.md)). Phases 4 and 5 inherit that
limit — synthetic corpora establish code properties, never market accuracy
([ADR-0018](docs/adr/0018-synthetic-data-limits.md)) — so no claim about
real-world pattern precision or recall has been made. And **no strategy, backtest
or trade exists yet**: opportunity scoring, portfolio construction, backtesting,
the dashboard and paper trading are Phases 7–11, so the platform currently has no
evidence of profitability of any kind. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the canonical twelve-phase plan.

**Real bars have been through it once.** `full-01` drove the Phase 4 and Phase 5
engines causally across a full imported universe — 272,537 pattern identities
over 87,704 distinct structures. It is frozen and is never cited for economic
claims, because its universe is not survivorship-safe — every cross-sectional
number over it would inherit a bias toward the securities that survived to be in
it ([`docs/CORPUS_REGISTRY.md`](docs/CORPUS_REGISTRY.md)).

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
- **A 69-table schema** applied to PostgreSQL 16, with monthly partitioning on
  the high-volume tables and a drift test that fails if the ORM and the
  migrations disagree. 41 of them were validated at Phase 2 and later phases
  added the rest; the count is measured from the ORM metadata.
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
- **A causal analytics layer.** 58 indicators under the shipped `baseline.toml`
  — the count comes from the feature registry and moves with the config —
  written rather than imported, so
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
  analytics/        causal indicators, RS, sectors, breadth, regimes
  patterns/         twelve detector families, identity, lifecycle, relationships
  breakouts/        thirteen-state engine, quality and confirmation scoring
  scanning/         causal feed and the scan runner over a snapshot
  edgar/            SEC index, denominator, lifecycle, control identity evidence
  acquisition/      vendor adapters, cache, journal, normalisation
  validation/       the empirical check harness and survivorship checks
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
- [Data model](docs/DATA_MODEL.md) — all 69 tables inventoried across nine
  domains, with ERDs, partitioning and indexes; `tables.py` remains the source of
  truth and a test pins the inventory to it
- [API specification](docs/API.md) — the endpoints Phase 10 will implement
- [Vendor evaluation](docs/VENDOR_EVALUATION.md) — options and an acceptance test
- [Roadmap](docs/ROADMAP.md) — the canonical twelve phases and the three non-numbered gates
- Phase reports — [1](docs/PHASE_01.md) · [2](docs/PHASE_02.md) · [3](docs/PHASE_03.md) · [4](docs/PHASE_04.md) · [5](docs/PHASE_05.md)
- Acceptance gates — [Phase 3](docs/PHASE_03_GATE.md) · [Phase 5 / empirical](docs/PHASE_05_GATE.md) — what was validated, what could not be, and why
- Breakouts — [architecture](docs/BREAKOUT_ARCHITECTURE.md) · [lifecycle](docs/BREAKOUT_LIFECYCLE.md) · [scoring](docs/BREAKOUT_SCORING.md) · [validation](docs/BREAKOUT_VALIDATION.md)
- Phase 6 — [improvement plan and milestones](docs/PHASE_06_IMPROVEMENT_PLAN.md) · [vendor matrix](docs/PHASE_06_VENDOR_MATRIX.md) · [purchase gate](docs/PHASE_06_PURCHASE_GATE.md)
- Identity and survivorship — [EDGAR delisting denominator](docs/EDGAR_DELISTING_DENOMINATOR.md) · [control universe](docs/DOTCOM_CONTROL_UNIVERSE.md) · [forward survivorship](docs/FORWARD_SURVIVORSHIP_SYSTEM.md)
- Future architecture — [multi-timeframe mandates](docs/MULTI_TIMEFRAME_MANDATES.md) · [strategy builder](docs/STRATEGY_BUILDER.md)
- [Getting real data](docs/LOCAL_DATA_ACQUISITION.md) — the step-by-step download procedure
- [What to supply](DATA_REQUIRED.md) — the shopping list, and what each file buys
- Pattern recognition — [architecture](docs/PATTERN_ARCHITECTURE.md) · [methodology](docs/PATTERN_METHODOLOGY.md) · [lifecycle](docs/PATTERN_LIFECYCLE.md) · [relationships](docs/PATTERN_RELATIONSHIPS.md)
- [Pattern validation report](docs/PATTERN_VALIDATION.md) — generated distributions, competing-pattern matrix, known weaknesses
- [Pattern labelling protocol](docs/PATTERN_LABELING.md) — the real-market corpus design, and the sampling rules that keep future returns out of it
- [Pattern performance](docs/PATTERN_PERFORMANCE.md) — benchmarks, budget, storage growth
- [ADRs](docs/adr/) — the decisions that constrain later phases

## Testing

```bash
make test      # unit + SQLite integration
make test-pg   # additionally runs the PostgreSQL-specific tests
```

The tests in `tests/unit/test_point_in_time.py` are the ones that matter most:
if they regress, every backtest this platform produces is fiction.

2,892 tests today. The 274 in `tests/unit/test_causality.py` are the analytics
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
