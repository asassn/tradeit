# Strategy Builder / Strategy Definition Engine

**Design and mandate only. Nothing implemented, no strategy authored, no
threshold tuned, no profitability computed, no live-trading logic written.**

TradeIt must not be a collection of hard-coded strategies. It must let strategies
be **created, edited, versioned, backtested, paper-traded, compared, promoted and
retired** — eventually through the dashboard, without writing Python.

---

## 1. The core idea

> **A strategy is a versioned declarative object composed from capabilities the
> platform already has.**

Not a script. Not a subclass. A *definition* that names which universe, which
timeframes, which detectors, which confirmation rules and which risk model to
compose — and which the existing engines then execute.

The distinction matters for one specific reason: **a script can bypass the
causality guarantees; a declaration cannot.** Every rule TradeIt has accumulated
about point-in-time reads, knowledge time, completed bars and corpus eligibility
lives in the engines. A strategy that *describes* what it wants inherits those
rules. A strategy that *executes its own logic* is a hole in them.

### It composes; it does not reimplement

The builder must call the same indicator, pattern, breakout, fundamentals and
multi-timeframe engines built elsewhere in TradeIt. A strategy that computes its
own RSI, or its own bull-flag geometry, is a second implementation that will
drift from the validated one and produce numbers nobody can reconcile.

---

## 2. Declarative structure

```
STRATEGY
├── mandate                    day | swing | retirement
├── universe
│   ├── exchanges
│   ├── market-cap range
│   ├── sectors / industries
│   ├── liquidity constraints
│   ├── price constraints
│   └── survivorship / data eligibility
├── timeframe hierarchy
│   ├── context timeframe(s)
│   ├── setup timeframe(s)
│   └── trigger timeframe(s)
├── market regime requirements
├── fundamental filters
├── technical indicators
├── pattern requirements
├── breakout / confirmation requirements
├── entry rules
├── position sizing
├── stop / invalidation rules
├── profit-taking / exit rules
├── portfolio-level risk constraints
└── execution assumptions
```

Every branch is a *reference into an existing engine*, not an inline
implementation:

| branch | resolves against |
|---|---|
| universe, survivorship eligibility | `CapabilityIndex`, `SurvivorshipStatus`, corpus classification |
| timeframe hierarchy | `Bartimeframe`, the mandate hierarchies in `MULTI_TIMEFRAME_MANDATES.md` |
| indicators | the Phase 3 feature registry |
| patterns | the Phase 4 detector registry and its `SUPPORTED_TIMEFRAMES` |
| breakout / confirmation | the Phase 5 lifecycle, evidence profiles and confirmation paths |
| fundamentals | the three-date rule; `knowledge_time`, never `period_end` |
| stops / invalidation | structural levels from patterns — *levels*, which are not stops until a position owns them |
| regime | the Phase 3 regime classifier |

**A strategy may only reference what the registries already declare.** A
definition naming an indicator that does not exist, or a detector on a timeframe
its family is not defined for, is invalid — the same refusal
`registry.supports()` already performs, one layer up.

---

## 3. Versioning and reproducibility

Every strategy carries:

```
strategy_id            stable across versions
strategy_version       monotonic; an edit creates a NEW version
created_at
definition_hash        content hash of the full definition
data_snapshot / corpus which corpus, at which snapshot digest
feature_engine_version
detector_versions      per detector, as the Phase 4 baseline already pins
parameter_values
mandate
eligible_timeframes
```

**Editing a strategy creates a new version. History is never mutated in place.**

Every backtest and every paper-trading result references the exact immutable
strategy version that produced it. This is the discipline `scan_runs`,
`config_digest` and `data_snapshot_digest` already implement for scans — the
strategy layer inherits it rather than inventing a parallel scheme, and
`0012_run_scoped_derivation` is the precedent for making it an isolation boundary
rather than a label.

---

## 4. Research governance — the builder is not a loophole

**A strategy definition cannot bypass any existing rule.** Named explicitly so
this cannot erode:

