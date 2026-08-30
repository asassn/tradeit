# ADR-0023: Confirmation profiles describe evidence, not risk — and there are three paths

**Status:** Accepted · **Date:** 2026-08-09 · **Phase:** 5

## Context

Two decisions that turned out to be one.

**How much evidence before an event is "confirmed"?** There is no single right
answer, and the brief says so: item 29 asks for CONSERVATIVE, BALANCED and
AGGRESSIVE profiles and adds "these names should describe evidence requirements,
NOT portfolio risk" and "do NOT associate these profiles with position sizing.
That belongs to Phase 8."

**What counts as confirmation at all?** Most implementations answer this with
one rule: a strong close, real volume, and follow-through on the next bar. That
is a real pattern and it is one of three. A modest break that quietly holds above
the level for a week is the classic base breakout. A break, an orderly pullback
and a recovery is the textbook retest. A system implementing only the first will
report the other two as unconfirmed forever, and its confirmation rate will be a
statement about its own gate rather than about the market.

The two decisions are one because a profile that demands more evidence on one
path is not the same object as a profile that demands evidence on more paths.

## Decision

**Three paths, tried in a configured order, first match wins.**

    PATH A — momentum     strong close, real volume, immediate follow-through
    PATH B — retest       break, controlled pullback, level holds, recovery
    PATH C — acceptance   modest break, several closes above, volatility contracts

First-match rather than best-match. There is no meaningful ranking between
"confirmed on momentum" and "confirmed on retest" — they are different routes to
the same state — and inventing a preference would be a trading opinion Phase 5
has no basis for. The order lives in `ProfileConfig.paths`, so a later phase can
state a preference explicitly if it finds one.

**Which path produced a confirmation is recorded on the event.** "How do
confirmations actually arrive?" is then answerable from the data. At n=200 on
the synthetic corpus it arrives very differently by scenario: the clean breakout
confirms on momentum 100% of the time, the weak one on acceptance, and
`successful_retest` splits between retest and acceptance.

**Profiles are evidence policies and carry no risk field.** `ProfileConfig` has
`required_closes`, `follow_through_window`, `min_follow_through`,
`min_relative_volume`, `min_breakout_quality`, `acceptance_closes`,
`acceptance_max_range`, `min_retest_quality`, `paths` and
`min_evidence_coverage`. It has no size, no risk fraction, and no stop, and a
test asserts the absence rather than trusting the reader.

`min_evidence_coverage` is the one that looks like a trading threshold and is
not. Below it the profile declines to confirm because the confirmation score is
computed from too little evidence to mean what it says — the same reasoning as
`DetectorContract.minimum_evidence_coverage` in Phase 4. It is a statement about
the score's validity, not about whether the trade is good.

**A blocked path reports what blocked it.** `PathDecision.blockers` carries
lines like `momentum: follow-through 52 < 55`, and they surface in the event's
contradicting evidence. "Awaiting evidence" is not an answer a reviewer can act
on.

## Consequences

**Good.** The profiles do measurably different work. On the synthetic corpus at
n=200, CONSERVATIVE confirms 67% of controlled scenarios and 25% of adversarial
ones; AGGRESSIVE confirms 86% and 45%. That gap is the thing the names claim,
and it is published rather than asserted.

The momentum path's window requirement is a **floor on elapsed time**, not on
the score: a profile asking for three bars of follow-through cannot confirm on
day one however good the first bar looked, because the evidence it wants has not
had time to exist. Reporting that as "not confirmed, awaiting evidence" rather
than as a low score keeps "no evidence yet" distinguishable from "evidence
against".

**Costs.** Three paths mean three ways to be wrong, and the acceptance path is
the loosest: a slow drift above an arbitrary level satisfies it, which is
visible in the `low_volume_drift` and `no_prior_resistance` adversarial rows
(96% and 92% confirmed). The mitigation is not in this layer — see the known
weaknesses in `BREAKOUT_VALIDATION.md`.

Events under different profiles are not comparable. The profile name is stored
on the event and in the `breakout_events.profile` column, because a state that
does not say which policy produced it cannot be interpreted.

**Rejected.** *One configurable threshold set with no paths.* Collapses the
three routes into whichever one the thresholds happen to describe.

*Scoring the paths and taking the best.* Requires a ranking Phase 5 cannot
justify, and would make the recorded path a function of the scoring rather than
of what happened.
