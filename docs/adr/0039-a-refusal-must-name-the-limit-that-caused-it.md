# ADR-0039: A refusal must name the limit that caused it

**Status:** accepted
**Date:** 2026-11-19
**Approved by:** the owner, 2026-11-18, choosing *"what each portfolio should
do today"* — holdings, would-buy and would-sell with size and reason, and
**refused with which veto** — as what the product opens on.
**Builds on** [ADR-0035](0035-the-dashboard-shows-what-the-record-holds.md),
which established that the dashboard shows what the record holds. This ADR
says what must be in the record for a decision to be checkable at all.

## Context

The architecture separates three scores — the setup, the **portfolio fit**,
and the number that may authorise — and declares five absolute vetoes:
`already_at_full_size`, `position_limit_reached`, `sector_limit_reached`,
`heat_limit_exceeded`, `insufficient_cash`.

### A claim this ADR made and withdraws

The first version said *"none of it had ever run"*. **That is wrong**, and the
evidence was already in this repository.
[ADR-0027](0027-score-3-ranks-capital.md) authorised wiring score 3 into the
allocation seat on 2026-10-18, `scripts/backtest_fit_ranked.py` runs it, and
`SIGNAL_SCOREBOARD.md` §57 measured it on 195 names over 2010-2019: 800 trades
at 44.40% against `DiversityAwareRanker`'s 802 at 48.31%. §55 and §56 had
already measured the fit on 15,095 candidates over 2,359 slates, with the
ranking changing on 1,703 of the 2,327 slates that held real competition.

I measured the stored **decision record**, found `DiversityAwareRanker` and no
import of `tradeit.opportunity`, and concluded about the **system**. The record
was produced by one script; the backtest path was a different one, and it had
the ranker wired. That is the same "never answer by accident" failure this
project keeps cataloguing — the expected answer arrived and was not an answer to
the question asked.

### What was actually missing

Narrower, and still worth an ADR:

| | |
|---|---|
| `OpportunityFitRanker` in the allocation seat | **ran** — ADR-0027, §57 |
| The fit's refusals surviving the call | **no.** `rank()` computed a `PortfolioFit` and its `VetoReason`s per candidate and returned neither |
| Any refusal in any record naming a `VetoReason` | **no** — measured below |
| A `Portfolio`, `Position`, `RiskSnapshot` or `PortfolioSnapshot` row | **no writer anywhere in `src/tradeit`** |
| A command, route or screen showing a portfolio's plan | **no** |

The single run that produced a stored decision record ranked with
`DiversityAwareRanker(correlation_weight=0.0, sector_penalty_per_holding=0.0,
held_sectors={})` — pure score order, portfolio awareness switched off — and
never imported `tradeit.opportunity`.

Measured in `decision_record_2003_2019.sqlite` on 2026-11-19: **804 individual
refusals** across 553 journal entries, against 1,956 recorded scores.

| reason | refusals |
|---|---|
| `sizer` | 504 |
| `max_positions` | 274 |
| `sector_exposure` | 26 |

**Not one of them is a `VetoReason`.** Those three names belong to the sizer
and the risk engine; `sector_exposure` looks like the fit's
`sector_limit_reached` and is a different control reached at a different stage.
And **all 804 record `score = 0.0`** — a placeholder written in place of the
candidate's own score, so the record cannot say what any refusal cost.

*(An earlier draft of this ADR and the commit messages of
`6ba88bc`, `cd5b684` and `e698cdb` said "1,956 refusals, all `sizer`". 1,956 is
the count of **scores**, and 504 of 804 refusals say `sizer`. The commits are
not rewritten; the correction is recorded here, which is the direction this
project keeps history in.)*

The cause was one line. `OpportunityFitRanker.rank` built the
`PortfolioContext`, called `score_fit`, called `combine` — and returned only
`AllocationCandidate`, dropping the fit and its vetoes at the `continue`.
`final.py` had already written down the intent this violated: a total is
computed for inadmissible candidates *"because 'forbidden but would have
ranked first' is worth seeing, and discarding it hides what a limit cost."*

