# Retest methodology

Price breaks a level, comes back to it, and either holds or does not. Two rules
dominate, and both exist because retest analysis is usually wrong when it is
done casually.

## Rule one: the original causal level, always

> A retest is evaluated against the original causal breakout level, never a level
> redefined after the event. — item 12

Refitting the level through post-breakout bars is not a small optimisation. A
support line fitted to include the pullback low **passes through the pullback
low**, so every retest holds, and the "retest success rate" computed from it is
approximately 100% by construction. It would look like a discovery.

The boundary this module reads is the one frozen when the event opened
([ADR-0022](adr/0022-the-boundary-is-snapshotted-at-open.md)), and there is no
code path that can replace it. The test that pins this scales every post-breakout
bar by 15% and asserts `boundary.nominal` does not move.

## Rule two: not every undercut is a failure

Price that dips below a level it cleared four days ago and closes back above it
has **tested** the level, not broken it. What separates the two is how far, for
how long, and on what volume — and all three have to be volatility-aware, because
a fixed percentage calls the same behaviour a hold on a quiet name and a failure
on a fast one.

`max_undercut_atr` is configured strictly below `FailureConfig.decisive_close_atr`,
and `BreakoutEngineConfig` **validates the ordering** rather than leaving two
numbers to agree by luck. An ordinary retest cannot trip the failure rule it is
supposed to survive.

The engine goes further: while a retest is running, the generic structural
failure rules are **suspended**. Deciding whether a return to the level is a test
or a break is exactly what the retest engine is for; a generic "two closes back
inside" rule evaluated first would pre-empt that judgement and fail every retest
that dipped for two sessions — which is most of them. This was a real defect
during Phase 5 development: `successful_retest` was failing on a 0.2-ATR,
two-session dip that the retest tolerance explicitly permits.

## When a retest starts

A pullback counts when the close is at least `retest_trigger_atr` (0.5 ATR)
below the **breakout close**. Measured from the breakout close rather than from
the running high: using the running high would make every advance followed by a
normal day look like a pullback, which is how a retest engine ends up
"detecting" retests during uptrends.

## The three states

| State | Means |
| --- | --- |
| `RETEST_PENDING` | pulled back materially; has not reached the level |
| `RETEST_HOLDING` | traded into the tolerance zone; has not broken it |
| `RETEST_CONFIRMED` | after reaching the zone, closed back above the level |

A pullback that turns back up before ever reaching the zone is **abandoned**, and
the event returns to `CONFIRMATION_PENDING`. Recording it as a successful retest
would credit the level with holding a test it never received; since shallow
pullbacks are far more common than deep ones, that single shortcut would make the
retest path the dominant route to confirmation for reasons unrelated to the
market.

Recovery closes are counted **from the low onward**. A close above the level
before the low is part of the decline, not a recovery.

## What is stored

`RetestRecord` carries everything item 12 asks for: the start session, the low
and its date, the maximum undercut in ATR, the duration, sessions spent below the
level, mean volume relative to the breakout bar, mean range relative to the ATR
at breakout, recovery closes, the recovery session, and the quality score.

The record **survives its own resolution**. A breakout that held a 0.5-ATR
undercut is a different object from one that never pulled back, and the
difference must remain in the record. `retest_active` is a separate flag, because
the record outlives the episode it describes.

## RETEST_QUALITY_SCORE

| Term | Weight | Full at | Zero at |
| --- | --- | --- | --- |
| undercut depth (inverted) | 0.30 | 0 ATR | 0.6 ATR (floor 10) |
| volume contraction | 0.25 | 0.45× breakout | 1.5× (floor 5) |
| range contraction | 0.15 | 0.6× ATR | 2.0× (floor 5) |
| recovery closes | 0.30 | 1 close | 0 |

The weighting states what the engine believes a good retest looks like: it did
not go far below the level, on contracting volume, in a contracting range, and
price closed back above.

Two details:

**The undercut term has a floor rather than falling to zero.** A retest that dug
1.5 ATR below the level and still recovered is a poor retest, not a non-existent
one, and zeroing the term would let the recovery weight carry a structure that
clearly broke.

**Spending more than `max_sessions_below` sessions below the level halves the
undercut term.** A shallow but persistent drift below a level is a break that
never produces a dramatic bar — the failure mode a purely depth-based rule misses
entirely.

## Failure and expiry

The retest has broken the level if **either** condition holds:

- maximum undercut beyond `max_undercut_atr` (0.6 ATR);
- more sessions below the level than `max_sessions_below` (3).

Two independent conditions rather than one composite, because they describe two
different ways a level gives way.

A retest that has churned around the level for more than `max_sessions_retesting`
(25) sessions has become a new consolidation. Continuing to call it a retest of a
breakout from last month would attribute its eventual resolution to an event it
has stopped being about, so the event expires with that reason stated.

## The retest confirmation path

`PATH B` in the confirmation profiles requires an **actual recovery**, not merely
a retest that has not yet failed. An event sitting at the level on day three of a
pullback is `RETEST_HOLDING` — informative, and not the same as confirmed.

The session on which a retest resolves records `RETEST_CONFIRMED` and nothing
else; the upgrade to `CONFIRMED` happens on the next session if the profile is
satisfied, and back to `CONFIRMATION_PENDING` if it is not. Collapsing the
resolution into `CONFIRMED` on the same bar would erase `RETEST_CONFIRMED` from
every history, and "how many breakouts were confirmed by a retest that visibly
held?" would have no row to count.

## What the corpus shows

At n=200 on the synthetic corpus (see
[`BREAKOUT_VALIDATION.md`](BREAKOUT_VALIDATION.md)):

- `successful_retest` — 98% produce a breakout, 77% enter `RETEST_HOLDING`, 60%
  reach `CONFIRMED`, 54% end `FAILED_BREAKOUT`. The two overlap because an event
  can confirm on the retest and subsequently fail, which is the behaviour
  `CONFIRMED → FAILED_BREAKOUT` exists to record.
- `failed_retest` — 98% fail, 32% having confirmed first.
- Confirmations on `successful_retest` split between the retest path and the
  acceptance path, which is the expected shape: a shallow retest that resolves
  quickly also satisfies acceptance.

None of these is a trading success rate.
