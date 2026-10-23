# ADR-0030: The Day horizon is deferred, and deliberately not deleted

**Status:** accepted
**Date:** 2026-10-23
**Supersedes:** nothing. **Amends:** the *Multi-Timeframe & Portfolio Mandate
Architecture* gate's "Still owed" list in `ROADMAP.md`. **No phase is
renumbered.** Records a product-scope decision authorised by the owner on
2026-10-23.

## Context

The Day mandate's `primary_corpus` is `intraday-01`, and
`CORPUS_REGISTRY.md` records it as **NOT YET BUILT**: 1-minute base, regular
trading hours, roughly 1,500 names over ten years, about 22 GB columnar, and
**expected to be survivorship-biased** because intraday history for
long-delisted securities is rarely available at any price.

Building it needs paid intraday data. The owner's decision of 2026-10-20 was
that **no further money goes on data**, and the forward-capture programme
exists because of it. So Day was unreachable, and would stay unreachable, while
still appearing on the roadmap as work somebody would eventually do.

The system's behaviour was already honest. `api/app.py` declines to score Day
and says why: *"there is no `intraday-01` corpus, and scoring the Day mandate on
Daily bars would collapse it into Swing — the one thing the three-horizon
architecture exists to prevent."* Nothing was producing a wrong Day answer. The
problem was the schedule, not the code.

**And the roadmap contained a contradiction.** The gate's entry says
`intraday-01` is still owed and that the gate **"must complete before Phase
7"**. Phase 7 completed and was audited on 2026-10-17 without it. That clause
had already been violated, quietly, and this ADR resolves it rather than
leaving it to be rediscovered.

## Decision

**Day is deferred: `intraday-01` is not a deliverable on the path to paper
trading, and no phase is blocked on it.**

**The machinery stays.** `_DAY` in `portfolio/mandate.py`, its timeframe
hierarchy, its eligible set, the refusals, and the tests that pin them are all
kept. Three reasons:

1. *A great stock and a great trade for this portfolio right now are different
   questions* and *a ticker may reach different conclusions at different
   horizons*. Day's absence is a **data gap, not a design change**, and the
   separation it participates in is the architecture's organising claim.
2. It costs nothing to keep. The spec is data; the refusal is already written
   and already explains itself.
3. Re-adding a horizon later means rebuilding the separation and its tests from
   scratch, and the separation is the expensive part — not the spec.

## Alternatives rejected

**Remove the Day mandate entirely.** Simplest to reason about, and rejected: it
collapses the three-horizon design, and the thing that would have to be rebuilt
is the separation rather than the twenty lines of spec.

**Keep Day as a deliverable and buy the intraday data.** Rejected by the owner;
it reverses the no-further-data-spend decision. If it is ever revisited it
comes back as a costed proposal, not as a quiet line item.

**Leave it as it was.** Rejected because it is the state that produced the
contradiction above: a roadmap implying work that cannot be done, and a gate
nominally blocking a phase that had already shipped.

## Consequences

**A new risk, which is why this ADR has a test.** A horizon nobody is working
on is a horizon somebody later "enables" the cheap way — by scoring it on the
daily bars that already exist. That collapses Day into Swing, and **it would
look like progress.** `test_day_survives_its_deferral_and_still_refuses_the_eod_corpus`
pins both halves: Day is still declared with a real hierarchy admitting minute
bars, and its primary corpus is still not `research-01`. Repointing it to make
Day scoreable fails, with the reason.

**Swing's trigger layer loses its intraday option too.** `intraday-01` also
serves Swing's trigger timeframes (`H1`, `M15`). Swing's *primary* corpus is
`research-01`, so Swing remains fully workable on daily bars, but its triggers
run at the coarser end of its declared hierarchy until an intraday corpus
exists. That is a real reduction in what Swing can express, and it is recorded
here rather than discovered later.

**Nothing about the Day mandate is claimed to work.** Deferral is not a verdict
on the strategy space; it is a statement about data availability. If intraday
data becomes free or affordable, this ADR is the thing to revisit, and the
survivorship posture declared in `CORPUS_REGISTRY.md` still applies when it is.
