# ADR-0022: The breakout boundary is snapshotted when the event opens

**Status:** Accepted · **Date:** 2026-08-09 · **Phase:** 5

## Context

A breakout is measured against a level. The level comes from Phase 4, which
legitimately *refines* its boundaries as new bars arrive: a horizontal
resistance drawn through three swing highs gains a fourth touch, the fitted line
moves a few basis points, and the pattern's `confidence` rises. That refinement
is correct behaviour for the pattern layer — it is not a leak, because each
refinement uses only bars that had already printed.

It becomes a leak one layer up. If a breakout event reads the pattern's *current*
boundary each session, then on the session after the breakout it is judged
against a level that partly reflects the breakout it is judging. The brief names
this in item 1 — "do not rediscover or optimize historical resistance using
post-breakout bars" — and in item 12: a retest "should be evaluated against the
ORIGINAL causal breakout level, not a level redefined after the event".

The retest case is the one where the damage is easiest to see. A support line
refitted to include the pullback low passes through the pullback low. Every
retest then holds, and a "retest success rate" computed from that data is
approximately 100% by construction — a number that would look like a discovery.

## Decision

**`BreakoutBoundary` is frozen when the event opens and is never replaced.**

The snapshot carries everything a later measurement needs, so nothing has to be
re-derived: the nominal level, its slope and anchor date, the touch count, the
pattern layer's confidence in it, the derived tolerance, and — critically — the
**ATR current at that moment**. Every ATR-relative figure on the event uses
`atr_at_open`, so a later change in volatility cannot restate how far through
the level the original bar closed.

The boundary is a field on the frozen `BreakoutEvent` dataclass, and
`BreakoutRepository._advance` deliberately does not update the boundary columns
on a re-save. The monitor rebuilds a boundary from the pattern every session for
the purpose of deciding whether to *open* an event; an event already open keeps
the one it has.

**Three prices, not one.** The snapshot exposes `nominal`, a tolerance `zone`
around it, and a `threshold` at the top of the zone. A qualifying close must
exceed the threshold, not the nominal level. Penny-exact resistance is a fiction
— a level through highs at 147.28, 147.33 and 147.30 is "about 147.30" — and an
engine that treats 147.31 as a breakout and 147.29 as a test is measuring its own
rounding. The tolerance is the larger of a percentage floor and an ATR multiple,
widened for a low-confidence boundary and capped; all three inputs are knowable
when the event opens, and none is a price outcome.

## Consequences

**Good.** "Resistance remains tied to the causal pattern boundary" is testable
directly: the boundary payload is byte-identical at every point in a replay, and
scaling every post-breakout bar by 15% does not move `nominal`. A stored event
can be re-read years later and re-judged against exactly the level it was judged
against, because the level is in the row rather than behind a foreign key.

**Costs.** A genuinely better boundary discovered after the event opens is not
adopted. If the pattern layer adds a fifth touch and tightens the level, the
open event keeps the coarser one. This is the intended trade — the alternative
admits post-breakout information — but it means an event opened against an early,
loose boundary carries that looseness for its whole life. The monitor mitigates
it only in the sense that a *new* attempt gets a fresh snapshot.

Storage grows: eleven boundary columns per event rather than one foreign key.
At the projected volumes in `PHASE_05.md` this is not the dominant term.

**Rejected.** *Reading the pattern's live boundary each session.* The leak
described above, and undetectable after the fact from the stored data.

*Storing only a pattern reference and re-deriving the level on read.* Same leak,
deferred to whoever runs the query — and the derivation would use whatever
detector version is installed at query time rather than the one that produced
the event.
