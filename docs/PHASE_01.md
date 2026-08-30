# Phase 1 — Foundation, Domain Model, and Point-in-Time Data Layer

**Status:** Complete · **Date:** 2026-08-07

---

## Scope

Phase 1 built the layer everything else stands on: the vocabulary of facts, the
storage that makes historical decisions reproducible, and the enforcement that
makes look-ahead bias hard rather than merely discouraged.

**Explicitly out of scope**, and not built: indicators, screening, pattern
detection, breakout logic, scoring, position sizing, portfolio state,
backtesting, paper trading, the API, and the dashboard. Those belong to Phases
2–8. Signal, position, order and portfolio models are also absent — inventing
their shape before the logic that uses them exists would guarantee a rewrite.

---

## Decisions made

### 1. Bitemporal, append-only storage with clock-gated reads — [ADR-0002](adr/0002-point-in-time-bitemporal-storage.md)

Every fact carries `event_time` (when it happened), `knowledge_time` (when it
became knowable), and `knowledge_source` (whether that timestamp was reported by
a vendor or estimated by us). Revisions insert new rows; nothing is updated in
place. Reads go through repositories that require an `AsOfClock` and filter
`knowledge_time <= as_of`.

**There is no repository method that returns "the latest" data.** That is the
single most consequential decision in the phase. It means leakage requires
deliberately writing raw SQL rather than merely forgetting a filter.

### 2. Instrument identity is a surrogate key, not a ticker

Tickers get recycled — a symbol freed by an acquisition is often reassigned
within months. `symbol_mappings` binds ticker to `instrument_id` over half-open
date intervals, so resolving a symbol always requires a date. On PostgreSQL an
`EXCLUDE USING gist` constraint makes overlapping claims on the same ticker
impossible while still permitting legitimate reassignment.

### 3. Universe membership is interval-based

`universe_memberships` stores when an instrument was in a universe, with an exit
reason. A screen run as of 2015 sees the companies listed in 2015, including
those that later delisted or went bankrupt. This is the survivorship control.

### 4. Raw prices, adjusted at read time — [ADR-0005](adr/0005-raw-prices-with-read-time-adjustment.md)

Adjusted price series are functions of the future and must not be persisted.
Bars are stored exactly as printed; corporate actions are separate bitemporal
facts; adjustment happens on read using only the actions the clock could see.
Volume is scaled inversely to split ratios so dollar volume is invariant —
otherwise a split shows up as a phantom volume breakout.

`SPLIT_ONLY` is the default for analysis, not total-return: a dividend gap is a
real price the market traded through, and smoothing it distorts support and
resistance levels.

### 5. Vendor-neutral provider protocols — [ADR-0003](adr/0003-vendor-neutral-data-providers.md)

Capability-split `Protocol` classes rather than a fat base class. Adapters
declare their limits via `ProviderCapabilities` — notably whether they supply
real filing timestamps and delisted instruments — and the ingestion run records
it, so a backtest report can state whether its data was backtest-grade instead
of relying on someone's memory.

### 6. Live trading requires an out-of-band authorization file — [ADR-0004](adr/0004-live-trading-safety-interlock.md)

`trading_mode = live` alone raises `ConfigError`. It also requires
`~/.tradeit/LIVE_TRADING_AUTHORIZED` containing an exact phrase — a file that
exists in no image, no repository, and no environment.

### 7. Decimal for money, float for indicators

Prices, cash and share quantities are `Decimal`, stored as `NUMERIC`. Floats
remain fine for indicators and scores. A cash ledger that must reconcile to the
cent after ten thousand fills cannot be built on binary floating point.

### 8. Timezone-awareness enforced at the type layer

A `UTCDateTime` column type rejects naive datetimes on write and attaches UTC on
read, so the point-in-time guarantee does not silently depend on which database
is underneath. This was found the hard way: SQLite returns naive datetimes for
`DateTime(timezone=True)`, which would have made every clock comparison in the
unit suite either crash or, worse, quietly compare the wrong things.

### 9. Nothing is dropped silently

Rows that fail validation go to `quarantined_rows` with their raw payload and
the rejection reason. A gap in a price series must always be explainable —
otherwise a failed load is indistinguishable from a market holiday.

