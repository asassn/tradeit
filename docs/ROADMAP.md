# Roadmap

Phases are gated: each ends with a written report, and the next does not start
until explicitly authorised. The sequencing rule is that **nothing is built on
an unverified foundation** — which is why execution modelling precedes
backtesting, and why machine learning comes last or never.

> **Numbering note.** The Phase 1 report listed an eight-phase plan in which
> Phase 2 was market analytics. Phase 2 was subsequently defined as system
> architecture and data design, so everything below it shifted by one and a live
> trading phase was made explicit. The table here is authoritative; earlier
> references to "Phase 2 — market analytics" mean what is now Phase 3.

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation, domain model, point-in-time data layer | ✅ Complete |
| 2 | System architecture, database design, interfaces, configuration | ✅ Complete |
| 3 | Market analytics: indicators, relative strength, regime, sectors | Not started |
| 4 | Screening: liquidity, quality, fundamental and technical filters | Not started |
| 5 | Patterns, breakout detection and confirmation, opportunity scoring | Not started |
| 6 | Portfolio construction, risk engine, position management | Not started |
| 7 | Execution: cost and fill models, paper broker, reconciliation | Not started |
| 8 | Backtesting, walk-forward, Monte Carlo, attribution | Not started |
| 9 | Paper trading, journal, API, dashboard, monitoring | Not started |
| 10 | Live trading | Requires separate written authorisation |

---

## Phase 1 — Foundation ✅

Bitemporal storage, the as-of clock, exchange calendar, provider protocols,
ingestion with quarantine, synthetic provider, migrations, CI.
See [`PHASE_01.md`](PHASE_01.md).

## Phase 2 — Architecture and data design ✅

Complete technical architecture, 41-table schema validated against
PostgreSQL 16 with partitioning, six provider interfaces, the domain interface
set for every later phase, content-addressed reproducibility, the versioned
configuration system, and the 22-job schedule.
See [`PHASE_02.md`](PHASE_02.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Phase 3 — Market analytics

Indicators (moving averages, ATR, RSI, MACD, ADX, volume statistics), relative
strength against benchmark and sector, multi-timeframe alignment, sector
aggregates, and a market-regime classifier.

**Constraint that defines the phase:** every indicator must be causal — the
value at bar *t* uses bars ≤ *t* only. Centred moving averages, full-sample
z-scores and universe-wide percentile ranks computed once over all history are
leaks with respectable names. The causality property test must exist before the
indicators it guards.

*Needs:* benchmark instrument, sector classification scheme.
*Provides:* `IndicatorValue` rows, `market_regime_states`, `sector_strength`.

## Phase 4 — Screening

Liquidity and tradability filters, quality filters, fundamental filters on
as-filed data, technical filters. The filter chain is declarative and versioned,
so a historical screen reproduces exactly — including which version of the rules
ran.

*Needs:* Phase 3, and the data vendor decision for fundamental filters.
*Provides:* candidate sets, `screen_rejections`.

## Phase 5 — Patterns, breakouts, scoring

Base and consolidation detection, volatility contraction, pivot identification,
breakout triggers, and — most importantly — confirmation: volume expansion,
follow-through, failure detection.

Scoring combines evidence into a single ranked score with a stored per-factor
breakdown. Rule-based. A learned model is not scheduled.

*Provides:* `patterns`, `breakout_events`, `opportunity_scores`, `watchlists`.

## Phase 6 — Portfolio construction and risk

Position sizing from stop distance and risk budget, portfolio heat limits,
correlation and cluster exposure, sector caps, allocation ranking, and position
management: stops, trailing, partial exits, pyramiding, time stops,
earnings-related exits, capital recycling.

Compounding is a property of this loop, not a separate feature: gains are
redeployed under the same risk budget.

*Needs:* starting capital and risk budget parameters.
*Provides:* `positions`, `risk_snapshots`, `portfolio_snapshots`.

## Phase 7 — Execution

Cost model (commission, spread, participation-dependent impact), fill model
(no fills outside the bar, gaps respected, volume caps participation), paper
broker, execution service with broker reconciliation.

**Built before backtesting on purpose.** The backtester must use the same cost
and fill models the paper broker uses; building execution first prevents it from
growing its own optimistic assumptions.

*Provides:* `orders`, `executions`, `trades`.

## Phase 8 — Evaluation

Event-driven backtester that advances a clock and calls the *same* components as
live — no parallel implementation. Walk-forward validation, Monte Carlo on trade
sequence and parameter perturbation, attribution by factor, sector and regime.

This phase needs its own bias controls: parameter-selection bias, multiple-
comparison correction, out-of-sample discipline. Phases 1–2 addressed data
leakage, not overfitting.

*Provides:* `backtest_runs`, `backtest_trades`, `monte_carlo_runs`.

## Phase 9 — Paper trading and surface

Paper trading on the live code path, the daily job pipeline running end to end,
trade journal capturing full decision context including rejected alternatives,
post-trade analysis, strategy-decay monitoring, FastAPI service, dashboard.

Live trading remains disabled throughout.

## Phase 10 — Live trading

Requires separate written authorisation, a live broker adapter, and its own
reliability, reconciliation and incident-response design. The interlock in
ADR-0004 stays in force regardless of how much of the above is complete.

---

## Deliberately unscheduled

**Machine learning.** The brief lists XGBoost and PyTorch as available, not
required. A learned model becomes worth adding when a rule-based baseline shows
a validated edge across several hundred out-of-sample trades. Introduced
earlier, it mostly launders look-ahead bias into a plausible-looking score. The
schema (`model_metadata`, with training-window CHECK constraints) is ready for
that day; nothing else is.

**News and sentiment.** Interfaces and storage exist. No phase consumes them,
because news is the most leak-prone dataset in the system and it should not be
added until something specific needs it.
