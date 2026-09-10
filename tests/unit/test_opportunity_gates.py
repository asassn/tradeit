"""Tests for setup gates.

The distinction under test is the one that motivated the module: a gate is
silent about candidates it does not apply to, and refusing for want of evidence
is different from refusing on evidence.
"""

from __future__ import annotations

import pytest

from tradeit.breakouts.lifecycle import BreakoutState
from tradeit.opportunity.gates import GateOutcome, breakout_confirmation_gate


class TestTheGateIsSilentWhereItDoesNotApply:
    def test_no_open_event_is_not_applicable(self) -> None:
        """A security with no breakout has no confirmation score, not a low one."""
        result = breakout_confirmation_gate(None)
        assert result.outcome is GateOutcome.NOT_APPLICABLE
        assert not result.blocks
        assert not result.applied

    @pytest.mark.parametrize(
        "state",
        [
            BreakoutState.NOT_APPROACHING,
            BreakoutState.APPROACHING,
            BreakoutState.TESTING_RESISTANCE,
            BreakoutState.INTRADAY_BREAK,
        ],
    )
    def test_before_the_breakout_the_gate_says_nothing(self, state: BreakoutState) -> None:
        """Approaching a level is not a reason to refuse a stock."""
        result = breakout_confirmation_gate(state)
        assert result.outcome is GateOutcome.NOT_APPLICABLE
        assert not result.blocks

    def test_a_rejected_attempt_never_broke_out(self) -> None:
        """The intraday break closed back inside; no bar closed above."""
        assert not BreakoutState.REJECTED.has_broken_out
        assert breakout_confirmation_gate(BreakoutState.REJECTED).outcome is (
            GateOutcome.NOT_APPLICABLE
        )

    def test_applied_separates_silence_from_a_pass(self) -> None:
        """A slate of NOT_APPLICABLE has not been filtered by confirmation."""
        silent = breakout_confirmation_gate(None)
        passed = breakout_confirmation_gate(BreakoutState.CONFIRMED)
        assert not silent.applied
        assert passed.applied
        assert not silent.blocks and not passed.blocks


class TestConfirmation:
    @pytest.mark.parametrize("state", [BreakoutState.CONFIRMED, BreakoutState.RETEST_CONFIRMED])
    def test_a_confirmed_breakout_passes(self, state: BreakoutState) -> None:
        result = breakout_confirmation_gate(state)
        assert result.outcome is GateOutcome.PASSED
        assert not result.blocks
        assert result.state is state

    def test_the_gate_cannot_promote(self) -> None:
        """Passing removes a refusal; it adds nothing to any score.

        That is what keeps this a gate rather than a weight wearing a new name.
        """
        result = breakout_confirmation_gate(BreakoutState.CONFIRMED)
        assert not hasattr(result, "score")
        assert set(vars(type(result))["__slots__"]) == {
            "gate",
            "outcome",
            "reason",
            "state",
        }


class TestBlocking:
    def test_a_failed_breakout_is_evidence_against(self) -> None:
        result = breakout_confirmation_gate(BreakoutState.FAILED_BREAKOUT)
        assert result.outcome is GateOutcome.BLOCKED_FAILED
        assert result.blocks
        assert "evidence against" in result.reason

    @pytest.mark.parametrize(
        "state",
        [
            BreakoutState.CLOSED_ABOVE,
            BreakoutState.CONFIRMATION_PENDING,
            BreakoutState.RETEST_PENDING,
            BreakoutState.RETEST_HOLDING,
        ],
    )
    def test_awaiting_confirmation_blocks_but_is_not_failure(self, state: BreakoutState) -> None:
        """Acting before confirmation is what the policy exists to prevent."""
        result = breakout_confirmation_gate(state)
        assert result.outcome is GateOutcome.BLOCKED_AWAITING
        assert result.blocks
        assert result.outcome is not GateOutcome.BLOCKED_FAILED

    def test_the_two_refusals_are_reported_separately(self) -> None:
        """A developing setup and a dead one are different facts."""
        awaiting = breakout_confirmation_gate(BreakoutState.CONFIRMATION_PENDING)
        failed = breakout_confirmation_gate(BreakoutState.FAILED_BREAKOUT)
        assert awaiting.blocks and failed.blocks
        assert awaiting.outcome is not failed.outcome
        assert awaiting.reason != failed.reason


class TestEveryStateIsHandled:
    def test_no_state_falls_through_unclassified(self) -> None:
        """A new lifecycle state must not silently become a block."""
        for state in BreakoutState:
            result = breakout_confirmation_gate(state)
            assert result.outcome in set(GateOutcome)
            if not state.has_broken_out:
                assert result.outcome is GateOutcome.NOT_APPLICABLE, state

    def test_every_broken_out_state_gets_a_verdict(self) -> None:
        broken = [s for s in BreakoutState if s.has_broken_out]
        assert broken  # the predicate is not vacuous
        for state in broken:
            assert breakout_confirmation_gate(state).applied, state