The highest-value instance: fundamentals whose `knowledge_time` equals their
period end are quarantined, because a vendor reporting that is telling us it has
no filing dates, and accepting those rows grants roughly six weeks of hindsight
on every fundamental factor.

---

## Assumptions

Each of these is a judgement call made to keep Phase 1 moving. Any of them can
be revisited without redesign.

| # | Assumption | If wrong |
|---|---|---|
| 1 | US equities, USD, single currency | Multi-currency needs an FX rate table (itself bitemporal) and a currency dimension on cash |
| 2 | Daily bars are the primary timeframe; intraday is modelled but not populated | Intraday needs bar partitioning and a different ingestion cadence; the schema already supports it |
| 3 | Long-only for now; `Side.SHORT` exists but nothing implements it | Shorting adds borrow availability, borrow cost, and hard-to-borrow constraints |
| 4 | Daily bars are knowable 20 minutes after the close | Configurable; a vendor with a longer lag needs the setting raised, not a code change |
| 5 | Vendors will supply real filing timestamps for fundamentals | If not, `assumed_filing_lag_days` applies a 45-day fallback and marks rows `ESTIMATED`; results must then be reported as optimistic |
| 6 | Instrument metadata (exchange, asset class, CIK) is effectively immutable | If an instrument genuinely changes exchange, it needs an interval table like tickers already have |
| 7 | PostgreSQL 16+ in production; SQLite only for unit tests | The `EXCLUDE` constraints and `timestamptz` semantics are PostgreSQL-specific by design |
| 8 | Single-node ingestion; no concurrent writers | Concurrent loaders would need advisory locks around the run-audit sequence |

---

## Architecture changes

Phase 1 started from an empty repository, so everything is new. Two things are
worth flagging as changes to what a reader might have expected:

**The `AsOfClock` is a required argument, not ambient context.** A thread-local
or context-manager clock would read more cleanly, but it would also make it
possible to read data without any clock at all — and the whole point is that
this must be impossible. Explicitness is the feature.

**Domain models are separate from ORM tables.** The Pydantic models carry
validation and invariants; the SQLAlchemy tables carry storage. There is
translation code between them, which is real cost. It buys the ability to
validate facts before they reach a database, to enforce invariants that SQL
`CHECK` constraints cannot express, and to change the physical schema without
changing the domain vocabulary.

---

## Risks

| Risk | Severity | Mitigation now | Residual |
|---|---|---|---|
| A future write path bypasses the repositories and reintroduces leakage | **High** | DB-level `CHECK` constraints as backstop; repositories are the only documented read path | Code review must catch raw SQL in analytics code. Consider a lint rule in Phase 2 |
| Vendors do not supply real filing timestamps | **High** | Quarantine `knowledge_time == period_end`; `ProviderCapabilities` declares it; `ESTIMATED` provenance propagates | Unresolved until a vendor is chosen. Directly affects how much any fundamental-driven backtest can be believed |
| Synthetic data flatters later phases | **Medium** | Documented as a test instrument, not a simulator; ADR-0003 forbids using its statistics to justify strategy | Real data will be messier. Phase 2 indicators may need robustness work they did not appear to need |
| Append-only tables grow without bound | **Medium** | Indexes lead with the query shape; revisions are rare in practice | No partitioning yet. A full US universe with intraday bars will need `session_date` range partitioning |
| The calendar fallback is wrong around one-off closures | **Low** | `pandas_market_calendars` is a hard dependency; `uses_fallback` is surfaced by `tradeit config` | If the fallback is ever used in a backtest, session counts near unusual closures will be wrong |
| Overfitting and selection bias | **Not addressed** | — | Out of scope by design. Phase 7 needs its own controls; Phase 1's guarantees say nothing about them |
| Wall-clock drift in live operation | **Low** | `assert_fresh()` refuses to act on a clock >15 min stale | Does not detect a clock that is wrong but current (NTP failure) |

---

## Open issues

1. **Which data vendor?** Blocks Phase 3's fundamental filters. The decision
   should be driven by three questions in order: does it supply real filing
   timestamps, does it include delisted instruments, and how far back does it go?
   Price-per-month is a distant fourth — a cheap vendor without filing dates
   produces backtests that cannot be trusted, which is worse than no backtest.
