# ADR-0027: Score 3 ranks capital, and the standalone score must declare its scale

**Status:** accepted
**Date:** 2026-10-18
**Supersedes:** nothing. **Amends:** nothing. Records a change to the decision
path authorised by the owner on 2026-10-18, as the scoped proposition the
standing rule on trading logic requires.

## Context

Phase 7 separates three scores — standalone setup quality, portfolio fit, and
the combination that ranks capital — and the separation is the project's
organising claim: *a great stock and a great trade for this portfolio right now
are different questions, and only the second may authorise a trade.*

The audit of 2026-10-17 found that `tradeit.opportunity` was **never imported by
any script**. It was built, tested, and measured beside the decision. Every
portfolio result this project has published —
`SIGNAL_SCOREBOARD.md` §46, §48, the walk-forward, all of them — was ranked by
`DiversityAwareRanker` constructed with `correlation_weight=0.0` and
`sector_penalty_per_holding=0.0`, by one uncommented line in
`baselines.build_engine`. Diversification was off, the fit score played no part
in choosing a trade, and nothing said so.

§55 and §56 then measured the fit score on corpus data: 15,095 candidates over
2,359 slates, 8,545 refused by the fit, and the ranking changed on 1,703 of the
2,327 slates that held real competition. The machinery discriminates. It simply
was not connected.

Leaving it disconnected through Phase 11 would mean paper trading exercises —
and Phase 12 certifies — a decision path that was never the intended one.

## Decision

**Score 3 ranks capital.** `OpportunityFitRanker` implements `AllocationRanker`
and may be installed wherever `DiversityAwareRanker` is, with two deliberate
properties:

1. **A fit veto refuses, it does not demote.** A sector cap is not a preference
   a better setup can outbid. The refusals overlap `MostRestrictiveEngine`,
   which remains the authority; the fit refuses earlier, before the
   authoritative sizing.
2. **A standalone total outside [0, 1] raises.** `combine` is linear and
   `OpportunityScore.total` declares no range, so blending a raw spread
   fraction (~0.02) with a 0–1 fit score at a declared 0.6 weight produces a
   number the fit dominates completely, while `standalone_weight` goes on
   describing a mixture that is not happening. The caller normalises against a
   **declared** range and says what range it used.

**`PortfolioCycle` is unchanged.** `AllocationRanker` already is the seat for
"see the whole basket and decide", and it already returns candidates in
allocation order with permission to return fewer.

**`build_engine` keeps `correlation_weight=0.0` and now carries the comment
explaining why.** Every published result stays reproducible by constructing the
ranker it was produced with.

## Alternatives rejected

**Teach `_plan_entries` about fit directly.** Rejected: it puts a portfolio
policy inside the mechanism that applies it, and makes the previous behaviour
unreachable — so no published result could be reproduced.

**Normalise the standalone score inside the ranker.** Rejected, and this is the
subtle one. Within-slate min-max normalisation looks reasonable and is worse
than it looks: on a two-candidate slate it forces the better name to exactly
1.0 and the worse to exactly 0.0 whatever the real gap, so the blend weight
silently varies with slate size. Refusing pushes the scale decision to where
the scale is understood.

**Make the fit score able to override any setup advantage.** Rejected:
`standalone_weight` is a declared blend, and an input that could always win
would make it a lie. A test asserts the boundary from both sides — at the
default 0.6 weight a 0.24 fit gap overturns a 0.10 setup gap and not a 0.30
one.

**Switch it on as the default everywhere.** Rejected, for now. The ranker is
installed per run. Making it the default would silently change what every
future comparison means against the archive of results produced without it.

## Consequences

**The decision path now differs, measurably.** Same universe, window, sizing,
stops, costs, fills and risk rules; only the ranker changed:

| | trades | return | Sharpe |
|---|---|---|---|
| `DiversityAwareRanker` (as before) | 802 | 48.31% | 0.28 |
| `OpportunityFitRanker` | 800 | 44.40% | 0.25 |

**Two trades differ and the return falls by 3.9 points.** Both halves of that
matter:

* **Admissions barely moved, as predicted before the run.** The fit's refusals
  overlap the risk engine's, so the change is in the *order* trades are taken.
  Predicted in the script's docstring specifically so it could not later be
  presented as a discovery.
* **It performed worse in sample, and that is recorded rather than explained
  away.** Diversification declines concentrated bets, and in a single
  in-sample window the most concentrated bets often were the best ones. **One
  universe, one window, one in-sample run, no pre-registration, no ledger
  spend: this is evidence the wiring is real and no evidence at all about
  whether score 3 earns anything.** It is not a trial and must not be cited as
  one.

**Every portfolio result in the archive describes an unconstrained book.** They
are not retracted and cannot have been inflated by the absence of a
diversification constraint — a limit refuses or shrinks positions, it does not
add return. Re-running them under the constraint is now possible and costs no
ledger budget.

**`insufficient_cash` remains unreachable** through `RiskBasedSizer`, which
caps quantity at `cash / entry_price`, so a notional taken from it equals
available cash at most. Recorded in §56; unchanged by this decision.