| rule | still applies |
|---|---|
| point-in-time reads | `WHERE knowledge_time <= :as_of`, always, no second path |
| `knowledge_time` provenance | never derived from `period_end`; never manufactured |
| causal indicator computation | Phase 3 |
| causal pattern detection | Phase 4; `structure_known_through` |
| breakout lifecycle causality | Phase 5 |
| completed-bar rule | historical detection sees `COMPLETE` bars only |
| survivorship / data eligibility | corpus classification and its prohibited-conclusion table |
| timeframe eligibility | per-detector and per-mandate |
| no state authorises a trade | `ADR-0025`: `EvidenceBundle` carries evidence and has no aggregate |

The builder is an **orchestration and definition layer**. If a strategy could
express something the engines refuse, the expression is the bug.

---

## 5. Lifecycle

```
DRAFT
  └─ validated against the registries: does everything it references exist?
VALIDATED
  └─ runs at all, on a corpus whose classification permits the claim
BACKTESTING
OUT_OF_SAMPLE_TESTING
PAPER_TRADING
  └─ on the live code path, simulated venue
ELIGIBLE_FOR_CAPITAL
LIVE
PAUSED
RETIRED
```

**The gates operate on strategy *versions*, not on strategies.** An edit drops
the new version back to `DRAFT`; it does not inherit the parent's evidence.

> **No strategy becomes LIVE because a backtest was profitable.**

That is the whole point of separating the states. Live trading additionally
remains behind the `ADR-0004` interlock and outside the roadmap.

### 5.1 What of this is enforced in code

`src/tradeit/strategy/lifecycle.py` carries the nine states and the transitions
between them. `StrategyConfig` was already the versioned, digest-identified
definition §2 and §3 describe; what did not exist was any notion of what a
version had *earned*, which is what makes the sentence above enforceable rather
than aspirational.

Three rules, each of which rejected a simpler shape:

**Promotion is one rung; demotion is any distance.** Evidence accrues one claim
at a time — passing out-of-sample says nothing about paper trading — so a
promotion that skipped a rung would assert a claim nobody tested. A demotion is
the opposite kind of fact: a contaminated backtest invalidates everything built
on it, so `LIVE → DRAFT` is a single legitimate move. Symmetric transitions
would have been simpler and would have left demotion too weak to express what a
discovered flaw means. `may_follow(BACKTESTING, LIVE)` refuses, and a test walks
every state to prove `LIVE` is reachable only from `ELIGIBLE_FOR_CAPITAL` or by
resuming a pause.

**Evidence is required to climb, and never inherited.** Every promotion carries
a citation; a demotion does not, because refusing to record a discovered flaw
until paperwork exists would leave a known-bad version at its old rung.
`StrategyVersion.edit` returns a **new version at `DRAFT` with empty history**,
since the parent's backtest was run on the parent's parameters. `has_reached`
is kept separate from `state` so a version demoted from paper trading stays
distinguishable from one that was never tested — a comparison that cannot tell
them apart treats a failure as a fresh start.

**`LIVE` is refused by default and cannot be argued into.** It requires a
`LiveAuthorisation`, which has no constructor that does not consult the
ADR-0004 interlock, and the interlock is *not* reimplemented — this module asks
`Settings` whether it passed. A test climbs every rung with a flawless record
and confirms the refusal still stands, because the interlock is not a function
of evidence.

`PAUSED` and `RETIRED` are deliberately off the ladder: they are not degrees of
earned evidence, and placing them on it would make "promotion to paused"
expressible. `mandate` is a field on the version rather than on a run, per §7 —
editing parameters does not turn a swing strategy into a day strategy.

### 5.3 `DRAFT → VALIDATED`, and what it caught

`src/tradeit/strategy/validate.py` performs §5's first gate — *does everything
it references exist?* — against three registries: `PatternType` for the
vocabulary, `DetectorRegistry` for what can actually be produced, and the
mandate for §7's rule that a strategy *may select within its mandate's eligible
hierarchy and not outside it*.

**The vocabulary is deliberately wider than the detector set.** Three storable
pattern types have no detector, because the enum must stay able to interpret an
old row whose family was later removed. Naming one in a strategy is still a
defect, and a distinct one: an unproducible pattern does not error, it silently
makes the strategy smaller and still reports a number.