2. **Which universe definition?** "US equities" needs a concrete rule — exchange
   list, minimum price, minimum dollar volume, and whether ADRs, REITs and ETFs
   are in scope. Affects Phase 3.
3. **Sector and industry classification.** Not modelled yet. Sector rotation
   (Phase 2) needs a scheme, and classifications change over time, so it will
   need interval semantics like tickers.
4. **Benchmark instruments.** Relative strength needs a benchmark; it is not yet
   decided whether that is SPY, an index series, or a configurable per-run
   choice.
5. **Portfolio starting capital and risk budget.** Needed before Phase 5 can
   define position sizing meaningfully.
6. **Intraday data.** The schema supports it; nothing populates it. Whether
   breakout confirmation (Phase 4) needs intraday volume is an open design
   question with a significant cost difference.

None of these block Phase 2, which operates on price series alone.

---

## File and folder structure

```
tradeit/
├── README.md
├── Makefile                     install · fmt · lint · type · test · migrate · demo
├── Dockerfile
├── docker-compose.yml           postgres 16 + redis 7
├── pyproject.toml               deps, ruff, mypy (strict), pytest config
├── alembic.ini
├── .env.example
│
├── docs/
│   ├── ARCHITECTURE.md          layers, data model, bias-control table
│   ├── ROADMAP.md               all eight phases
│   ├── PHASE_01.md              this document
│   └── adr/
│       ├── 0001-record-architecture-decisions.md
│       ├── 0002-point-in-time-bitemporal-storage.md
│       ├── 0003-vendor-neutral-data-providers.md
│       ├── 0004-live-trading-safety-interlock.md
│       └── 0005-raw-prices-with-read-time-adjustment.md
│
├── src/tradeit/
│   ├── config.py                settings + live-trading interlock
│   ├── errors.py                exception hierarchy
│   ├── logging.py               structlog configuration
│   ├── cli.py                   config · init-db · demo-ingest
│   ├── core/
│   │   ├── clock.py             AsOfClock — the leakage guard
│   │   ├── calendar.py          sessions, holidays, early closes, day arithmetic
│   │   ├── models.py            bitemporal domain facts
│   │   ├── money.py             Decimal discipline
│   │   └── enums.py             persisted controlled vocabularies
│   ├── data/
│   │   ├── provider.py          capability-split protocols
│   │   ├── registry.py          name-based adapter lookup
│   │   └── providers/synthetic.py
│   ├── ingest/pipeline.py       append-only writes, quarantine, run audit
│   └── storage/
│       ├── tables.py            schema + UTCDateTime column type
│       ├── session.py           engine, session scope, statement timeout
│       └── repositories.py      the only sanctioned read path
│
├── migrations/versions/0001_initial.py
│
└── tests/
    ├── conftest.py
    ├── unit/                    clock · models · calendar · point-in-time ·
    │                            config/ingest · synthetic provider
    └── integration/             end-to-end (SQLite) · PostgreSQL-specific
```

---

## Acceptance criteria

All criteria below were verified by running them, not by inspection.

