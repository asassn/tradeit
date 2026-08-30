# ADR-0025: Breakout state is not trade eligibility

**Status:** Accepted · **Date:** 2026-08-09 · **Phase:** 5 / empirical gate

## Context

Phase 5 produces a lifecycle state per breakout attempt. `CONFIRMED` is the
state everyone will reach for, and its meaning is narrow:

> The configured confirmation pathway has been satisfied.

Phase 5's own characterisation established that this is not a theoretical
concern. On the adversarial corpus at n=200, `low_volume_drift` — price drifting
above an arbitrary level on collapsing volume — reaches `CONFIRMED` **96%** of
the time, and `no_prior_resistance` **92%**. Their median breakout quality is 49
and 55 against 93 for a clean break, and confidence on a single-touch unattached
level falls from 83 to 47. **The evidence separates them cleanly; the state does
not separate them at all.**

That is not a defect in the engine. Handed a level, the engine faithfully
reports that price closed above it; deciding whether the level is a *structure*
is Phase 4's job and whether it is still live is the monitor's filter. But it
means a downstream stage that branches on `state == CONFIRMED` will treat a
textbook base breakout and a directionless drift identically, and will do so
while believing it consulted the analysis.

The failure mode is not carelessness. It is convenience: the object that has all
the numbers is the natural place to put a helper that combines them, and one
`is_tradable` property four stages upstream is enough. The Phase 2 draft of this
codebase had exactly that property, which is why it was deleted rather than left
alongside the real implementation.

## Decision

**No breakout lifecycle state, by itself, authorises a trade, an opportunity
recommendation, a position or a portfolio allocation.** Downstream consumers
evaluate the full evidence object.

Three mechanisms, because a rule stated only in prose is a rule that decays.

**1. `EvidenceBundle` is the supported way to consume a breakout.** It carries
every field a downstream stage needs across both layers — pattern quality,
pattern coverage, pattern state, boundary confidence, breakout quality,
confirmation score, breakout coverage, breakout confidence, breakout state,
relative strength, sector, regime — plus explicit `None` slots for the two
Phase 5 cannot fill: `fundamental_score` (Phase 6) and `portfolio_fit_score`
(Phase 7). Present as slots rather than absent, so a consumer reaching for them
gets a named gap and a report can count how many decisions were made without
them.

`missing_for_decision()` returns those gaps with the stage that would supply
each. A consumer may decide without them; what it may not do is decide without
knowing what it lacked.

**The bundle has no aggregate.** No total, no rank, no verdict, no
`is_tradable`. A convenience method that summed the fields would become the
decision by default, and asserting the absence is cheaper than re-litigating it
later — `FORBIDDEN_DECISION_TERMS` is walked over the dataclass fields, the
public attributes and the enum members by test.

**2. Boundary provenance is recorded.** `BoundaryKind` tags where a level came
from:

| Kind | Means |
| --- | --- |
| `STRUCTURAL_PATTERN_BOUNDARY` | derived by a Phase 4 detector from causally-confirmed geometry |
| `MANUAL_BOUNDARY` | supplied by a human — a research note, a chart annotation |
| `EXPERIMENTAL_BOUNDARY` | produced by an experimental or unreleased method |
| `OTHER` | unknown origin, including imported levels |

The tag is about *provenance*, not quality. `OTHER` exists so that "we do not
know where this came from" is expressible — a row that cannot state its
provenance must not be able to claim a good one. The kind is stored on
`breakout_events.boundary_kind` with its own index, so a query can separate the
populations without inferring it from a score.

`manual_boundary()` supports research use and **refuses to be tagged
structural**. That refusal is the load-bearing part: a keyword argument is
exactly how a research level would otherwise enter the production population
indistinguishably.

**3. The production monitor accepts only structural boundaries.**
`PRODUCTION_PATTERN_STATES` is the architectural floor — `MATURE`,
`NEAR_BREAKOUT`, `BROKEN_OUT_UNCONFIRMED` — and `check_monitor_eligibility`
runs at monitor construction. An operator may monitor *fewer* states; nobody may
add `FORMING`, whose boundary is still resolving, because once such events are
in the dataset every rate computed from it describes the monitor rather than the
market.

Eligibility requires **both** the structural tag and a present pattern key.
Trusting the tag alone would let one bad construction call defeat the whole
mechanism.

## Consequences

**Good.** "Which population is this row in?" is answerable by query. A Phase 7
stage that wants to reject low-quality confirmed events has every number it
needs, and a Phase 7 stage that ignores them is visibly ignoring them rather
than unaware. The research path stays open — asking "what would the engine say
about this level?" is a legitimate question — without contaminating the
production dataset.

**Costs.** Consuming a breakout is now more verbose: a caller must assemble a
bundle and supply context rather than reading `event.state`. That verbosity is
the point, and it will be irritating at exactly the moment someone is in a hurry.

The bundle duplicates values that already live on the event and the pattern. A
stale bundle is possible if one is built and held across sessions; it carries
`as_of_session` so the staleness is detectable, and it is a snapshot by design.

`OTHER` is the default `BoundaryKind` on the dataclass and the database column,
which means a boundary constructed directly without a kind is research-grade
until someone says otherwise. That is the safe direction, and it does mean a
future caller who forgets the argument gets a boundary the monitor will refuse
rather than one it silently trusts.

**Rejected.** *A quality floor inside Phase 5 that suppresses low-quality
confirmations.* This is the tempting fix and it is wrong twice: it would put a
Phase 7 threshold in a Phase 5 module, and it would make `CONFIRMED` mean "good"
— collapsing the layers this architecture separated on purpose, and destroying
the dataset Phase 7 needs in order to learn what a low-quality confirmation is
worth.

*Renaming `CONFIRMED` to something less inviting.* Cosmetic. The next name would
acquire the same meaning within a phase.
