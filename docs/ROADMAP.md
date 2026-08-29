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
| 4 | Pattern recognition | ✅ Complete |
| 5 | Breakout detection & confirmation | ✅ Complete |
| — | Empirical data access & validation gate | ✅ Complete (Outcome B) |
| 6 | Fundamentals & earnings quality | In progress. **Milestone 0b complete — 30/30 controls fully adjudicated.** 0a implemented but never run against real EDGAR data. **0c deliberately skipped**: the ~$14 Kibot month was authorised instead, so the probe measures the data rather than a vendor's answers about it — which moves the schema questions onto the probe too. **0d remains outstanding and undecided** (Twelve Data / FMP retention, still `UNVERIFIED`, and it governs whether `full-01` can serve as permanent history). **Milestone 1, the Kibot probe, is the live thread** |
| — | **Multi-Timeframe & Portfolio Mandate Architecture** | **Not started.** May overlap Phase 6; **must complete before Phase 7** |
| — | **Strategy Definition / Builder Architecture** | **Not started.** Follows the multi-timeframe gate; **must complete before Phase 7** |
| 7 | Opportunity scoring | Not started |
| 8 | Portfolio construction, risk & compounding | Not started |
| 9 | Portfolio backtesting & Monte Carlo analysis — **includes signal research and indicator ranking** | Not started |
| 10 | Dashboard / research interface — **includes valuation callout, multi-horizon ticker panel, live news & attention, onboard AI assistant** | Not started |
| 11 | Paper trading execution | Not started |
| 12 | End-to-end validation & production readiness — **includes the Profitability & Readiness framework and the governed research loop** | Not started |
| — | **News-driven signals** | **Not started, and separately gated.** Requires `research-01`, a backtester, and a news source with honest publication timestamps. Distinct from the *observational* news dashboard in Phase 10 |
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

Complete technical architecture, the 41-table schema **as it stood at Phase 2**
validated against PostgreSQL 16 with partitioning, six provider interfaces, the
domain interface set, content-addressed reproducibility, versioned
configuration, 22-job schedule. Later phases added to it: the current count is
**57**, measured from `tables.py`. See [`PHASE_02.md`](PHASE_02.md) and
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Phase 3 — Market analytics foundation ✅

The causal feature layer everything above it depends on: 58 causal
indicators, causal multi-timeframe construction, multi-benchmark relative
strength, sector strength, market breadth, a transparent market-regime
classifier, a volatility-regime model, and a formal feature registry.

See [`PHASE_03.md`](PHASE_03.md) and [`ANALYTICS.md`](ANALYTICS.md).

## Phase 4 — Pattern recognition ✅

Twelve causal chart-pattern families with declared contracts, a lifecycle, an
append-only history, a relationship taxonomy, human-labelling infrastructure, an
integrated scanner and multi-timeframe support. Produces structural pivot and
invalidation levels — emphatically not stops, which belong to a position.

Answers one question: *does this structure resemble a valid bullish chart
pattern, and how good is it?* It does not judge breakouts, generate trades or
rank candidates.

See [`PHASE_04.md`](PHASE_04.md), [`PATTERN_ARCHITECTURE.md`](PATTERN_ARCHITECTURE.md)
and [`PATTERN_VALIDATION.md`](PATTERN_VALIDATION.md).

*Consumes from Phase 3:* rolling highs/lows, ATR and range contraction, volume
contraction, distance-from-high, moving-average structure.
*Still open:* real-market validation. The corpus is synthetic, and synthetic
data establishes code properties rather than market accuracy
([ADR-0018](adr/0018-synthetic-data-limits.md)). Cross-family score calibration
does not exist and needs labelled real data.

## Phase 5 — Breakout detection & confirmation

Approach, testing, penetration, close, confirmation, retest, rejection, failure
and expiry — thirteen states with a constrained machine between them. The
distinction between *closed above* and *confirmed* is the difference between
buying breakouts and buying failed breakouts, and the distinction between
*failed* and *expired* is what stops non-events inflating the failure rate.

Four numbers per event, deliberately not one: breakout quality (frozen at the
breakout bar), confirmation score (accumulating after it), evidence coverage and
identification confidence. Three confirmation paths — momentum, retest,
acceptance — under three evidence profiles that describe how much evidence is
wanted and say nothing about risk.

Answers one question: *what is price doing relative to an established structural
boundary, and how convincing is that behaviour?* It does not decide whether to
buy anything, and holds no field in which that answer could be recorded.

See [`PHASE_05.md`](PHASE_05.md), [`BREAKOUT_ARCHITECTURE.md`](BREAKOUT_ARCHITECTURE.md)
and [`BREAKOUT_VALIDATION.md`](BREAKOUT_VALIDATION.md).

