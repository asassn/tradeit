"""The breakout state machine.

Phase 4's lifecycle answered "what is this structure doing?". This one answers
"what is price doing relative to that structure's boundary?" — and, like the
pattern machine, it is constrained rather than free-form, because a state
history that can go anywhere is not evidence of anything.

**The machine.**

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

Four decisions in that diagram are not obvious, and each one is a place where a
looser machine would quietly destroy a measurement.

**REJECTED closes the attempt, not the setup.** The brief says a rejection "may
remain non-terminal if the structural setup survives and another attempt is
possible", and there are two ways to honour that. Letting a REJECTED event walk
back to APPROACHING and try again keeps one event alive across many attempts —
and then "how often does a breakout attempt fail?" has no denominator, because
attempts are not countable. So REJECTED is a *resolved* state with no path back
to the pre-breakout states: the setup survives, and the next qualifying close
mints attempt N+1 under a **new breakout event id**. See ADR-0020.

**CONFIRMED is not terminal.** A confirmed breakout can subsequently fail, and
recording that as anything other than FAILED_BREAKOUT would produce a confirmed
population that had erased its own failures — the same inversion the pattern
lifecycle guards against. It can also enter RETEST_PENDING: confirmation by the
momentum path followed by an orderly pullback is ordinary behaviour.

**FAILED_BREAKOUT and EXPIRED are both absorbing, and they are different.**
Expiration means nothing happened in time; failure means something happened and
it went the wrong way. Merging them would inflate every failure rate with
non-events, which is precisely the statistic a later phase would most like to
trust.

**Pattern invalidation resolves by where the event had got to.** If the event
had reached CLOSED_ABOVE or beyond, a real breakout occurred and the structure
subsequently broke: FAILED_BREAKOUT, reason ``PATTERN_INVALIDATED``. If it never
got past TESTING_RESISTANCE, no breakout ever happened and calling it a failed
breakout would be a lie about what was observed: EXPIRED, same reason. The
brief allows either; this states which and why, rather than inventing a
fourteenth state that means "expired, but a bit failed".
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from tradeit.errors import ConfigError


class BreakoutState(StrEnum):
    """Where a breakout attempt stands.

    Ordered by progression. None of these names implies an action: the vocabulary
    is deliberately observational, and a state called ``BUY`` or ``ENTRY_READY``
    would be a Phase 8 concept smuggled into a Phase 5 enum.
    """

    #: Price is nowhere near the boundary. The nil state; an event that exists
    #: only because it once approached can return here.
    NOT_APPROACHING = "not_approaching"
    #: Within the configured approach zone. Informational, and explicitly not a
    #: breakout — this state exists so that "we were watching" is recorded.
    APPROACHING = "approaching"
    #: Price is trading at the boundary within the testing distance, without
    #: having pushed through it.
    TESTING_RESISTANCE = "testing_resistance"
    #: The bar's high cleared the threshold; its close did not. A distinct fact
    #: from a close above, and the one most often mistaken for it.
    INTRADAY_BREAK = "intraday_break"
    #: A completed bar closed above the breakout threshold.
    CLOSED_ABOVE = "closed_above"
    #: Closed above, and the active profile wants more evidence before it will
    #: call the event confirmed.
    CONFIRMATION_PENDING = "confirmation_pending"
    #: The profile's evidence requirements were met by one of its paths.
    CONFIRMED = "confirmed"
    #: Price has pulled back toward the former resistance after the breakout.
    RETEST_PENDING = "retest_pending"
    #: The pullback reached the level and has not broken it.
    RETEST_HOLDING = "retest_holding"
    #: The level held and price closed back above it.
    RETEST_CONFIRMED = "retest_confirmed"
    #: Price went through the boundary and was pushed back within the bar or
    #: the next. The attempt is over; the setup may survive.
    REJECTED = "rejected"
    #: A breakout occurred and the structure gave it back. Terminal.
    FAILED_BREAKOUT = "failed_breakout"
    #: Out of time without resolving. Terminal, and not a failure.
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in (BreakoutState.FAILED_BREAKOUT, BreakoutState.EXPIRED)

    @property
    def is_resolved(self) -> bool:
        """Terminal, or REJECTED — the attempt has nothing further to do.

        REJECTED is separated from the terminal pair because the *pattern* is
        still live and a later attempt is expected; the monitor keeps the
        boundary under observation, it simply does so under a new event.
        """
        return self.is_terminal or self is BreakoutState.REJECTED

    @property
    def has_broken_out(self) -> bool:
        """Whether a completed bar has ever closed above the threshold.

        The dividing line the whole machine turns on: before it, nothing has
        happened that could fail; after it, expiration is no longer the honest
        way to end.
        """
        return self in (
            BreakoutState.CLOSED_ABOVE,
            BreakoutState.CONFIRMATION_PENDING,
            BreakoutState.CONFIRMED,
            BreakoutState.RETEST_PENDING,
            BreakoutState.RETEST_HOLDING,
            BreakoutState.RETEST_CONFIRMED,
            BreakoutState.FAILED_BREAKOUT,
        )

    @property
    def is_pre_breakout(self) -> bool:
        return self in (
            BreakoutState.NOT_APPROACHING,
            BreakoutState.APPROACHING,
            BreakoutState.TESTING_RESISTANCE,
            BreakoutState.INTRADAY_BREAK,
        )

    @property
    def is_retesting(self) -> bool:
        return self in (
            BreakoutState.RETEST_PENDING,
            BreakoutState.RETEST_HOLDING,
            BreakoutState.RETEST_CONFIRMED,
        )


class TransitionReason(StrEnum):
    """Why the event moved. Recorded so a history reads as a narrative."""

    OPENED = "opened"
    #: No change in circumstances worth a new state.
    HELD = "held"
    ENTERED_APPROACH = "entered_approach"
    LEFT_APPROACH = "left_approach"
    TOUCHED_BOUNDARY = "touched_boundary"
    PENETRATED_INTRADAY = "penetrated_intraday"
    QUALIFYING_CLOSE = "qualifying_close"
    #: The profile's evidence requirements are not yet met.
    AWAITING_EVIDENCE = "awaiting_evidence"
    CONFIRMED_BY_PATH = "confirmed_by_path"
    PULLBACK_STARTED = "pullback_started"
    RETEST_REACHED_LEVEL = "retest_reached_level"
    RETEST_RECOVERED = "retest_recovered"
    REJECTED_AT_BOUNDARY = "rejected_at_boundary"
    CLOSED_BACK_INSIDE = "closed_back_inside"
    BROKE_BREAKOUT_LOW = "broke_breakout_low"
    ADVERSE_EXCURSION = "adverse_excursion"
    REPEATED_REJECTION = "repeated_rejection"
    PATTERN_INVALIDATED = "pattern_invalidated"
    TIMED_OUT = "timed_out"
    #: A confirmed event monitored to the end of its window. Not an outcome —
    #: the engine simply has nothing further to say about it.
    RESOLVED = "resolved"


#: Reasons that end an event as FAILED_BREAKOUT. Listed rather than inferred so
#: the failure taxonomy is reviewable in one place.
FAILURE_REASONS: frozenset[TransitionReason] = frozenset(
    {
        TransitionReason.CLOSED_BACK_INSIDE,
        TransitionReason.BROKE_BREAKOUT_LOW,
        TransitionReason.ADVERSE_EXCURSION,
        TransitionReason.REPEATED_REJECTION,
        TransitionReason.PATTERN_INVALIDATED,
    }
)


#: The shared machine. Self-edges are legal everywhere non-terminal: a state
#: that persists for six sessions is six observations of the same state, and
#: forbidding the self-edge would force the history to omit them.
LEGAL_TRANSITIONS: Mapping[BreakoutState, frozenset[BreakoutState]] = {
    # An event opens here and is classified on its first evaluation, which may
    # land anywhere: the monitor can notice a boundary on the session price
    # takes it. Forbidding the direct edges would force a fictitious approach
    # observation on the day before the one the engine actually saw.
    BreakoutState.NOT_APPROACHING: frozenset(
        {
            BreakoutState.NOT_APPROACHING,
            BreakoutState.APPROACHING,
            BreakoutState.TESTING_RESISTANCE,
            BreakoutState.INTRADAY_BREAK,
            BreakoutState.CLOSED_ABOVE,
            BreakoutState.REJECTED,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.APPROACHING: frozenset(
        {
            BreakoutState.NOT_APPROACHING,
            BreakoutState.APPROACHING,
            BreakoutState.TESTING_RESISTANCE,
            BreakoutState.INTRADAY_BREAK,
            BreakoutState.CLOSED_ABOVE,
            BreakoutState.REJECTED,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.TESTING_RESISTANCE: frozenset(
        {
            BreakoutState.APPROACHING,
            BreakoutState.NOT_APPROACHING,
            BreakoutState.TESTING_RESISTANCE,
            BreakoutState.INTRADAY_BREAK,
            BreakoutState.CLOSED_ABOVE,
            BreakoutState.REJECTED,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.INTRADAY_BREAK: frozenset(
        {
            BreakoutState.INTRADAY_BREAK,
            BreakoutState.TESTING_RESISTANCE,
            BreakoutState.APPROACHING,
            # Price can leave the approach zone from any pre-breakout state; a
            # spike through the level followed by a slide is one bar's move.
            BreakoutState.NOT_APPROACHING,
            BreakoutState.CLOSED_ABOVE,
            BreakoutState.REJECTED,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.CLOSED_ABOVE: frozenset(
        {
            BreakoutState.CLOSED_ABOVE,
            BreakoutState.CONFIRMATION_PENDING,
            BreakoutState.CONFIRMED,
            BreakoutState.RETEST_PENDING,
            # A pullback can reach the zone on the session it starts, so the
            # intermediate PENDING observation would be a bar that never existed.
            BreakoutState.RETEST_HOLDING,
            BreakoutState.FAILED_BREAKOUT,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.CONFIRMATION_PENDING: frozenset(
        {
            BreakoutState.CONFIRMATION_PENDING,
            BreakoutState.CONFIRMED,
            BreakoutState.RETEST_PENDING,
            BreakoutState.RETEST_HOLDING,
            BreakoutState.FAILED_BREAKOUT,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.CONFIRMED: frozenset(
        {
            BreakoutState.CONFIRMED,
            # A confirmed breakout that pulls back is retesting, not failing.
            BreakoutState.RETEST_PENDING,
            BreakoutState.RETEST_HOLDING,
            BreakoutState.FAILED_BREAKOUT,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.RETEST_PENDING: frozenset(
        {
            BreakoutState.RETEST_PENDING,
            BreakoutState.RETEST_HOLDING,
            # A pullback that turns back up before ever reaching the level was
            # not a retest. Forcing it through the retest states would let a
            # level that was never tested be recorded as having held.
            BreakoutState.CONFIRMATION_PENDING,
            BreakoutState.CONFIRMED,
            BreakoutState.FAILED_BREAKOUT,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.RETEST_HOLDING: frozenset(
        {
            BreakoutState.RETEST_HOLDING,
            BreakoutState.RETEST_CONFIRMED,
            BreakoutState.FAILED_BREAKOUT,
            BreakoutState.EXPIRED,
        }
    ),
    BreakoutState.RETEST_CONFIRMED: frozenset(
        {
            BreakoutState.RETEST_CONFIRMED,
            BreakoutState.CONFIRMED,
            BreakoutState.RETEST_PENDING,
            BreakoutState.RETEST_HOLDING,
            # The retest resolved but the profile is not satisfied by it. Back
            # to accumulating evidence rather than stranded in a state that
            # sounds confirmatory and is not.
            BreakoutState.CONFIRMATION_PENDING,
            BreakoutState.FAILED_BREAKOUT,
            BreakoutState.EXPIRED,
        }
    ),
    # Resolved: the attempt is over. No path back to the pre-breakout states,
    # because the next attempt is a new event with its own identity.
    BreakoutState.REJECTED: frozenset(
        {
            BreakoutState.REJECTED,
            BreakoutState.FAILED_BREAKOUT,
            BreakoutState.EXPIRED,
        }
    ),
    # Absorbing.
    BreakoutState.FAILED_BREAKOUT: frozenset({BreakoutState.FAILED_BREAKOUT}),
    BreakoutState.EXPIRED: frozenset({BreakoutState.EXPIRED}),
}


class IllegalBreakoutTransitionError(ConfigError):
    """An attempt to move an event along an edge that does not exist."""


def is_legal(from_state: BreakoutState, to_state: BreakoutState) -> bool:
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


def check_transition(
    from_state: BreakoutState,
    to_state: BreakoutState,
    *,
    event_id: str = "",
) -> None:
    """Raise unless the transition is legal, naming the remedy.

    The remedy is the non-obvious part. "My rejected event closed above the
    level again" is not a missing edge — it is a second attempt, and the answer
    is a new event id carrying ``attempt_number + 1``.
    """
    if is_legal(from_state, to_state):
        return

    where = f" for {event_id}" if event_id else ""
    if from_state.is_terminal:
        raise IllegalBreakoutTransitionError(
            f"{from_state} -> {to_state}{where}: terminal breakout states are "
            "absorbing. A failed or expired attempt must not become a live one "
            "under the same identity, or every false-breakout rate would be "
            "computed from a population that erased its own failures."
        )
    if from_state is BreakoutState.REJECTED:
        raise IllegalBreakoutTransitionError(
            f"{from_state} -> {to_state}{where}: a rejected attempt is resolved. "
            "The structural setup may well survive and be taken later — that is a "
            "new attempt, and it needs a new breakout event carrying "
            "attempt_number + 1, so that attempts remain countable."
        )
    raise IllegalBreakoutTransitionError(
        f"{from_state} -> {to_state}{where} is not a legal breakout transition"
    )


def terminal_state_for_invalidation(current: BreakoutState) -> BreakoutState:
    """Where pattern invalidation sends an event.

    See the module docstring: the split is on whether a breakout ever happened,
    not on how bad the invalidation looks.
    """
    return BreakoutState.FAILED_BREAKOUT if current.has_broken_out else BreakoutState.EXPIRED


@dataclass(frozen=True, slots=True)
class BreakoutTransition:
    """One entry in an event's history. Append-only.

    Carries both scores and the coverage behind them, for the same reason the
    pattern history does: an event confirmed at 88 on 45% coverage was confirmed
    on very little, and a history that stores only the number cannot say so.
    """

    session_date: dt.date
    from_state: BreakoutState | None
    to_state: BreakoutState
    reason: TransitionReason
    breakout_quality: float
    confirmation_score: float
    evidence_coverage: float = 100.0
    #: Which confirmation path produced a CONFIRMED state, where applicable.
    path: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.from_state is not None:
            check_transition(self.from_state, self.to_state)
        for label, value in (
            ("breakout_quality", self.breakout_quality),
            ("confirmation_score", self.confirmation_score),
            ("evidence_coverage", self.evidence_coverage),
        ):
            if not 0.0 <= value <= 100.0:
                raise ConfigError(f"{label} {value} outside [0, 100]")

    @property
    def is_failure(self) -> bool:
        return self.to_state is BreakoutState.FAILED_BREAKOUT

    def __str__(self) -> str:
        origin = str(self.from_state) if self.from_state else "-"
        path = f", via {self.path}" if self.path else ""
        return (
            f"{self.session_date.isoformat()}  {origin} -> {self.to_state} "
            f"({self.reason}, quality {self.breakout_quality:.0f}, "
            f"confirmation {self.confirmation_score:.0f}, "
            f"coverage {self.evidence_coverage:.0f}{path})"
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "session_date": self.session_date.isoformat(),
            "from_state": str(self.from_state) if self.from_state else None,
            "to_state": str(self.to_state),
            "reason": str(self.reason),
            "breakout_quality": round(self.breakout_quality, 6),
            "confirmation_score": round(self.confirmation_score, 6),
            "evidence_coverage": round(self.evidence_coverage, 6),
            "path": self.path,
            "note": self.note,
        }


__all__ = [
    "FAILURE_REASONS",
    "LEGAL_TRANSITIONS",
    "BreakoutState",
    "BreakoutTransition",
    "IllegalBreakoutTransitionError",
    "TransitionReason",
    "check_transition",
    "is_legal",
    "terminal_state_for_invalidation",
]