**Decision timeframes and construction inputs are different questions.**
`enabled` and `intraday_enabled` are what the strategy decides on and the
mandate governs them; `base_timeframe` and `intraday_base` are what higher
timeframes are aggregated *from*. A swing strategy building 15-minute bars out
of 1-minute bars is not making a 1-minute decision, and 1m is outside the swing
mandate — so checking construction inputs against the mandate would forbid
building 15-minute bars correctly. They are checked only for existence and for
not being coarser than the group they build, **each paired with its own
group**: a first version pooled them and reported that a daily base could not
build 15-minute bars, which is neither its job nor true.

*What it found.* Run against the default `StrategyConfig`, all three mandates
refuse it, and every finding is real:

| mandate | why |
|---|---|
| Day | `timeframes.enabled` names `1w`, above its ceiling |
| Swing | `intraday_enabled` names `4h`, which §3.2 withholds |
| Retirement | `intraday_enabled` is entirely below its floor of `1d` |

That is the expected answer, not a bug to fix in the defaults: **there is no
mandate-scoped strategy configuration yet**, and the default is one generic
config while §7 says a version belongs to exactly one mandate. The defaults are
left alone deliberately — changing them is a strategy-parameter change and
needs a scoped proposition, not a commit.

---

### 5.2 The corpus condition on `VALIDATED`

The one condition §5 places between `VALIDATED` and the rungs above it is that
a version runs *on a corpus whose classification permits the claim*.
`src/tradeit/strategy/admissibility.py` applies it, and the line it draws is
**run versus believe**:

> A version **may enter `BACKTESTING`** on a survivor-biased corpus, because
> building and exercising the machinery is what that corpus is for. It **may
> not leave `BACKTESTING` upward**, because every rung above asserts that the
> results meant something.

That is a transcription rather than a decision. The threshold, the
classification and the sentence that lifts the rule are all already written:
*build the machinery on it; do not believe its numbers — the rule lifts when
the gate says something other than `SURVIVOR_BIASED`, and not before.* A test
puts a 41% CAGR in the citation and confirms the refusal is unchanged, because
the refusal is about the corpus and not about the number.

Two things left out deliberately. **No finer gradation:** nothing says
`PARTIALLY_SURVIVORSHIP_CORRECTED` permits paper trading but not capital, and
inventing that distinction would be a backtesting assumption needing a scoped
proposition rather than a commit — so all three non-biased classes are treated
alike until somebody decides otherwise on purpose. **No corpus binding:**
a version is not tied to the corpus it ran on, because persistence does not
exist yet; the caller passes the classification it measured, and passing a
stale one is the failure this cannot catch. `CorpusAdmissibility.measured_at`
records the age so the staleness is at least visible.

Demotions are exempt. A biased corpus must never block recording that something
went wrong.

---

*Not included, per §6 and the placement note:* no thresholds, no comparison
metric, no claim about **when** a version deserves promotion. A state machine
that decided that would be inventing exactly the thresholds §6 says are not
computed yet. It decides only what may follow what.

---

## 6. Comparison and learning — architecture only

The platform should eventually compare strategy versions across regime,
timeframe, mandate, universe, expectancy, hit rate, drawdown, risk-adjusted
return, turnover, exposure, robustness, parameter sensitivity and out-of-sample
degradation.

**None of those is computed now, and none is computed by this document.**

The governing intent: **learn which strategy versions work under which
conditions, rather than collapsing everything into one global "best strategy".**
A single champion is the shape overfitting takes when a platform is asked for a
winner — and it discards precisely the conditional structure that the mandate
separation exists to preserve.

---

## 7. The three mandates are not one strategy with three holding periods

From `MULTI_TIMEFRAME_MANDATES.md`, and binding on the builder:

| | Day | Swing | Retirement |
|---|---|---|---|
| context | `1d` / `1h` | `1w` / `1d` | `1mo` / `1w` |
| setup | `1h` / `15m` | `1d` / `1h` | `1w` / `1d` |
| trigger | `5m` / `1m` | `1h` / `15m` | `1d` |

They may legitimately differ in timeframe hierarchy, signals, risk model,
execution assumptions, data requirements and performance gates. **A 5-minute bull
flag for the day portfolio is not the same statistical object as a daily bull
flag for the swing portfolio, and the two must never share a population.**

