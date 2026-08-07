# Phase 2 — System Architecture & Data Design

**Status:** Complete · **Date:** 2026-08-07

---

## Scope

Documentation, architecture refinement, database validation, interface
refinement, and design. **No trading logic was implemented.**

New code was written only where the architecture needed something executable to
be verifiable rather than merely asserted: the interface set, the schema, the
configuration system, the versioning mechanism, and the job catalogue. Every one
of those is a design artifact that would otherwise be a diagram nobody could
check. No indicator, filter, detector, sizer, risk rule, cost model or backtest
engine exists.

**Deliverables** (all requested outputs produced):

| Requested | Delivered |
|---|---|
| Complete architecture document | [`ARCHITECTURE.md`](ARCHITECTURE.md) — 15 sections covering all 12 required topics |
| Component diagram (Mermaid) | `ARCHITECTURE.md` §2 |
| Database ERD (Mermaid) | [`DATA_MODEL.md`](DATA_MODEL.md) — 5 domain ERDs |
| Repository folder tree | `ARCHITECTURE.md` §3 |
| Key Python interfaces | 8 modules under `src/tradeit/`, type-checked |
| API specification | [`API.md`](API.md) |
| Data-flow diagram | `ARCHITECTURE.md` §14 |
| Implementation order | `ARCHITECTURE.md` §15, [`ROADMAP.md`](ROADMAP.md) |

All nine Mermaid diagrams were parsed with the Mermaid 11 parser, not
eyeballed — two ERD syntax errors were found and fixed that way.

---

## Decisions made

### 1. PostgreSQL alone; no TimescaleDB — [ADR-0006](adr/0006-postgresql-without-timescaledb.md)

The workload is one batch write after the close, ~40M indicator rows a year, and
reads that are point-in-time slices rather than time-bucketed aggregates.
TimescaleDB's strengths — continuous aggregates, compression, high-frequency
ingest — address problems this system does not have, while its costs are real:
version pinning, constrained hosting, an upgrade path coupled to a third party,
and awkward interaction with the `EXCLUDE` constraints the identity model needs.

Native declarative partitioning gives pruning, `DROP TABLE` retention, and
per-partition maintenance. Revisit triggers are recorded specifically: minute
bars across a full universe, or measured planning-time cost.

### 2. Identity is content, not a version number — [ADR-0007](adr/0007-content-addressed-reproducibility.md)

Strategy configurations, data snapshots, feature sets and models are identified
by SHA-256 of their canonical serialisation and pinned together in a
`RunManifest` that every derived row references.

`v3` cannot tell you whether two runs used the same rules, and nothing stops
someone editing `v3` in place. A content hash makes "did these two runs use the
same rules?" answerable without anyone's cooperation.

The subtlest part is `data_snapshot`. Bounding reads by `as_of` is *almost*
sufficient — but a backfill landing after a scan means replaying that scan sees
rows the original did not, legitimately, because their `knowledge_time` precedes
`as_of` and they simply had not been loaded. Pinning the maximum
`ingestion_run_id` per dataset makes replay exact rather than merely honest.
This is the gap most likely to be missed and it is closed.

### 3. No strategy constant may live in code — [ADR-0008](adr/0008-configuration-driven-strategy.md)

Eleven validated configuration sections in TOML. `extra="forbid"` everywhere, so
a typo'd key is an error rather than a silent fallback to a default the author
did not intend.

Cross-section validation catches configurations that satisfy every field
constraint yet are incoherent as a whole. The instructive case: a
relative-strength lookback longer than the required trading history means every
instrument passing the liquidity filter fails on warm-up, and the screen
silently returns nothing — the kind of failure diagnosed as "the strategy found
nothing today" for weeks.

### 4. One deployable, four worker queues — [ADR-0009](adr/0009-monolith-with-worker-queues.md)

Microservices would add network partitions and deployment skew — where the
scanner runs one version of the scoring rules and the backtester another — to
solve a scaling problem that does not exist. Queue separation (`realtime`,
`compute`, `backtest`, `maintenance`) solves the real problem, which is that a
four-hour backtest must not delay a fifteen-second breakout check.

### 5. Six capability-split provider protocols

`MarketDataProvider`, `FundamentalDataProvider`, `EarningsProvider`,
`NewsProvider`, `MacroDataProvider`, `BrokerProvider`. Earnings is split from
fundamentals because vendors sell them separately and because earnings *dates*
are needed for risk management even with no fundamental licence at all.

`ProviderCapabilities` gained `caveats()`, which renders limitations as the
strings that appear in `backtest_runs.data_caveats` and API responses — so the
qualification travels with the result instead of living in someone's memory.

