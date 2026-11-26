# ADR-0040: The daily-bar signal search is answered; effort moves to the platform

**Status:** accepted
**Date:** 2026-11-26
**Approved by:** the owner, 2026-11-26, accepting the recommendation made after
trial 145 (*"declare the signal-research question answered and spend effort on
the platform instead"*).
**Builds on** [`RESEARCH_FRONTIER_2026-11-21.md`](../RESEARCH_FRONTIER_2026-11-21.md),
whose option A — *stop spending trials inside the frame* — this makes a
standing decision rather than a recommendation.

## Context

145 trials are on the ledger. Inside the frame every one of them was measured
in — **US equities, daily bars, long only, cross-sectional rank, held 21 to 63
sessions** — fourteen research lines are closed, each by a stop rule written
before its result was known. One signal survives *as a signal* (§35,
`realized_vol_60`); nothing survives as a strategy. The floor any strategy
must beat is 7.74% a year, and it is thirteen names chosen by a tie-break, not
a skilled portfolio (`BREADTH_2026-11-24.md`).

The multiple-testing hurdle is |t| > 2.6587 and rises with every trial, so a
further trial inside an exhausted frame does not merely fail to help: it raises
the bar for the one later trial that might carry new information.

Trial 145 was the last proposal with a reason to be spent here, and it was a
*risk* hypothesis rather than a signal. It failed on a held-out decade and the
mechanism ran the other way from the one proposed (§59).

## Decision

1. **No trial is registered for a signal, indicator, pattern, combination or
   portfolio-construction rule inside that frame without new information** —
   data this corpus does not hold, a different market, or the short side, which
   is the record's own list of what remains open (§42, §44). "A parameter not
   yet tried" is not new information; every stop rule already says so.
2. **Effort moves to the platform**: the decision path running on the recorded
   book, paper operation that survives restarts, and the surfaces that show
   what each horizon holds, would do and refused. A system that can carry an
   edge honestly is worth building before the edge is found, because the
   readiness framework, the trial ledger and the risk controls are what make a
   later result trustworthy.
3. **The scoreboard, ledger and hurdle are unchanged.** Nothing is deleted or
   re-graded. The next trial, whenever it comes, is trial 146 against the
   hurdle as it then stands.

## What this does not decide

* **It does not authorise any purchase.** The new information most often named
  is intraday bars, and the owner declined that purchase on 2026-11-23. This
  ADR does not reopen it.
* **It does not authorise live trading**, and it does not lift the caveat every
  plan carries — *no signal in this system has a validated portfolio edge*. The
  platform will run the decision process on real data; that is not a
  recommendation to trade it.
* **It does not close the short side.** That line is open and is platform work
  before it is research: the sizer, `PositionState`, the stop ladder and the risk
  rules are long-only.

## Alternatives rejected

* **Keep searching at a slower rate.** Rejected: the hurdle rises per trial, not
  per month, and the stop rules already forbid the obvious next arms. A slower
  search inside the same frame is the same search.
* **Lower the bar for a "promising" result.** Rejected outright. Relaxing a
  criterion after seeing the data is how registered trials get laundered
  (`register-before-measuring`, and §59's handling of its own defective
  criterion 4).