*Consumes from Phase 3:* relative volume, volatility regime, relative strength,
market regime.
*Consumes from Phase 4:* the pattern's resistance boundary, its confidence and
touch count, and the lifecycle state that says whether it is still live.
*Still open:* real-market validation, unchanged from Phase 4 and now the binding
constraint on the platform. The known weakness worth carrying forward is that the
engine cannot distinguish a genuine level from an arbitrary line — quality and
confidence discriminate, the state alone does not.

## Empirical data access & validation gate

Not a numbered phase: a stop placed between Phase 5 and Phase 6 to prevent
another analytical layer being built on exclusively synthetic data.

Two things came out of it. The weakness carried forward from Phase 5 — that
`CONFIRMED` does not discriminate while the evidence does — became an
architectural invariant: no breakout state, by itself, authorizes a trade, an
opportunity, a position or an allocation. `EvidenceBundle` carries every field a
downstream stage needs and deliberately has no aggregate; `BoundaryKind` records
where a level came from and refuses to let a manual one claim to be structural;
the production monitor accepts only structural boundaries from three pattern
states. See [`ADR-0025`](adr/0025-breakout-state-is-not-trade-eligibility.md).

And the complete offline path for real data: a package format defined by meaning
rather than by vendor column names, a four-stage import that discards nothing and
records every change it makes, a point-in-time layer that will not let a quarter
become knowable on the day it ends, and an 18-check validation harness that
reports blocked checks before passed ones and refuses to compute a performance
statistic.

**Outcome B.** Provider egress is denied in the build environment, so no
empirical result is claimed and none is fabricated. See
[`PHASE_05_GATE.md`](PHASE_05_GATE.md) for what remains blocked and
[`DATA_REQUIRED.md`](../DATA_REQUIRED.md) for what to supply.

## Phase 6 — Fundamentals & earnings quality

Growth, quality and balance-sheet screens on as-filed data, with
REIT-appropriate metrics for REIT-tagged instruments. Earnings surprise and
revision history.

*Blocked by:* the data vendor decision. See
[`VENDOR_EVALUATION.md`](VENDOR_EVALUATION.md).

## Multi-Timeframe & Portfolio Mandate Architecture

**Not a numbered phase**, following the precedent of the empirical gate above.
The canonical numbering is untouched and Phases 3, 4 and 5 are not renumbered.

TradeIt is not one strategy on one timeframe. It will manage **three portfolio
mandates at materially different horizons** — Day, Swing and Retirement — each
with its own timeframe hierarchy, holding horizon, risk model, backtest, KPIs and
capital-graduation criteria.

The core principle: **a security has no global state.** There is no
`breakout = true`. State is scoped by `instrument × timeframe × analytical
episode`, and a security that is in a Monthly uptrend, a Weekly breakout, a Daily
retest and a 5-minute pullback is exhibiting four independent causal
observations, not four contradictions.

*Placement:* **may overlap Phase 6**, and in the data dimension it must — the
corpora being specified now have to serve every mandate, not only the swing one.
**Must complete and validate before Phase 7**, because opportunity scoring is the
first stage that would blend timeframes into a ranking, and blending them before
the populations are separated is the error the whole design exists to prevent.

*Data consequence:* two corpora, not one. `research-01` stays daily, 1998+,
survivorship-safe, serving Swing and Retirement. A separate `intraday-01` —
1-minute base, a shorter high-quality window, expected to be survivorship-biased
and labelled as such — serves the Day mandate and the Swing mandate's triggers.

*Does not include:* the multi-timeframe coordinator's logic, any claim that
timeframe alignment is profitable, or any combined cross-timeframe score.

`full-01` remains the validated **Daily** machinery baseline. Future timeframe
expansion gets separate derivation runs and corpora, and must independently pass
the same causality and provenance gates Daily passed.

See [`MULTI_TIMEFRAME_MANDATES.md`](MULTI_TIMEFRAME_MANDATES.md).

## Strategy Definition / Builder Architecture

**Not a numbered phase.** Same precedent as the two gates above; Phases 3, 4 and
5 are not renumbered.

TradeIt must not be a collection of hard-coded strategies. Strategies must be
**created, edited, versioned, backtested, paper-traded, compared, promoted and
retired** — eventually through the dashboard, without writing Python.

A strategy is a **versioned declarative object composed from capabilities the
platform already has**: universe, timeframe hierarchy, regime requirements,
fundamental filters, indicators, patterns, breakout confirmation, entry, sizing,
stops, exits, portfolio risk and execution assumptions — each a *reference into
an existing engine*, never a reimplementation.

