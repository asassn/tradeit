"""Setup gates: conditions that refuse a candidate without scoring it.

A weight and a gate are different instruments and ``breakout_confirmation`` was
declared as the wrong one.

A **weight** says every candidate has some amount of this quality and the score
blends them. A **gate** says most candidates are unaffected and a few are
refused. Confirmation is the second: ``ConfirmationInputs`` requires
``acceptance_score`` as a mandatory field -- post-breakout evidence only -- so a
security with no breakout does not have a low confirmation score, **it has
none**. Weighting it at 0.2222 alongside factors every security can score
treated two different kinds of thing as one, and under
:mod:`tradeit.strategy.factors` it meant a slate containing any security
without an open breakout could never have uniform coverage.

The policy already existed
--------------------------

Nothing here invents a threshold. ``ProfileConfig`` already declares what
evidence a breakout needs before it may be called confirmed -- required closes,
follow-through, relative volume, retest quality, and a
``min_evidence_coverage`` below which it "declines to confirm at all". The
engine applies that policy and records the verdict as a
:class:`~tradeit.breakouts.lifecycle.BreakoutState`. **This module reads that
verdict; it does not second-guess it.**

Absence of evidence is not evidence against
-------------------------------------------

The gate turns on ``BreakoutState.has_broken_out``, which the lifecycle calls
"the dividing line the whole machine turns on: before it, nothing has happened
that could fail". A candidate that has not broken out is **not applicable** --
the gate is silent and the candidate stands on its other merits. Approaching a
level is not a reason to refuse a stock.

Once a bar has closed above, three things can be true and they are kept apart:

* **confirmed** -- the profile's evidence requirement is met, so the gate passes
* **failed** -- it broke out and broke down, which is evidence *against*
* **awaiting** -- it broke out and the evidence is not in yet

The last one blocks, and that is the point of a confirmation gate rather than a
defect in it: acting before confirmation is precisely what the policy exists to
prevent. It is reported separately from failure because they are different
facts about the world, and a reader who cannot tell them apart cannot tell a
setup that is developing from one that is dead.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tradeit.breakouts.lifecycle import BreakoutState

__all__ = ["GateOutcome", "GateResult", "breakout_confirmation_gate"]


class GateOutcome(StrEnum):
    """What a gate concluded. Only ``BLOCKED_*`` refuses."""

    #: The gate does not apply to this candidate. Not a pass and not a failure.
    NOT_APPLICABLE = "not_applicable"
    PASSED = "passed"
    #: Broke out, and the evidence is not in yet.
    BLOCKED_AWAITING = "blocked_awaiting_confirmation"
    #: Broke out and broke down.
    BLOCKED_FAILED = "blocked_failed_breakout"


@dataclass(frozen=True, slots=True)
class GateResult:
    """One gate's verdict, with the state that produced it."""

    gate: str
    outcome: GateOutcome
    reason: str
    state: BreakoutState | None = None

    @property
    def blocks(self) -> bool:
        return self.outcome in (
            GateOutcome.BLOCKED_AWAITING,
            GateOutcome.BLOCKED_FAILED,
        )

    @property
    def applied(self) -> bool:
        """Whether the gate had anything to say.

        Distinguishing "did not apply" from "applied and passed" is why this
        exists: a slate where every candidate is NOT_APPLICABLE has not been
        filtered by confirmation at all, and a reader should be able to see
        that rather than infer it from an absence of vetoes.
        """
        return self.outcome is not GateOutcome.NOT_APPLICABLE


_CONFIRMED = (BreakoutState.CONFIRMED, BreakoutState.RETEST_CONFIRMED)


def breakout_confirmation_gate(state: BreakoutState | None) -> GateResult:
    """Refuse a candidate whose breakout is unconfirmed or has failed.

    ``state`` is the candidate's open breakout event, or ``None`` when it has
    no open event at all. Both mean the same thing to this gate when no bar has
    closed above the level: nothing to confirm, so nothing to refuse.
    """
    if state is None:
        return GateResult(
            gate="breakout_confirmation",
            outcome=GateOutcome.NOT_APPLICABLE,
            reason="no open breakout event; the gate does not apply",
        )
    if not state.has_broken_out:
        return GateResult(
            gate="breakout_confirmation",
            outcome=GateOutcome.NOT_APPLICABLE,
            reason=(
                f"state {state} is before the breakout; nothing has happened that "
                "could be confirmed or fail"
            ),
            state=state,
        )
    if state in _CONFIRMED:
        return GateResult(
            gate="breakout_confirmation",
            outcome=GateOutcome.PASSED,
            reason=f"breakout {state} under the profile's evidence requirement",
            state=state,
        )
    if state is BreakoutState.FAILED_BREAKOUT:
        return GateResult(
            gate="breakout_confirmation",
            outcome=GateOutcome.BLOCKED_FAILED,
            reason="the breakout failed; this is evidence against the setup",
            state=state,
        )
    return GateResult(
        gate="breakout_confirmation",
        outcome=GateOutcome.BLOCKED_AWAITING,
        reason=(
            f"broke out and is {state}: the profile's evidence requirement is not "
            "yet met, and acting before confirmation is what the policy prevents"
        ),
        state=state,
    )
