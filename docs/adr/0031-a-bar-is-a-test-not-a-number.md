# ADR-0031: A promotion bar is a test against something written, not a number somebody chose

**Status:** accepted
**Date:** 2026-10-23
**Supersedes:** nothing. **Amends:** nothing. **Companion to**
[ADR-0029](0029-demotion-needs-two-tests.md), which settled demotion; this
settles two of the promotion thresholds and states the method both used.

## Context

`PHASE_12_AUDIT_2026-10-18.md` listed four thresholds the readiness framework
*"will not invent"*, and the reason was the same for three of them: each judges
a measurement **that already exists**, so choosing a value after seeing it is
the after-the-fact threshold the roadmap forbids. A threshold carrying
`ThresholdSource.UNAPPROVED` blocks promotion, which was the correct state to be
in and not a bug.

The obvious next move — ask the owner for three numbers — would have been
wrong. Picking a number *is* the forbidden act, whoever picks it. The owner's
risk appetite settles a bar like "may a live strategy lose money"; it cannot
settle "is 60% of walk-forward windows enough", because that question has a
statistical answer and a guess would masquerade as a decision.

## Decision

**Express each bar as a test against a value already written down. Then the
number in the measurement's own units comes from the data at assessment time,
and nothing is invented.**

Applied to two of the four:

| criterion | metric | bar | parent |
|---|---|---|---|
| `out_of_sample_clears_hurdle` | `out_of_sample_t_over_hurdle` | **1.0** | the ledger's `small_sample_hurdle(trials, blocks)` |
| `walk_forward_consistency` | `walk_forward_consistency_z` | **2.0** | `decay.py`'s `confidence_z`, per ADR-0029 |

Both are `DERIVED`, never `WRITTEN`: a derived bar is only as good as its
parent, and saying so is the point of the grade.

**The hurdle, as a ratio.** The ledger hurdle is computed per test, so encoding
any constant would contradict the ledger. 1.0 means "clears the hurdle the
ledger computed for this test". The decisive argument is directional: **the
hurdle rises with the trial count** — 2.6563 at 144 — so a fixed bar becomes
weaker than the ledger as trials accumulate and would eventually pass a result
the ledger rejects. A constant that looked conservative when written is the
failure mode, not a careless constant.

**Consistency, as a z against chance.** The positive-window share is
standardised against what chance would produce and compared to the same 2.0 the
decay monitor uses, so the framework holds one definition of *"more than
sampling noise"*. `z = (2k − n) / √n`, so the default 2003–2019 span's 14
windows require 11 positive and seven windows would require all seven. **The
bar rises as evidence thins** — the right direction, and the opposite of what a
fixed fraction does. `MIN_WINDOWS_FOR_A_VERDICT = 4`, because a perfect record
at *n* windows scores only √n and below four even a flawless record cannot reach
2.0.

## The assumption this rests on, and it is not free

**Consistency's null is a coin flip**: a strategy with no edge is taken to
produce positive windows half the time. That is the natural null and is **not
fitted to our data**, which is the property the roadmap's rule protects — but it
is an assumption, and a sufficiently skewed return distribution would break it.

Rejected: estimating the null share from this project's own windows. That is
precisely the fitting the criterion exists to prevent, and it would have
produced a defensible-looking number that was contaminated.

The hurdle ratio carries no equivalent assumption; its parent is a computation
this repository already performs.

## Where the method does not reach

**`out_of_sample_degradation` stays UNAPPROVED and this ADR does not settle
it.** There is nothing to standardise against: a no-edge strategy has no
characteristic degradation, because it has no edge to lose. And
`out_of_sample_clears_hurdle` already requires the held-out result to clear the
ledger *after* whatever degradation occurred — so what is missing is a bar on
**fragility**, not on profitability, and fragility has no written parent here.

It is discharged by **pre-registering a limit before the next out-of-sample
test**, which is a decision taken when that test is designed. That is genuinely
the owner's, and it has a trigger rather than a date.

## Consequences

**Three of the four thresholds are settled without a number being chosen**, and
the one that remains is the one that actually needed a person.

**A new threshold should be attempted this way first.** The question to ask of
any proposed bar is *what already-written value is this a test against?* If
there is an answer, the bar is derivable; if there is not, it is a judgement and
belongs to the owner with its assumption stated.

**Promotion to live is not nearer.** `out_of_sample_degradation` still blocks,
Phase 12 still owes live-system monitoring, and the ADR-0004 interlock stands.
What changed is that two bars which blocked for want of a decision now block
only for want of a measurement — a different and better complaint.

**These were taken as design decisions rather than brought to the owner**,
because neither chooses a value. The coin-flip null is the one place that claim
is arguable, which is why it is declared here rather than left in a docstring,
and why the threshold is `DERIVED`: if the assumption is rejected, the bar goes
with it.

## Tests

- `test_the_ratio_is_stricter_than_any_standing_minimum_would_have_been` — a t
  of 2.3 clears a plausible minimum of 2.0 and the hurdle at 3 trials, and
  **fails it at 144**, measured against the real function.
- `test_the_consistency_bar_rises_as_the_windows_thin` — 11 of 14 passes, 10 of
  14 fails, 7 of 7 passes, 6 of 7 fails.
- `test_a_coin_flip_record_does_not_pass_at_any_size` — across five sizes, so a
  larger sample cannot launder a 50% record.
- `test_too_few_windows_is_refused_rather_than_scored`.

A 60% fixed fraction substituted for the consistency z fails two of these; a
`hurdle_evidence` that omits the division fails two of the hurdle's.
