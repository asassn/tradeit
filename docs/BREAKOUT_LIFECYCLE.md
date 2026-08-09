# Breakout lifecycle

Thirteen states, and a constrained machine between them. The constraint is the
point: a state history that can go anywhere is not evidence of anything.

## The machine

    NOT_APPROACHING ──→ APPROACHING ──→ TESTING_RESISTANCE ──→ INTRADAY_BREAK
           ▲                 │                  │                    │
           └─────────────────┘                  │                    │
                             └──────────────────┴────────────────────┘
                                                │
                                          CLOSED_ABOVE
                                                │
                                    ┌───────────┴────────────┐
                                    │                        │
                          CONFIRMATION_PENDING ←──────→ RETEST_PENDING
                                    │                        │
                                    │                  RETEST_HOLDING
                                    │                        │
                                    │                  RETEST_CONFIRMED
                                    │                        │
                                    └────────→ CONFIRMED ←────┘

    any pre-close state ──→ REJECTED ──→ (EXPIRED | FAILED_BREAKOUT)
    any state            ──→ EXPIRED         (terminal)
    any post-close state ──→ FAILED_BREAKOUT (terminal)

Self-edges are legal everywhere non-terminal. A state that persists for six
sessions is six observations of the same state, and forbidding the self-edge
would force the history to omit them.

## The states

| State | Means |
| --- | --- |
| `NOT_APPROACHING` | price is nowhere near the boundary |
| `APPROACHING` | inside the approach zone — informational, and explicitly not a breakout |
| `TESTING_RESISTANCE` | trading at the boundary without having pushed through |
| `INTRADAY_BREAK` | the high cleared the threshold; the close did not |
| `CLOSED_ABOVE` | a completed bar closed above the breakout threshold |
| `CONFIRMATION_PENDING` | closed above; the profile wants more evidence |
| `CONFIRMED` | the profile's requirements were met by one of its paths |
| `RETEST_PENDING` | pulled back materially, has not reached the level |
| `RETEST_HOLDING` | traded into the zone and has not broken it |
| `RETEST_CONFIRMED` | after reaching the zone, closed back above the level |
| `REJECTED` | went through and was pushed back; the attempt is over |
| `FAILED_BREAKOUT` | a breakout occurred and the structure gave it back — terminal |
| `EXPIRED` | out of time without resolving — terminal, and not a failure |

None of these names implies an action. The vocabulary is observational, and a
state called `BUY` or `ENTRY_READY` would be a Phase 8 concept smuggled into a
Phase 5 enum — asserted by test, not left to review.

## Four decisions worth defending

### REJECTED closes the attempt, not the setup

`REJECTED` has no path back to the pre-breakout states. The structural setup may
well survive and be taken later — that is a *new attempt*, opened by the monitor
with `attempt_number + 1` and its own event id, so attempts stay countable.
Full reasoning: [ADR-0020](adr/0020-rejection-resolves-the-attempt.md).

### CONFIRMED is not terminal

A confirmed breakout can subsequently fail. Recording that as anything other
than `FAILED_BREAKOUT` would produce a confirmed population that had erased its
own failures — the same inversion the Phase 4 pattern lifecycle guards against.
`CONFIRMED` can also enter `RETEST_PENDING`: confirmation by momentum followed by
an orderly pullback is ordinary behaviour.

### FAILED_BREAKOUT and EXPIRED are both absorbing, and they are different

Expiry means nothing happened in time. Failure means something happened and it
went the wrong way. Merging them would inflate every failure rate with
non-events, which is precisely the statistic a later phase would most like to
trust.

The terminal reason distinguishes them further. A confirmed event monitored to
the end of its window ends `EXPIRED` with reason `RESOLVED` — the engine simply
has nothing further to say about it, which is neither a success nor a failure.

### A pullback that never reached the level was not a retest