**Why this matters beyond tidiness.** A screen or a record showing only what a
system *would buy* looks identical whether its limits are working or absent.
There is no observation that distinguishes "no candidate hit a limit" from
"the limits are not wired", and a risk control nobody can see fire is one
nobody will notice has stopped firing.

## Decision

**A refusal is a first-class result, and it carries four things:**

1. **Which candidate**, by instrument and by the ticker a person reads.
2. **Every limit that bound it**, not the first. A candidate refused by two
   limits reporting one would make the second look slack when it is also
   binding — which is how a limit gets relaxed on the strength of a
   measurement that never applied to it.
3. **Which component refused**, because a *ranker veto* before sizing and a
   *risk-engine rejection* after it are different mechanisms. They stay in
   separate fields. Collapsing them loses which control acted.
4. **The standing it would have had**, taken on the ranking total **before**
   admissibility is applied. This is the only thing that makes a cap's cost
   legible: *"forbidden, and would have ranked first"* is a fact about the
   limit, and it is unrecoverable once the candidate has been dropped.

**A control that cannot fire is reported as absent, and the report is measured
from the wiring rather than written down.** Four exist today: `PyramidRule`,
`CorrelationRule`, `DrawdownHaltRule` and `DailyLossRule` are never installed;
the trailing stop engages only on an ATR that no caller supplies; the
round-trip cost cap binds only when a cost model is passed. A fixed list would
keep asserting an absence after somebody fixed it, which is a false claim about
a risk control — the one place a false claim costs money.

**`VetoReason.INSUFFICIENT_CASH` is unreachable by construction and stays.**
**This was established by `SIGNAL_SCOREBOARD.md` §56 on 2026-10-18, not here** —
same reasoning and the same `sizing.py:197` citation, recorded there as "a proof
of impossibility, not a near miss". `BindingConstraint.CASH` caps quantity at
`cash / entry_price` and then floors it, so the sizer's notional can equal
available cash but never exceed it, while the veto needs `required_cash > cash`.

What this ADR adds is a **test that pins the unreachability against the cap**
rather than leaving it as prose: removing the cap fails six tests, which is the
moment somebody needs to know the veto came alive. The veto is not deleted,
because a different sizer, a multi-leg order, or a cash reservation taken
between sizing and fit would each make it reachable.

§56 also measured the other four firing comfortably on a universe selected for
sector coverage — 6,662 `heat_limit_exceeded`, 1,903 `sector_limit_reached`,
1,875 `already_at_full_size`, 335 `position_limit_reached` — so "four fire, the
fifth cannot" is §56's finding and is cited, not rediscovered.

**A horizon whose primary corpus does not exist declines to be scored.** Day's
primary corpus is `intraday-01` ([ADR-0030](0030-day-is-deferred-not-deleted.md)).
Daily bars are in Day's eligible set, so a Daily slate would be admissible and
would produce a plausible plan — but Day's hierarchy is context `D1/H1`, setup
`H1/M15`, trigger `M5/M1`, so its setup and trigger layers would both be
absent. The result would be a swing plan wearing a day-trading label, which is
the collapse of three horizons into one that the architecture exists to
prevent.

### Rejected alternatives

**Reporting the first veto only.** Simpler, and the one measured example —
`TXN` on 2015-11-27 — is bound by *both* the sector cap and the heat limit. A
reader told only about the sector cap would reasonably conclude that freeing
sector room would admit the trade. It would not.

**A `fit_vetoes` table.** `observability/record.py` already records that a
`risk_verdicts` table was proposed and **rejected**, routing gate decisions to
`journal_entries` instead. Reversing a written decision is the owner's, not
Claude's, so refusals are reported and not yet stored. This is a real gap and
it is stated rather than worked around.