`BrokerProvider` is the documented exception to the "never look at the clock"
rule: it reports live state, not historical facts.

### 6. Partitioned tables use natural composite keys

PostgreSQL requires a partitioned table's primary key to contain the partition
column. Rather than bolt `session_date` onto a surrogate id, `indicator_values`
drops the surrogate entirely — nothing references it, the natural key is already
unique, and at ~40M rows/year a `BIGSERIAL` plus its index is pure overhead.
`system_logs` uses `(logged_at, event_id)` with a client-generated UUID, avoiding
a sequence contended by several workers.

This was discovered by running the schema, not by reasoning about it: the first
attempt failed on SQLite with "no autoincrement for composite primary keys",
which surfaced the design question.

### 7. Simulated and real fills live in separate tables

`backtest_trades` and `trades` are structurally near-identical, and that is
precisely the risk. One accidental `UNION` turns a live performance report into
fiction. Separate tables make the mistake require intent.

### 8. Reject-tracking is first-class

`screen_rejections` and `data_quality_issues` record what did *not* pass and what
was found wrong across rows. Storing only survivors makes "why isn't NVDA on the
list?" answerable only by re-running the whole chain, and recording only executed
trades makes a strategy look better than it is.

---

## Assumptions

| # | Assumption | If wrong |
|---|---|---|
| 1 | Single-user, single-region, no external API consumers | Multi-tenancy needs row-level portfolio scoping throughout; the schema has `portfolio_id` everywhere, so it is additive |
| 2 | Daily bars remain the primary timeframe | Intraday requires partitioning `ohlcv_bars` and a different ingestion cadence; schema already supports the timeframe dimension |
| 3 | US equities, USD | Multi-currency needs a bitemporal FX table and a currency dimension on cash |
| 4 | Long-only in practice; `Side.SHORT` declared but unimplemented | Shorting adds borrow availability, borrow cost and hard-to-borrow constraints |
| 5 | ~40 indicators over ~4,000 instruments | An order of magnitude more makes `indicator_values` a candidate for weekly rather than monthly partitions |
| 6 | Celery for job execution | The catalogue is runner-agnostic; only `scheduling/runner.py` binds to it |
| 7 | Redis is available for caching and brokering | Both are optional in Phase 2; nothing yet depends on either |
| 8 | Sector classification will be interval-based | Already modelled that way; a vendor supplying only current classifications is survivorship-unsafe for rotation work |

---

## Architecture changes

Phase 2 changed three things established in Phase 1.

**`PriceProvider` renamed to `MarketDataProvider`**, matching the brief's
vocabulary. The old name is kept as an alias so existing adapters keep working;
`FundamentalProvider` became `FundamentalDataProvider` with earnings split out
into its own protocol, which is a genuine interface change and was applied to
the conformance test.

**Phase 1 deferred position, order and portfolio models**, on the grounds that
inventing their shape before the logic that uses them would guarantee a rewrite.
Phase 2 defines them because the brief asks for the schema. They are marked
provisional: `positions`, `orders`, `executions` and `trades` are the tables most
likely to change when Phases 6–7 implement against them. The constraints that
encode judgement — a stop required while a position is open, `client_order_id`
unique, filled quantity bounded by ordered quantity — are the parts expected to
survive.

**`ohlcv_bars` is still unpartitioned.** Partitioning it requires changing its
primary key from `id` to `(id, session_date)` — a table rewrite, cheap now and
expensive later. It is recorded as an open issue rather than done speculatively,
with the trigger stated: before intraday ingestion, since minute bars multiply
the row count by ~390.

---

## Risks

| Risk | Severity | Mitigation now | Residual |
|---|---|---|---|
| **Schema designed before the logic that uses it** | **High** | Constraints encode judgement, not just shape; drift test keeps ORM and migrations aligned; provisional tables identified | Phases 6–7 will need migrations against `positions`/`orders`. Expected, not a surprise |
| **Interfaces designed before implementations** | **Medium** | Every protocol is type-checked and has at least one property test it must satisfy | Some signatures will change on contact with real logic |
| **Vendor still unchosen** | **High** | Provider protocols are vendor-neutral; capabilities carry limitations into reports | Blocks Phase 4's fundamental filters. Unchanged from Phase 1 and now the critical path |
| **Partition maintenance is an operational duty** | **Medium** | Created two months ahead; helper is idempotent; `DEFAULT` partition as backstop; weekly job declared | If maintenance lapses long enough for rows to land in `DEFAULT`, attaching a proper partition later requires moving them |
| **`run_manifest_id` on the hot write path** | **Low** | Foreign key with `RESTRICT`; one manifest per run, not per row | Adds a join to most analytical queries. Accepted for auditability |
| **Config validation gives false confidence** | **Medium** | Cross-section rules catch the incoherent combinations found so far | Validators cannot catch a coherent configuration that is simply a bad strategy. Only Phase 8 can |
| **22 jobs with a 90-minute dependency chain** | **Medium** | Dependencies explicit and cycle-checked; trading gate blocks on failure | A slow vendor pushes plan generation late. Timeouts are declared but untested |
| **Overfitting** | **Not addressed** | — | Out of scope by design. Phase 8 needs its own controls; nothing here says anything about it |