`RETEST_PENDING → CONFIRMATION_PENDING` is a legal edge. A pullback that turns
back up before reaching the zone is *abandoned* rather than credited: recording
it as a successful retest would give the boundary a test it never received, and
since shallow pullbacks are far more common than deep ones, that single shortcut
would make the retest path the dominant route to confirmation for reasons that
have nothing to do with the market.

The three retest states mean three different things and the engine keeps them
apart: `PENDING` (pulled back, has not reached the level), `HOLDING` (in the
zone, not broken), `CONFIRMED` (recovered above the level after reaching it).
The session on which a retest resolves records `RETEST_CONFIRMED` and nothing
else; the upgrade to `CONFIRMED` happens on the next session. Collapsing them
would erase the state from every history, and "how many breakouts were confirmed
by a retest that visibly held?" would have no row to count.

## Failure

Failures split into two groups by whether the retest engine is running.

**Unconditional**, checked every session:

| Condition | Default |
| --- | --- |
| close below the level by more than `decisive_close_atr` | 1.2 ATR |
| adverse excursion from the breakout close beyond `max_adverse_atr` | 2.5 ATR |

Both thresholds sit above the retest tolerances, and
`BreakoutEngineConfig` **validates the ordering** rather than leaving two numbers
to agree by luck: `max_undercut_atr` must be below `decisive_close_atr`, or an
ordinary retest would fail the event it is testing.

Adverse excursion is measured from the breakout close over bars **after** the
breakout bar. Seeding it with the breakout bar's own low — which is well below
its close on any wide-range breakout — would charge the event for the very bar
that made it, and every decisive breakout would fail itself on the next session
whatever price did. This was a real defect during Phase 5 development; it is
now a named test.

**Structural**, checked only when no retest is running:

| Condition | Default |
| --- | --- |
| consecutive closes below the zone floor | 2 |
| trading below the breakout bar's low *and* closing below the zone | — |

Both describe price settling back inside the pattern. Inside a retest that is
precisely the behaviour under adjudication, and the retest engine — which is
volatility-aware and counts sessions below the level — is the thing qualified to
adjudicate it.

**Pattern invalidation** resolves by where the event had got to. Past
`CLOSED_ABOVE`, a real breakout occurred and the structure subsequently broke:
`FAILED_BREAKOUT`, reason `PATTERN_INVALIDATED`. Before it, no breakout ever
happened and calling it a failed breakout would be a lie about what was
observed: `EXPIRED`, same reason. Item 38 permits either; this states which and
why, rather than inventing a fourteenth state meaning "expired, but a bit
failed". The event stays attached to its pattern throughout.

## Expiration

| Clock | Default | Applies to |
| --- | --- | --- |
| `max_sessions_approaching` | 40 | pre-breakout states |
| `max_sessions_pending` | 15 | `CONFIRMATION_PENDING` |
| `max_sessions_confirmed` | 30 | `CONFIRMED` — resolved, not failed |
| `max_sessions_retesting` | 25 | the retest states |
| `pattern_length_multiple` | 1.0 | a cap relative to the pattern's own length |

The length-relative cap exists so a three-week flag and a nine-month cup are not
given the same clock.

Expiration never overrides a failure or a confirmation reached on the same
session. Only a session that produced nothing decisive can time out.

## Transitions are recorded, not inferred

Every state change appends a `BreakoutTransition` carrying the session, both
states, the reason, both scores, the coverage behind them and the confirmation
path where applicable. An event confirmed at 88 on 45% coverage was confirmed on
very little, and a history that stores only the number cannot say so.

The observation log is append-only in the strong sense: the unique constraint on
`(event_id, session_date)` makes a second write for a session a conflict rather
than an overwrite, and the repository skips rather than replaces. Item 15's
example — `CLOSED_ABOVE` at T0, `CONFIRMATION_PENDING` at T+1, `CONFIRMED` at
T+3, `FAILED_BREAKOUT` at T+7 — is replayed literally in
`test_breakout_persistence.py`, and every earlier row survives the failure.