**Carrying `VetoReason` into `tradeit.portfolio`.** `tradeit.opportunity`
already imports `portfolio.base`; importing the enum back would close the loop.
So `RankerRefusal` carries plain strings, and the cycle relays a refusal from
any ranker without knowing that ranker's vocabulary.

**Persisting the portfolio and reading holdings from it.** There is nothing to
read: `portfolios` and `positions` have no writer. Reading from empty tables
would produce a confident plan for an empty portfolio, indistinguishable from a
portfolio that genuinely holds nothing — the failure `tradeit status` had to be
fixed for. Holdings are **declared by the operator** in a versionable file, and
the screen says so rather than implying a persistence the system does not have.

**Reconstructing the portfolio from the decision record.** Defensible, and
deferred. That record is of a backtest whose ranker had the portfolio awareness
switched off, so its positions are the output of the decision process this work
replaces. Planning today's trades against them would be reasoning from the
thing being corrected.

## Consequences

`tradeit plan` and `/plan` run the three-score path end to end for one horizon
and one session. Measured against `research-01` on 2015-11-27 over fourteen
tickers warmed from 2014-01-02, in 0.56 s:

```
REFUSED   TXN  sector_limit_reached, heat_limit_exceeded
               would have ranked 1 · fit 0.3455, total 0.1423, sector Manufacturing
```

That line had never been produced by this system.

**What this does not establish.** Nothing here is evidence of profitability.
No signal in this repository has a validated portfolio edge — the best measured
one, `realized_vol_60`, passes a registered out-of-sample test as a *signal*
and lost to a random portfolio in four of four samples as a *portfolio*. The
slate therefore ranks on something unvalidated, and every plan carries that
statement beside its numbers rather than in a document. The live-trading
interlock of [ADR-0004](0004-live-trading-safety-interlock.md) is untouched.

**Four controls remain unarmed.** Installing them changes what a backtest
measures, which CLAUDE.md places behind an explicit scoped proposition. They
are reported absent until that proposition is put and answered.

### A finding from sweeping the path, rather than running it once

Running one session proves the code executes. Walking 1,763 sessions of
2003-2009 over 36 names found two things one session could not.

**A ticker resolved by its earliest alias interval is an invented identity.**
`resolve_universe` did that — deterministic, and wrong. `DIS` resolved to the
Disney security whose prices begin in **2018**, so it printed on 0 of 1,763
sessions. Resolution is **per date**
(`RESEARCH_01_DATA_DICTIONARY` §0.2), and the per-date query returns **more
than one row for 6.18% of tickers**, measured — so zero rows and two rows are
both `UNRESOLVED`, with different reasons worth reporting separately. That is
now §0.2a of the dictionary, and the first test of the old behaviour had
asserted the wrong thing as though it were a safeguard.

**A claim about the engine that was wrong, and the correction.** The first
version of this ADR said `EventDrivenEngine._decide` skips a whole session when
any held instrument lacks a print, citing 1,007 skipped sessions of 1,763.
**That was my sweep script's defect, not the engine's.** `_decide` skips only
when `_context` returns `None`, and `_context` falls back to
`state.last_prices`, which `_RunState` populates as bars arrive — so once a
holding has printed once the fallback always succeeds. The engine's own
docstring states the reasoning and it is right: *"A security that did not print
today is carried at its last known price rather than skipped. That is not a
guess — it is the last thing anybody knew, and being unchanged from yesterday
it cannot trigger a stop that yesterday did not."*

The real finding is about **this package**, which had the defect the engine
avoided: `plan_declared` refused an entire horizon when a declared holding had
no print on the as-of date. It now carries the holding at its last known close
and **reports the staleness per position**, because a mark two sessions old is
a fact the reader should see rather than one the plan should hide — and only
refuses when a holding has never printed at all, which is an identity problem
rather than a quiet session.

This is the "never answer by accident" failure in the direction that matters
least and embarrasses most: a symptom was observed in a measuring instrument
and attributed to the thing being measured.
