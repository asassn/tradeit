"""Legal pattern state transitions.

A pattern's state history is a claim about what the system believed over time.
For that claim to mean anything, the transitions have to be constrained: a
structure that was INVALIDATED on Tuesday cannot be MATURE on Wednesday under
the same identity. If genuinely new structure appears later, it is a **new
pattern**, and pretending otherwise erases the fact that the first one failed —
which is exactly the fact a false-positive rate is computed from.

**The shared machine.**

    FORMING ──→ MATURE ──→ NEAR_BREAKOUT ──→ BROKEN_OUT_UNCONFIRMED
       │           │             │                     │
       └───────────┴─────────────┴─────────────────────┴──→ INVALIDATED
       └───────────┴─────────────┴─────────────────────┴──→ EXPIRED

Two edges are less obvious and both are deliberate:

* **NEAR_BREAKOUT → MATURE is legal.** Price approaching resistance and then
  drifting back is ordinary behaviour, not a failure. Forbidding it would force
  a pattern to invalidate every time it hesitated.
* **BROKEN_OUT_UNCONFIRMED → INVALIDATED is legal.** A structure can clear its
  resistance and then break its support. Phase 4 does not judge whether the
  breakout was valid; it does record that the structure subsequently failed.

**What is forbidden**, and why it matters more than it looks: any edge *out of*
a terminal state. INVALIDATED and EXPIRED are absorbing. Allowing
INVALIDATED → MATURE would let a failed pattern quietly become a successful one
under the same identity, which does not merely lose information — it inverts
it. Every measurement of how often patterns fail would be computed from a
population that had erased its own failures.

Pattern families may narrow this machine but never widen it. A breakout-retest
structure, for instance, begins life already resolved and has no FORMING state.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from tradeit.core.enums import PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.base import PatternState


class TransitionReason(StrEnum):
    """Why a pattern moved between states. Recorded so a history reads."""

    DETECTED = "detected"
    #: Re-detected on a later session with the same identity.
    ADVANCED = "advanced"
    #: Not re-detected, but resolved against stored levels using today's price.
    CARRIED_FORWARD = "carried_forward"
    #: Price closed below the stored invalidation level.
    SUPPORT_BROKEN = "support_broken"
    #: Price closed above the stored resistance level.
    RESISTANCE_CLEARED = "resistance_cleared"
    #: Ran out of time without resolving.
    TIMED_OUT = "timed_out"
    #: Disappeared from detection without resolving or breaking.
    LOST = "lost"
    #: Absorbed by a larger structure on the same instrument.
    SUPERSEDED = "superseded"


#: The shared machine. Every family's transitions must be a subset of this.
LEGAL_TRANSITIONS: Mapping[PatternState, frozenset[PatternState]] = {
    PatternState.FORMING: frozenset(
        {
            PatternState.FORMING,
            PatternState.MATURE,
            PatternState.NEAR_BREAKOUT,
            PatternState.BROKEN_OUT_UNCONFIRMED,
            PatternState.INVALIDATED,
            PatternState.EXPIRED,
        }
    ),
    PatternState.MATURE: frozenset(
        {
            PatternState.MATURE,
            PatternState.NEAR_BREAKOUT,
            PatternState.BROKEN_OUT_UNCONFIRMED,
            PatternState.INVALIDATED,
            PatternState.EXPIRED,
        }
    ),
    PatternState.NEAR_BREAKOUT: frozenset(
        {
            # Back to MATURE is legal: approaching resistance and drifting away
            # is ordinary hesitation, not failure.
            PatternState.MATURE,
            PatternState.NEAR_BREAKOUT,
            PatternState.BROKEN_OUT_UNCONFIRMED,
            PatternState.INVALIDATED,
            PatternState.EXPIRED,
        }
    ),
    PatternState.BROKEN_OUT_UNCONFIRMED: frozenset(
        {
            PatternState.BROKEN_OUT_UNCONFIRMED,
            # A structure can clear resistance and then break support. Phase 4
            # does not judge the breakout; it does record the later failure.
            PatternState.INVALIDATED,
            PatternState.EXPIRED,
        }
    ),
    # Absorbing. No edges out, ever.
    PatternState.INVALIDATED: frozenset({PatternState.INVALIDATED}),
    PatternState.EXPIRED: frozenset({PatternState.EXPIRED}),
}

#: A pattern family may forbid states the shared machine allows. Recorded here
#: rather than inside each detector so the whole set is reviewable at once.
FAMILY_CONSTRAINTS: Mapping[PatternType, frozenset[PatternState]] = {
    # A retest structure is observed only after price has already moved through
    # resistance, so it never occupies the pre-breakout states. Emitting one as
    # NEAR_BREAKOUT would describe a different structure entirely.
    PatternType.BREAKOUT_RETEST: frozenset(
        {
            PatternState.FORMING,
            PatternState.MATURE,
            PatternState.BROKEN_OUT_UNCONFIRMED,
            PatternState.INVALIDATED,
            PatternState.EXPIRED,
        }
    ),
}


class IllegalTransitionError(ConfigError):
    """An attempt to move a pattern along an edge that does not exist."""


def is_legal(
    from_state: PatternState,
    to_state: PatternState,
    *,
    pattern_type: PatternType | None = None,
) -> bool:
    """Whether one identity may move between these states."""
    if to_state not in LEGAL_TRANSITIONS.get(from_state, frozenset()):
        return False
    if pattern_type is not None:
        permitted = FAMILY_CONSTRAINTS.get(pattern_type)
        if permitted is not None and to_state not in permitted:
            return False
    return True


def check_transition(
    from_state: PatternState,
    to_state: PatternState,
    *,
    pattern_type: PatternType | None = None,
    identity_key: str = "",
) -> None:
    """Raise unless the transition is legal.

    The error message names the remedy rather than only the problem, because
    the remedy is genuinely non-obvious: the answer to "my pattern went from
    INVALIDATED to MATURE" is not to permit the edge, it is to mint a new
    identity for what is genuinely a new structure.
    """
    if is_legal(from_state, to_state, pattern_type=pattern_type):
        return

    where = f" for {identity_key}" if identity_key else ""
    if from_state.is_terminal:
        raise IllegalTransitionError(
            f"{from_state} -> {to_state}{where}: terminal states are absorbing. "
            "A failed or expired structure must not become a live one under the "
            "same identity -- every measurement of how often patterns fail would "
            "be computed from a population that erased its own failures. If new "
            "structure has genuinely appeared, mint a new pattern identity."
        )
    raise IllegalTransitionError(
        f"{from_state} -> {to_state}{where} is not a legal transition"
        + (f" for {pattern_type}" if pattern_type else "")
    )


def terminal_reason(state: PatternState) -> TransitionReason:
    """The reason that belongs with a terminal state reached by price."""
    if state is PatternState.INVALIDATED:
        return TransitionReason.SUPPORT_BROKEN
    return TransitionReason.TIMED_OUT


@dataclass(frozen=True, slots=True)
class StateTransition:
    """One entry in a pattern's history. Append-only.

    Carries the quality *and* coverage at the moment of transition. Coverage
    matters historically for the same reason it matters currently: a pattern
    that matured at quality 88 on 40% coverage matured on very little evidence,
    and a history that records only the score cannot say so.
    """

    session_date: dt.date
    from_state: PatternState | None
    to_state: PatternState
    reason: TransitionReason
    quality: float
    evidence_coverage: float = 100.0
    note: str = ""

    def __post_init__(self) -> None:
        if self.from_state is not None:
            check_transition(self.from_state, self.to_state)

    def __str__(self) -> str:
        origin = str(self.from_state) if self.from_state else "-"
        return (
            f"{self.session_date.isoformat()}  {origin} -> {self.to_state} "
            f"({self.reason}, quality {self.quality:.0f}, "
            f"coverage {self.evidence_coverage:.0f})"
        )