---

## Open issues

1. **Data vendor.** Now the critical path — it blocks Phase 4. Decide on:
   real filing timestamps → delisted instruments → history depth → price. A
   cheap vendor without filing dates produces backtests that cannot be trusted.
2. **Benchmark instrument.** Blocks Phase 3's relative strength. SPY, an index
   series, or configurable per run.
3. **Sector classification scheme.** Blocks Phase 3's sector aggregates. Must
   supply historical classifications, not just current ones.
4. **Universe definition.** Concrete rule needed: exchanges, price and dollar-
   volume floors, and whether ADRs, REITs and ETFs are in scope. The config file
   has placeholders.
5. **Starting capital and risk budget.** Blocks Phase 6. `baseline.toml` has
   plausible defaults (0.5% per trade, 6% heat, 12 positions) that are
   hypotheses, not validated numbers.
6. **`ohlcv_bars` partitioning.** Decide before intraday ingestion.
7. **Dashboard framework.** Streamlit is fastest; a React SPA is more capable.
   Not needed until Phase 9.
8. **Retention policy.** How long to keep `screen_rejections`, `indicator_values`
   detail and `system_logs`. Partitioning makes this a configuration choice
   rather than a redesign.

Only issues 2 and 3 block Phase 3.

---

## File and folder structure

Full annotated tree in [`ARCHITECTURE.md` §3](ARCHITECTURE.md). New in Phase 2:

```
config/strategies/baseline.toml           every strategy parameter, hash-identified

src/tradeit/
├── analytics/base.py                     Indicator, CrossSectionalFeature,
│                                         FeatureVector, RegimeClassifier
├── strategy/
│   ├── base.py                           ScreenFilter, PatternDetector,
│   │                                     BreakoutMonitor, OpportunityScorer
│   └── config.py                         11 validated sections, content-hashed
├── portfolio/base.py                     PortfolioState, PositionSizer,
│                                         AllocationRanker, SizingDecision
├── risk/base.py                          RiskRule, RiskEngine, RiskVerdict
├── execution/base.py                     OrderRequest, Fill, CostModel, FillModel,
│                                         ExecutionService
├── backtesting/base.py                   BacktestSpec, PerformanceMetrics,
│                                         WalkForwardHarness, MonteCarloSpec
├── scheduling/jobs.py                    22 declared jobs, validated catalogue
├── reproducibility/versioning.py         content hashing, ArtifactVersion, RunManifest
├── core/enums.py                         +14 Phase 2 vocabularies
├── core/models.py                        +NewsItem, +MacroObservation
├── data/provider.py                      6 capability-split protocols
└── storage/tables.py                     +32 tables

migrations/versions/0002_phase2_schema.py partitioning + maintenance helper

docs/
├── ARCHITECTURE.md                       the architecture document
├── DATA_MODEL.md                         5 domain ERDs + table catalogue
├── API.md                                endpoint specification
├── PHASE_02.md                           this document
└── adr/0006–0009                         four new decision records

tests/
├── unit/test_versioning.py               19 tests
├── unit/test_strategy_config.py          22 tests
├── unit/test_job_catalogue.py            17 tests
└── integration/test_phase2_schema.py     18 tests against PostgreSQL 16
```

---

## Acceptance criteria

Every criterion was verified by running it.