*Placement:* **after the multi-timeframe gate, before Phase 7.** Phase 7 is the
first stage that ranks anything, and "portfolio fit" is meaningless without a
strategy object to fit to. Ranking before strategies are first-class would bake
one implicit hard-coded strategy into the ranking layer — the exact outcome this
milestone exists to prevent. It follows the multi-timeframe gate because a
strategy's timeframe hierarchy is part of its definition.

*The governing constraint:* the builder is an orchestration and definition layer,
**not a loophole**. A strategy definition cannot bypass point-in-time reads,
`knowledge_time` provenance, causal detection, the completed-bar rule,
survivorship eligibility or timeframe eligibility. If a strategy could express
something the engines refuse, the expression is the bug.

*Lifecycle:* `DRAFT → VALIDATED → BACKTESTING → OUT_OF_SAMPLE_TESTING →
PAPER_TRADING → ELIGIBLE_FOR_CAPITAL → LIVE → PAUSED → RETIRED`, with the gates
operating on strategy **versions**. An edit creates a new version and drops it
back to `DRAFT`; it does not inherit its parent's evidence. **No strategy becomes
LIVE because a backtest was profitable**, and live trading remains behind the
[ADR-0004](adr/0004-live-trading-safety-interlock.md) interlock regardless.

*Does not include:* any implementation, any authored strategy, any threshold, any
profitability or comparison metric.

See [`STRATEGY_BUILDER.md`](STRATEGY_BUILDER.md).

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

### Signal research and indicator ranking

**Which indicators actually predict anything, at which horizon, in which
regime.** Not a display feature — a measurement. It lives here rather than in
Phase 7 because it needs exactly the machinery Phase 9 builds: out-of-sample
separation, walk-forward windows, realistic costs and a baseline to beat.

Every study must state its **target** before it runs — next-day return, 5-day
excess return, breakout success, probability of stop before target, 3-month
return. "Will the stock go up" is not a target, and on a multi-timeframe platform
it is not even a question.

Two results are wanted and are different: whether a signal is *statistically*
detectable, and whether it survives spread, slippage, fees and turnover to be
*economically* useful. A signal can pass the first and fail the second, and only
the second matters. Every candidate is compared against simple baselines — buy
and hold, the benchmark, a moving-average rule, a naive classifier — and a
sophisticated model that cannot beat them robustly is not promoted for being
sophisticated.

*Feeds:* Phase 7's scoring weights. Those weights are config-driven and
content-hashed (ADR-0008), so Phase 7 can ship with declared, transparent,
**unvalidated** weights and have them replaced by measured ones later without a
code change. **This ordering is deliberate and the tension is real:** ranking
before the research exists means the first weights are judgement, not evidence,
and they must be labelled as such wherever they appear.

*Deliberately excluded:* machine learning, per the standing position below.

## Phase 10 — Dashboard / research interface

FastAPI service and dashboard over produced results. See [`API.md`](API.md).

Four capabilities belong here and are recorded so they are not forgotten:

**Multi-horizon per-ticker panel.** One security, three independent
conclusions — Day, Swing, Retirement — each with its own supporting and
contradicting factors. A stock may be attractive long-term and unattractive as a
swing; that is information, not a contradiction. **There is no global buy/sell
score**, and the panel must not compute one. This depends on the multi-timeframe
gate above, which is what makes the three horizons separate populations rather
than three labels.

**Valuation and the undervalued / fairly valued / overvalued callout.** A short
human-readable verdict when browsing or selecting a name, backed by multiples,
the security's own valuation history, and sector-relative comparison. **The
callout is never shipped without the reasoning that produced it** — a one-word
label with no visible methodology is an opinion wearing a badge, and it would be
acted on far more confidently than its evidence deserves. Depends on Phase 6
fundamentals.

**Live news, sentiment and attention.** Which names are being mentioned most
right now, with what sentiment, across news and social feeds; and tracked
coverage for names a strategy has selected. This is *observational* and
present-tense: it makes no historical claim, so it carries none of the
look-ahead risk that gates news-driven signals below. Engagement — reposts,
views, velocity — is displayed as **attention, never as truth**: it is
purchasable and adversarial, and popularity is not evidence about a company.
**This surface has no trading authority.**

**Onboard AI assistant.** An assistant inside the platform that answers *why*:
why this ticker scored as it did, what supports the swing signal, what
contradicts the long-term thesis, which indicators mattered, what changed since
yesterday, why a trade was entered or rejected, what regime was active, which
portfolio constraint applied. It **operates over the platform's own structured
data and provenance and explains decisions the system actually made** — it never
invents a reason. If the system cannot reconstruct why something happened, the
correct answer is that it cannot, and the assistant says so. This is why the
explainability requirements in Phases 4, 5, 7 and 8 are load-bearing rather than
decorative: the assistant is only as honest as the record beneath it.

