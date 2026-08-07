# Roadmap — canonical

**This numbering is authoritative.** It was fixed by written authorisation after
Phase 2 and must be used consistently in all documentation, commits, phase
reports and cross-references. It is not to be renumbered again without explicit
authorisation.

| Phase | Scope | Status |
|---|---|---|
| 1 | Causal / point-in-time data foundation | ✅ Complete |
| 2 | System architecture & data design | ✅ Complete |
| 3 | Market analytics foundation | ✅ Complete |
| 4 | Pattern recognition | Not started |
| 5 | Breakout detection & confirmation | Not started |
| 6 | Fundamentals & earnings quality | Not started |
| 7 | Opportunity scoring | Not started |
| 8 | Portfolio construction, risk & compounding | Not started |
| 9 | Portfolio backtesting & Monte Carlo analysis | Not started |
| 10 | Dashboard / research interface | Not started |
| 11 | Paper trading execution | Not started |
| 12 | End-to-end validation & production readiness | Not started |
| — | **Live trading** | **Outside the roadmap.** Disabled until separately and explicitly authorised after successful paper trading and validation |

Phases are gated: each ends with a written report, and the next does not start
until explicitly authorised.

> **Historical note.** Two earlier numbering schemes appear in the Phase 1 and
> Phase 2 reports, which were written before this roadmap was fixed. Where they
> conflict, this table wins. Those documents carry pointers here.

---

## Phase 1 — Causal / point-in-time data foundation ✅

Bitemporal storage, the as-of clock, exchange calendar, provider protocols,
ingestion with quarantine, synthetic provider, migrations, CI.
See [`PHASE_01.md`](PHASE_01.md).

## Phase 2 — System architecture & data design ✅

Complete technical architecture, 41-table schema validated against
PostgreSQL 16 with partitioning, six provider interfaces, the domain interface
set, content-addressed reproducibility, versioned configuration, 22-job
schedule. See [`PHASE_02.md`](PHASE_02.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Phase 3 — Market analytics foundation ✅

The causal feature layer everything above it depends on: 26 technical
indicators, causal multi-timeframe construction, multi-benchmark relative
strength, sector strength, market breadth, a transparent market-regime
classifier, a volatility-regime model, and a formal feature registry.

See [`PHASE_03.md`](PHASE_03.md) and [`ANALYTICS.md`](ANALYTICS.md).

## Phase 4 — Pattern recognition

Base and consolidation geometry: flat bases, cup-with-handle, VCP, ascending
triangles, bull flags, pennants. Produces pivot and stop levels — the two
numbers that determine entry and position size.

*Consumes from Phase 3:* rolling highs/lows, ATR and range contraction, volume
contraction, distance-from-high, moving-average structure.
*Blocked by:* nothing in Phase 3. A vendor decision improves the universe but
does not gate pattern geometry.

## Phase 5 — Breakout detection & confirmation

Approach, trigger, confirmation, failure. The distinction between *triggered*
and *confirmed* is the difference between buying breakouts and buying failed
breakouts.

*Consumes from Phase 3:* relative volume, volatility regime, relative strength,
market regime.

## Phase 6 — Fundamentals & earnings quality

Growth, quality and balance-sheet screens on as-filed data, with
REIT-appropriate metrics for REIT-tagged instruments. Earnings surprise and
revision history.

*Blocked by:* the data vendor decision. See
[`VENDOR_EVALUATION.md`](VENDOR_EVALUATION.md).

## Phase 7 — Opportunity scoring

Three distinct scores, deliberately separated:

1. **Standalone Opportunity Score** — how good is this setup, in isolation?
2. **Portfolio Fit Score** — how well does it fit *this* portfolio right now?
3. **Final Portfolio Opportunity Score** — the combination that ranks capital.

The separation is the point: a great stock the portfolio already effectively
owns three times over is not a great trade.

## Phase 8 — Portfolio construction, risk & compounding

Risk-based sizing, portfolio heat, correlation and sector limits, allocation
ranking, stops and trailing, pyramiding, capital recycling. Compounding is a
property of this loop rather than a separate feature.

## Phase 9 — Portfolio backtesting & Monte Carlo

Event-driven backtester that advances a clock and calls the same components as
live. Walk-forward, Monte Carlo on trade sequence and parameter perturbation,
attribution by factor, sector and regime. Needs its own overfitting controls.

## Phase 10 — Dashboard / research interface

FastAPI service and dashboard over produced results. See [`API.md`](API.md).

## Phase 11 — Paper trading execution

Cost and fill models, paper broker, execution service with reconciliation,
trade journal, running on the live code path with a simulated venue.

## Phase 12 — End-to-end validation & production readiness

Full-pipeline validation, operational runbooks, monitoring, incident response,
strategy-decay detection, and the evidence required before live trading could
even be discussed.

---

## Live trading

**Not a roadmap phase.** It remains disabled by the interlock in
[ADR-0004](adr/0004-live-trading-safety-interlock.md) and becomes possible only
after Phase 12, on separate and explicit written authorisation. Completing every
phase above does not by itself authorise it.

## Deliberately unscheduled

**Machine learning.** Worth adding when a rule-based baseline shows a validated
edge across several hundred out-of-sample trades. Introduced earlier it mostly
launders look-ahead bias into a plausible score. The schema is ready
(`model_metadata` with training-window constraints); nothing else is.

**News and sentiment.** Interfaces and storage exist; nothing consumes them.
News is the most leak-prone dataset in the system and should wait until
something specific needs it.