| # | Criterion | Evidence |
|---|---|---|
| 1 | The 41-table schema applies cleanly to PostgreSQL 16 | `alembic upgrade head`; 98 relations created |
| 2 | ORM and migrations do not disagree | `test_the_migration_and_the_orm_have_not_drifted` — verified to catch a deliberately added column |
| 3 | Partitions exist ahead of need | 28 monthly partitions, 24 back and 2 forward |
| 4 | Rows route to the correct partition | 2026-03-16 → `indicator_values_p202603` |
| 5 | Date-bounded queries prune | 26 of 28 subplans removed |
| 6 | Retention is a partition drop | `DROP TABLE indicator_values_p202409` |
| 7 | The maintenance helper is idempotent | Called twice; one partition created |
| 8 | The default partition stays empty | Asserted; rows there would block future attachment |
| 9 | An open position without a stop is refused | `ck_position_open_has_stop` |
| 10 | A pattern stop above its pivot is refused | `ck_pattern_stop_below_pivot` |
| 11 | Sentiment without a model version is refused | `ck_news_sentiment_provenance` |
| 12 | Validation starting before training ends is refused | `ck_model_validation_after_training` |
| 13 | Monte Carlo below 100 iterations is refused | `ck_montecarlo_iterations` |
| 14 | JSONB round-trips and supports containment queries | `test_jsonb_containment_is_queryable` |
| 15 | Config hashing is deterministic and order-independent | 8 canonicalisation tests |
| 16 | Changing a threshold changes strategy identity | `differs_from` reports the exact dotted path |
| 17 | Editing prose does not change identity | `test_editing_the_description_does_not_change_the_strategy` |
| 18 | Proportional weight sets are the same strategy | `test_weights_are_normalised_before_hashing` |
| 19 | The shipped config matches the code defaults | `test_the_file_matches_the_code_defaults` |
| 20 | Incoherent configurations are refused | 4 cross-section tests |
| 21 | A manifest identifies a reproducible context | `reproduces()` sensitive to config, data, features, model, code, `as_of` |
| 22 | Artifact kinds cannot be swapped between slots | `test_artifact_kinds_are_enforced_by_slot` |
| 23 | The job catalogue is acyclic and fully resolved | `validate_catalogue`, `execution_order` |
| 24 | No trading blocker depends on a non-blocker | `test_a_trading_blocker_never_depends_on_a_non_blocker` |
| 25 | Corporate actions are ingested before bars | `test_bars_are_ingested_after_corporate_actions` |
| 26 | Every provider protocol is satisfiable | Synthetic provider satisfies all four data protocols |
| 27 | All Mermaid diagrams parse | 9/9 with the Mermaid 11 parser |
| 28 | Phase 1 guarantees still hold | All 109 Phase 1 tests pass unchanged |

### Verification summary

```
ruff check           clean
ruff format --check  clean
mypy --strict        no issues in 39 source files
pytest               185 passed  (160 unit + 25 integration, PostgreSQL 16)
mermaid parse        9 diagrams, 0 failures
```

Coverage is 74% overall, down from 83% — arithmetically, because Phase 2 added
~900 lines of interface definitions that are `Protocol` bodies and dataclasses
with no implementations to exercise. The modules with logic remain well covered:
`versioning.py` 97%, `strategy/config.py` 96%, `scheduling/jobs.py` 100%,
`storage/tables.py` 99%, `repositories.py` 96%, `pipeline.py` 99%.

---

## What Phase 3 requires

**Available now:**

- `BarRepository.history(clock, instrument_id, …)` — clock-gated, adjusted,
  chronological series
- `InstrumentRepository.universe(clock, name)` — survivorship-safe candidates
- `TradingCalendar` — session arithmetic, early closes
- `Indicator` / `CrossSectionalFeature` / `FeatureVector` / `RegimeClassifier`
  protocols
- `indicator_values`, `market_regime_states`, `sector_strength` tables, ready
- `StrategyConfig.technical` — every parameter Phase 3 needs, already validated
- `ArtifactVersion` for the feature-set digest each indicator row carries

**Decisions needed before Phase 3 starts:**

1. **Benchmark instrument** for relative strength (open issue 2).
2. **Sector classification scheme** (open issue 3). Phase 3 can build indicators,
   relative strength and regime without it and add sector aggregates after.

**Constraints Phase 3 must honour:**

- **Causality.** The value at bar *t* uses bars ≤ *t* only. The property test —
  computing over `bars[:k]` equals the first *k* values of computing over all
  bars, for every *k* — must be written before the indicators it guards.
- **Warm-up.** Return `None` until there is enough history. A 200-day average of
  40 bars is a different indicator with the same name.
- **Cross-sectional features rank within the point-in-time universe**, never
  against a separately fetched list — that is how delisted names silently vanish.
- **No direct database access.** Read through repositories.
- **No parameters in code.** Everything from `StrategyConfig` (ADR-0008).
- **Every indicator row carries a `feature_set_digest`** so a changed definition
  writes new rows rather than silently overwriting.

**Suggested deliverables:** a causal indicator library with warm-up contracts,
relative strength against benchmark and sector, multi-timeframe alignment, a
regime classifier, and the causality property test applied to every indicator.

---

**Phase 3 is not started and will not be started until explicitly instructed.**