## Phase 11 — Paper trading execution

Cost and fill models, paper broker, execution service with reconciliation,
trade journal, running on the live code path with a simulated venue.

## Phase 12 — End-to-end validation & production readiness

Full-pipeline validation, operational runbooks, monitoring, incident response,
strategy-decay detection, and the evidence required before live trading could
even be discussed.

### The Profitability & Readiness framework

The question this phase exists to answer is not "did the backtest work". It is
**"is this good enough to trust with money, and how would we know if it stopped
being?"**

The measurement contracts already exist: `tradeit.backtesting.base.PerformanceMetrics`
declares total return, CAGR, max drawdown and its duration, Sharpe, Sortino,
Calmar, win rate, profit factor, expectancy in R, average win and loss in R,
trade count, exposure, turnover and benchmark return — with
`statistically_meaningful` (at least thirty trades) and `trustworthy` (completed,
no data caveats, and meaningful) as explicit guards. **What does not exist is the
framework that turns those numbers into a decision**, and it is the deliverable
here.

**No single metric decides readiness**, and no threshold in this framework may be
chosen after seeing the result it would judge. Beyond the headline ratios it must
consider performance by regime, by year and by sector; out-of-sample degradation;
walk-forward consistency; slippage and transaction-cost sensitivity; capacity and
liquidity; concentration; correlation with strategies already running; and
paper-versus-backtest divergence.

Promotion runs `DRAFT → RESEARCHED → VALIDATED → PAPER → LIMITED_LIVE → LIVE`
with explicit criteria at each step and **explicit demotion criteria too** —
`DEGRADED`, `SUSPENDED`, `RETIRED`. A strategy that decays comes back down. The
exact state names are the strategy builder's to settle; the governed promotion
*and demotion* is the requirement.

**Risk controls sit outside a strategy's reach.** Maximum position, portfolio
exposure, daily loss, drawdown, per-strategy allocation, sector concentration,
correlation limits, kill switch, stale-data protection, duplicate-order
protection and execution reconciliation are enforced above the strategy, not
configured by it. Their values need approval and are not inferred.

### The governed research loop

Research continues after deployment, and must not be allowed to quietly rewrite
what is running. The loop is: hypothesis → backtest → out-of-sample validation →
failure-mode analysis → comparison against baselines → logged result → reject or
promote → paper → live-forward evaluation → only then consider promotion.

**The production strategy never mutates automatically because a fresh backtest
looked better.** Every candidate carries its reproducibility metadata — strategy
version, feature version, dataset version, universe definition, date range,
parameters, execution and cost assumptions, code version, result metrics — so no
result of the form "this made 42%" can exist without the means to reproduce it.

### Observability

The system must be able to reconstruct its own decisions: what data arrived,
which signal fired, which strategy version acted, which risk gate approved or
rejected, what order was submitted, what execution occurred, what P&L resulted.
**A live trading system that cannot explain what it did is not acceptable**, and
this is also the substrate the Phase 10 assistant answers from.

---

## News-driven signals

**Not a numbered phase, and separately gated.** Distinct from the observational
news and attention surface in Phase 10, which has no trading authority and can be
built with the dashboard.

A rule of the form "positive news triggers a trade" is not hard to write. Knowing
whether it works is the hard part, and it requires historical news carrying **the
instant each item genuinely became public**. News archives routinely serve
revised timestamps, backfilled articles, and sentiment scored later by models
that did not exist at the time. Backtested against that, a strategy appears to
react minutes before anyone could have read the story, and the resulting equity
curve describes a different universe — the same failure the importer already
refuses for fundamentals, where a row timestamped within a day of its own period
end is rejected outright and no flag relaxes it.

*Unblocked when all three hold:* `research-01` exists as a price spine to measure
reactions against; a backtester exists so a news rule can be tested rather than
asserted; and a news source with honest publication timestamps has been
identified and verified to the standard the fundamentals importer already
enforces.

*Engagement signals* — reposts, views, attention velocity — carry an additional
problem the price and fundamental data do not: they are **purchasable and
adversarial**. They are admissible as a measure of attention and never as
evidence about a company.

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

**News and sentiment** were listed here until the requirement was separated into
its two halves, which turned out to be a scheduling question rather than a
capability question. The *observational* surface — what is being mentioned, with
what sentiment, right now — makes no historical claim and is scheduled in
Phase 10. The *signal* half is scheduled above under News-driven signals, behind
three explicit conditions. Interfaces and storage exist; nothing consumes them
yet, and the `NewsProvider` protocol is still unimplemented by any adapter.