These hierarchies are **current architectural defaults**, not eternal strategy
thresholds. A strategy may select *within* its mandate's eligible hierarchy; it
may not select outside it. `4h` remains unadopted pending the decision recorded
in `MULTI_TIMEFRAME_MANDATES.md` §3.2.

### Worked shapes, illustrative only

```
"Daily Bull Flag Momentum"        mandate: day
  context  1d trend bullish; 1h relative strength above threshold
  setup    15m bull flag
  trigger  5m breakout; 1m volume confirmation
  risk     0.5% portfolio risk per trade
  stop     pattern invalidation, or an ATR-based rule
  exit     partial target + trailing stop

"3-Month Breakout Swing"          mandate: swing
  context  weekly trend + daily market regime
  setup    daily VCP / flat base / bull flag
  trigger  1h or 15m confirmation
  horizon  days to ~3 months

"Long-Term Compounder"            mandate: retirement
  context  monthly / weekly
  filters  quality / growth / value conditions on point-in-time fundamentals
  setup    weekly / daily accumulation or breakout
  horizon  months to years
```

**These are illustrations of the definition surface, not proposed strategies, and
no threshold in them is a recommendation.**

---

## 8. Future dashboard modules

Documented so the data model is designed for them. **No UI is built.**

| module | shows |
|---|---|
| **Strategy Library** | every strategy: status, version, mandate, timeframes, and eventually its performance evidence |
| **Strategy Builder** | the declarative editor; composition from registries |
| **Strategy Detail** | one version's full definition and its `definition_hash` |
| **Backtest Results** | results bound to the exact version and corpus that produced them |
| **Paper Trading** | live-code-path track record per version |
| **Strategy Comparison** | side-by-side across the §6 dimensions |
| **Promotion / Capital Gate** | the `ELIGIBLE_FOR_CAPITAL` decision, with its evidence |
| **Live Strategy Monitor** | running versions and their current state |
| **Retired Strategy Archive** | retired versions, never deleted |

---

## 9. What will eventually need to change

Assessed against the code as it stands. **Nothing is changed by this document.**

| # | area | change |
|---|---|---|
| 1 | `strategy/config.py` | `TimeframeConfig` and friends are engine configuration, not a strategy definition. A strategy definition is a new, versioned, content-hashed object; the existing config becomes one of its resolved *outputs* |
| 2 | new tables | `strategies`, `strategy_versions`, `strategy_runs`, and the promotion/gate evidence. None exist |
| 3 | `scan_runs` | already carries `config_digest`, `data_snapshot_digest`, `code_version`. A `strategy_version_id` joins naturally and is the obvious binding point |
| 4 | `registry.SUPPORTED_TIMEFRAMES` | already the per-detector eligibility mechanism. The builder needs the same shape per *mandate*, one layer up |
| 5 | mandate tables | do not exist — noted in `MULTI_TIMEFRAME_MANDATES.md` §9 item 13 and unchanged |
| 6 | `EvidenceBundle` | must stay aggregate-free. The builder must not become the place a combined score is finally computed |
| 7 | backtesting (Phase 9) | must take a strategy *version* as input rather than a config object |
| 8 | date-grained schema | the same intraday limitation recorded in `MULTI_TIMEFRAME_MANDATES.md` §9 applies to any day-mandate strategy |

---

## 10. Placement

**A named platform gate after the Multi-Timeframe & Portfolio Mandate
Architecture gate, and before Phase 7.** Not a renumbering; Phases 3, 4 and 5 are
untouched, following the precedent of the empirical gate and the multi-timeframe
gate.

The reason for *before Phase 7* specifically: **Phase 7 is the first stage that
ranks anything.** Opportunity scoring produces a Standalone Opportunity Score, a
Portfolio Fit Score and a combination — and "fit" and "combination" are
meaningless without a strategy object to fit *to*. Ranking before strategies are
first-class would bake one implicit hard-coded strategy into the ranking layer,
which is exactly the outcome this milestone exists to prevent.

It must follow the multi-timeframe gate because a strategy's timeframe hierarchy
is part of its definition, and the mandate hierarchies must exist before a
definition can reference them.
