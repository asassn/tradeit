# ADR-0029: A live strategy comes down on a real shortfall or on losing money

**Status:** accepted
**Date:** 2026-10-23
**Supersedes:** nothing. **Amends:** nothing. Records the demotion threshold
authorised by the owner on 2026-10-23, as the scoped proposition the standing
rule on detector thresholds requires.

## Context

`PHASE_12_AUDIT_2026-10-18.md` lists four thresholds the readiness framework
will not invent, and calls `live_vs_expected` *"the single most consequential
number in this module and the one most obviously wrong to invent."* It carried
`ThresholdSource.UNAPPROVED`, which **blocks promotion** — so nothing could
reach live trading while it was owed, which was the correct state to be in.

The measurement already existed. `readiness/decay.py` computes the z of
realised against expected using the **live sample's own spread**, with
`confidence_z = 2.0` defining "more than sampling noise", and its docstring
said plainly: *"The bar on the strategy lives in `criteria.py` and is
unapproved."*

What made this hard is not the arithmetic. Three of the four owed thresholds
would be chosen *after* seeing the results they would judge, which is the
after-the-fact threshold the roadmap forbids. A demotion bar does not have that
problem in the same way — there are no live results yet to fit it to — but a
bare percentage would still be a number nobody could defend.

## Decision

**Demote on either test. Both are required because they fail in opposite
directions.**

1. **`live_matches_expectation`** — `live_vs_expected` must be at least
   **-2.0**. Source `DERIVED`: it is the same `confidence_z` that `decay.py`
   already uses, so the bar on the strategy and the function measuring it
   cannot disagree, and the z is computed against the live sample's own spread
   so the number comes from the data at assessment time rather than from a
   figure fitted to results already seen.

2. **`live_is_not_losing_money`** — `live_mean_trade_return` must be at least
   **0.0**. Source `WRITTEN`: zero is a natural boundary, the only number on
   this ladder needing no justification beyond its own meaning, which is
   exactly why it can be set now without the after-the-fact problem.

The z test alone is not enough: **a strategy with a wide enough spread can lose
money indefinitely without the shortfall ever reaching significance**, because
the standard error swallows it. The floor alone is not enough either: applied
without a minimum sample it would take a working strategy down on its first bad
week.

So `decay.demotion_evidence` emits **neither** metric below
`MIN_TRADES_FOR_A_VERDICT` trades. A metric it omits reports as `not measured`,
which `assess_demotion` treats as UNRESOLVED and therefore HOLD — surfaced in
the report rather than acted on. The gate is structural rather than remembered:
building the evidence mapping by hand at the call site would put it in whoever
remembered it, and `realised_mean` survives on an insufficient-evidence
assessment, so testing the *verdict* rather than the field is what makes it
hold.

## Alternatives rejected

**The statistical test alone.** Cleanest and fully traceable, and rejected for
the reason above: the slow bleed is a real failure mode and this misses it
entirely.

**A fixed percentage of expected return**, e.g. demote below 50%. Fires fast
and explains easily, and rejected as invented — chosen after seeing this
project's own results, and liable to fire on noise. It also scales wrongly: the
same percentage means different things at different trade frequencies.

**Leave it UNAPPROVED until a real candidate exists.** Costs nothing today
since no strategy is close to live, and rejected because the decision gets
*harder* once a candidate exists, not easier — at that point there are results
to fit the bar to, which is the thing the framework is built to prevent.
Choosing it now, with no live record in existence, is the only moment it can be
chosen cleanly.

## What this does not settle

**Three thresholds remain UNAPPROVED, deliberately.**
`out_of_sample_clears_hurdle`, `walk_forward_consistency` and
`out_of_sample_degradation` all judge measurements that **already exist**, so
choosing them now would fit the bar to the data. They are not missing work;
they are **pre-registration obligations**, owed before the next out-of-sample
test rather than today, and they correctly continue to block promotion until
then. Treating them as a to-do list to be cleared would defeat them.

`out_of_sample_clears_hurdle` has a further wrinkle recorded in the audit: the
ledger hurdle is `small_sample_hurdle(trials, blocks)`, computed per test, so
encoding any constant would contradict the ledger. Whether readiness asks for
the hurdle at assessment time or a standing minimum is a design question still
open; this ADR does not answer it.

## Consequences

**Demotion is now possible, which the roadmap requires of the ladder.** The
readiness framework's own rule is that `RESEARCH → ROBUST_BACKTEST → PAPER →
LIMITED_LIVE → SCALED_LIVE` must permit demotion, and until now the criterion
that would carry it was unapproved.

**Promotion to live is still blocked**, by the three thresholds above and by
the interlock in `config.py`. Nothing here moves a strategy toward live money;
it makes the way back down defined.

**A demotion can be wrong roughly one time in forty** on the z test, by
construction at 2 sigma. That is the trade-off the audit named, and it is the
right side of it: a demoted strategy can be re-promoted on evidence, while a
strategy left live on a vanished edge spends real money.

## Tests

In `tests/unit/test_readiness_decay.py`, each shown failing against the
behaviour it replaces:

- `test_a_statistically_real_shortfall_demotes` — the z test fires.
- `test_a_slow_bleed_demotes_on_the_zero_floor_alone` — a negative mean with a
  z *inside* the band demotes on the floor only, which is the case the z test
  cannot catch. Fails when the floor criterion is removed.
- `test_a_handful_of_losing_trades_does_not_demote` — four losing trades supply
  no measurement and hold. Fails when the minimum-trade gate is dropped.
- `test_a_healthy_live_record_holds` — both criteria are satisfiable, so the
  ladder has a top rung.
- `test_both_demotion_thresholds_are_approved_and_cited` — an UNAPPROVED source
  here would mean this ADR was recorded and never wired in.