| # | Criterion | Evidence |
|---|---|---|
| 1 | A bar is invisible before its publication lag elapses | `test_a_bar_is_invisible_before_its_publication_lag_elapses` |
| 2 | A walk-forward loop never sees a future bar | `test_a_walk_forward_never_sees_the_future` — 12 monthly steps over 2 years |
| 3 | A vendor revision is invisible until published, then replaces the original | `test_the_original_value_is_returned_before_the_correction_was_published` |
| 4 | Restated fundamentals return the as-filed value at the earlier date | `test_fundamental_series_uses_as_filed_values` |
| 5 | A delisted company is still in the universe on a past date | `test_a_delisted_company_is_still_in_the_universe_on_a_past_date` |
| 6 | A recycled ticker resolves to the right company for the date | `test_a_recycled_ticker_resolves_to_the_right_company` |
| 7 | Prices are unadjusted before a split is announced, adjusted after | `test_prices_are_unadjusted_before_a_split_is_announced` |
| 8 | Dollar volume is invariant under split adjustment | `test_dollar_volume_survives_a_split_adjustment` |
| 9 | An earnings date is invisible until announced | `test_an_earnings_date_is_invisible_until_the_company_announces_it` |
| 10 | A filing stamped with its period end is quarantined, not stored | `test_a_filing_stamped_with_its_period_end_is_quarantined` |
| 11 | Rejected rows are quarantined with payload and reason, never dropped | `test_a_mismatched_timeframe_is_quarantined_not_dropped` |
| 12 | Re-running an ingest is idempotent; revisions still stored | `test_replaying_the_same_window_is_idempotent`, `test_on_conflict_do_nothing_makes_replays_idempotent` |
| 13 | Live mode is refused without the authorization file and phrase | 4 tests in `TestLiveTradingInterlock` |
| 14 | A clock cannot be rewound; a stale live clock refuses to act | `test_clock_cannot_rewind`, `test_stale_live_clock_is_rejected` |
| 15 | Naive datetimes cannot be persisted or constructed | `test_naive_timestamps_cannot_be_persisted`, `test_naive_timestamps_are_rejected` |
| 16 | Overlapping ticker intervals are rejected by the database | `ex_symbol_no_overlap`, verified against PostgreSQL 16 |
| 17 | Migration applies cleanly and the ORM works against the migrated schema | `test_migration_produces_a_schema_the_orm_can_use` |
| 18 | `NUMERIC` columns preserve exact decimals | `test_numeric_columns_preserve_exact_decimals` |
| 19 | The pipeline runs end-to-end without a vendor account or network | `make demo` — 2,760 bars across 8 instruments, re-runnable |
| 20 | Lint, strict typing, and the full suite pass | ruff clean · mypy strict clean on 22 modules · 109 tests pass |

### Verification summary

```
ruff check           clean
ruff format --check  clean
mypy --strict        no issues in 22 source files
pytest               109 passed  (102 SQLite + 7 PostgreSQL 16)
coverage             83% overall
```

Coverage is 83% rather than higher because `cli.py`, `logging.py` and
`session.py` have no automated tests — they are thin wiring over tested code.
The CLI was verified manually end-to-end against PostgreSQL, twice, to confirm
idempotency. The modules that carry the guarantees are covered at 96–100%:
`repositories.py` 96%, `models.py` 97%, `pipeline.py` 99%, `tables.py` 99%,
`config.py` 100%.

---

## What the next phase requires

> **Numbering note (added after Phase 2).** Phase 2 was subsequently defined as
> system architecture and data design, so the analytics work described below is
> now **Phase 3**. See [`ROADMAP.md`](ROADMAP.md), which is authoritative.


**Available from Phase 1**, nothing further needed:

- `BarRepository.history(clock, instrument_id, ...)` — clock-gated, adjusted,
  chronological price series
- `TradingCalendar` — session arithmetic, early closes, `shift_sessions`
- `InstrumentRepository.universe(clock, name)` — survivorship-safe candidate set
- `SyntheticProvider` — deterministic series with a planted breakout to test
  detectors against

**Decisions needed before Phase 2 starts:**

1. **Benchmark instrument** for relative strength (open issue 4).
2. **Sector classification scheme** for sector rotation (open issue 3). Phase 2
   can build market-regime and relative-strength work without it and add sector
   aggregates once chosen.

**Constraints Phase 2 must honour:**

- **Every indicator must be causal.** The value at bar *t* may use bars ≤ *t*
  only. Centred moving averages, full-sample z-scores and full-sample
  normalisation are leaks with respectable names.
- **Indicators must declare a warm-up period** and return `None` until they have
  enough history, rather than returning a wrong number computed from a short
  window.
- **No direct database access.** Analytics read through repositories, so the
  point-in-time guarantee holds transitively.
- **Bar series may contain gaps** (halts, suspect data excluded by quality
  flags). Indicators must handle a series shorter than the requested window.

**Suggested Phase 2 deliverables:** a causal indicator library with warm-up
contracts, relative strength vs. benchmark and sector, multi-timeframe
alignment, a market-regime classifier, and a test suite that asserts causality
by construction — computing each indicator over a truncated series and
confirming the values match the full-series prefix.

---

**No further phase is started until explicitly instructed.**
