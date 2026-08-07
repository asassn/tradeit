# Roadmap

Phases are gated: each one ends with a written report, and the next does not
start until it is explicitly authorised. The sequencing rule is that **nothing
gets built on top of an unverified foundation** — which is why backtesting comes
after portfolio construction rather than alongside screening, and why machine
learning comes last or not at all.

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation, domain model, point-in-time data layer | ✅ Complete |
| 2 | Market analytics: indicators, relative strength, regime, sectors | Not started |
| 3 | Screening: liquidity, quality, fundamental and technical filters | Not started |
| 4 | Patterns, breakout detection and confirmation, opportunity scoring | Not started |
| 5 | Portfolio construction: sizing, heat, correlation, allocation | Not started |
| 6 | Position management: stops, trailing, pyramiding, capital recycling | Not started |
| 7 | Backtesting, walk-forward, Monte Carlo, attribution | Not started |
| 8 | Paper trading, journalling, explainability, API and dashboard | Not started |

---

## Phase 1 — Foundation ✅

Domain model, bitemporal storage, as-of clock, calendar, provider protocols,
ingestion with quarantine, synthetic provider, migrations, CI. See
`docs/PHASE_01.md`.

## Phase 2 — Market analytics

Indicators (moving averages, ATR, RSI, MACD, ADX, volume statistics), relative
strength vs. benchmark and sector, multi-timeframe alignment, sector aggregates
and rotation, and a market-regime classifier.

The whole phase computes on clock-gated bar series, so **every indicator must be
causal**: value at bar *t* uses bars ≤ *t* only. A centred moving average or a
z-score computed over the full sample is a leak wearing a respectable name.

Needs from Phase 1: `BarRepository.history`, the calendar, adjustment policy.
Adds: an indicator library with a warm-up contract (indicators return `None`
until they have enough history, rather than a wrong number).

## Phase 3 — Screening

Liquidity and tradability filters (dollar volume, price floor, spread proxy),
quality filters, fundamental filters using as-filed data, technical filters. A
declarative, versioned filter-chain description so a historical screen can be
reproduced exactly — including which version of the rules ran.

Needs from Phase 2: indicators and relative strength.
Open question: which fundamental metrics, which requires the vendor decision.

## Phase 4 — Patterns, breakouts, and scoring

Base/consolidation detection, volatility contraction, pivot identification,
breakout triggers, and — most importantly — **confirmation**: volume expansion,
follow-through, and failure detection.

Scoring combines the evidence into a single opportunity score with a per-factor
contribution breakdown, so every recommendation can be explained. Scoring is
rule-based here. A learned model is Phase 9 at the earliest, and only against a
rule-based baseline that already works.

Needs from Phase 3: a screened candidate set.

## Phase 5 — Portfolio construction

Where "a great stock" becomes "a great trade for this portfolio right now":
position sizing from stop distance and risk budget, total portfolio heat limits,
correlation and cluster exposure, sector concentration caps, and ranking
competing uses of capital.

Needs: a portfolio state model (positions, cash, open risk) — this is where the
`Position`/`Portfolio`/`Order` domain models get defined, deliberately not in
Phase 1.

## Phase 6 — Position management

Initial and trailing stops, partial exits, pyramiding rules, time stops,
earnings-related exits, and capital recycling as positions close. Compounding is
a property of this loop, not a separate feature: gains are redeployed under the
same risk budget.

## Phase 7 — Evaluation

Event-driven backtester reusing the *same* screening and portfolio code paths as
live — no parallel implementation. Realistic costs: commission, slippage as a
function of participation rate, gap risk, and no fills at prices that did not
trade. Walk-forward validation, Monte Carlo on trade sequence and on parameter
perturbation, and attribution by factor, sector, and regime.

This phase needs its own bias controls: parameter-selection bias, multiple-
comparison correction, and out-of-sample discipline. Phase 1's controls address
data leakage, not overfitting.

## Phase 8 — Paper trading and interface

Paper broker with realistic fills, the daily job pipeline, a trade journal
capturing the full decision context (score breakdown, portfolio state,
alternatives rejected), post-trade analysis, strategy-decay monitoring, a
FastAPI service, and a dashboard.

Live trading remains disabled throughout, per ADR-0004.

## Deliberately unscheduled

**Machine learning.** The brief lists XGBoost and PyTorch as available, not
required. A learned model is worth adding when there is a rule-based baseline
with a validated edge and enough independent trades to train without overfitting
— several hundred at minimum. Introduced earlier, it mostly launders look-ahead
bias into a plausible-looking score.

**Live execution.** Requires separate authorisation, a broker adapter, and its
own reliability and reconciliation design.
