"""The breakout state machine, its configuration, and the frozen boundary.

Three properties this file exists to pin down, all of them about what the system
is allowed to *forget*:

* a terminal breakout state is absorbing, so a failed attempt cannot become a
  live one and a false-breakout rate cannot be computed from a population that
  erased its own failures;
* a rejected attempt is resolved rather than revived, so attempts stay countable
  and item 26's attempt history has something to count;
* the boundary an event is judged against is frozen when the event opens, so
  resistance cannot drift toward the breakout it is judging.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.breakouts.base import ConfirmationPath, EarningsContext, event_identity
from tradeit.breakouts.boundary import BreakoutBoundary, boundary_from_pattern, tolerance_for
from tradeit.breakouts.config import BreakoutEngineConfig, ProfileConfig, ToleranceConfig
from tradeit.breakouts.lifecycle import (
    FAILURE_REASONS,
    LEGAL_TRANSITIONS,
    BreakoutState,
    BreakoutTransition,
    IllegalBreakoutTransitionError,
    TransitionReason,
    check_transition,
    is_legal,
    terminal_state_for_invalidation,
)
from tradeit.core.enums import Bartimeframe
from tradeit.errors import ConfigError
from tradeit.patterns.base import Boundary, PricePoint

SESSION = dt.date(2023, 6, 15)


def boundary(**overrides: object) -> BreakoutBoundary:
    defaults: dict[str, object] = {
        "nominal": 100.0,
        "anchor_date": SESSION,
        "tolerance_pct": 0.002,
        "confidence": 70.0,
        "method": "swing_highs",
        "touch_count": 4,
        "atr_at_open": 2.0,
        "pattern_key": "pattern-1",
        "pattern_type": "flat_base",
        "pattern_quality": 80.0,
    }
    defaults.update(overrides)
    return BreakoutBoundary(**defaults)  # type: ignore[arg-type]


class TestStateVocabulary:
    def test_every_state_is_reachable_from_the_machine(self):
        """A state nobody can enter is a state nobody will ever debug."""
        reachable = {BreakoutState.NOT_APPROACHING}
        for targets in LEGAL_TRANSITIONS.values():
            reachable |= targets
        assert reachable == set(BreakoutState)

    def test_terminal_states_are_absorbing(self):
        for state in (BreakoutState.FAILED_BREAKOUT, BreakoutState.EXPIRED):
            assert LEGAL_TRANSITIONS[state] == {state}

    def test_rejected_never_returns_to_a_pre_breakout_state(self):
        """The rule that keeps attempts countable.

        If a rejected event could walk back to APPROACHING and try again, one
        event would span many attempts and "how often is an attempt turned
        back?" would have no denominator.
        """
        outgoing = LEGAL_TRANSITIONS[BreakoutState.REJECTED]
        assert not any(state.is_pre_breakout for state in outgoing)
        assert outgoing == {
            BreakoutState.REJECTED,
            BreakoutState.FAILED_BREAKOUT,
            BreakoutState.EXPIRED,
        }

    def test_confirmed_can_still_fail(self):
        """Otherwise the confirmed population would erase its own failures."""
        assert is_legal(BreakoutState.CONFIRMED, BreakoutState.FAILED_BREAKOUT)

    def test_confirmed_can_enter_a_retest(self):
        assert is_legal(BreakoutState.CONFIRMED, BreakoutState.RETEST_PENDING)

    def test_a_pullback_that_reverses_returns_to_the_confirmation_track(self):
        """A pullback that never reached the level did not test it.

        Forcing it through RETEST_HOLDING would credit the boundary with holding
        a test it never received, and shallow pullbacks are common enough that
        the retest path would become the dominant route to confirmation for
        reasons unrelated to the market.
        """
        assert is_legal(BreakoutState.RETEST_PENDING, BreakoutState.CONFIRMATION_PENDING)
        assert not is_legal(BreakoutState.RETEST_HOLDING, BreakoutState.CONFIRMATION_PENDING)

    def test_no_state_name_implies_an_action(self):
        """Phase 5 answers what price is doing, not what to do about it."""
        forbidden = ("buy", "sell", "entry", "trade", "position", "stop", "target")
        for state in BreakoutState:
            assert not any(word in str(state) for word in forbidden)

    def test_has_broken_out_is_the_dividing_line(self):
        assert BreakoutState.CLOSED_ABOVE.has_broken_out
        assert BreakoutState.FAILED_BREAKOUT.has_broken_out
        assert not BreakoutState.INTRADAY_BREAK.has_broken_out
        assert not BreakoutState.REJECTED.has_broken_out


class TestTransitionChecks:
    def test_an_illegal_edge_raises_and_names_the_remedy(self):
        with pytest.raises(IllegalBreakoutTransitionError, match="absorbing"):
            check_transition(BreakoutState.FAILED_BREAKOUT, BreakoutState.CONFIRMED)

    def test_reviving_a_rejection_points_at_attempt_numbering(self):
        with pytest.raises(IllegalBreakoutTransitionError, match="attempt_number"):
            check_transition(BreakoutState.REJECTED, BreakoutState.CLOSED_ABOVE)

    def test_pattern_invalidation_resolves_by_whether_a_breakout_happened(self):
        """Item 38 permits either outcome; this states which and why.

        An event that never closed above the level did not have a breakout to
        fail, so recording FAILED_BREAKOUT would be a claim about something that
        never happened.
        """
        assert (
            terminal_state_for_invalidation(BreakoutState.CONFIRMED)
            is BreakoutState.FAILED_BREAKOUT
        )
        assert (
            terminal_state_for_invalidation(BreakoutState.TESTING_RESISTANCE)
            is BreakoutState.EXPIRED
        )

    def test_a_transition_records_both_scores_and_the_coverage(self):
        transition = BreakoutTransition(
            session_date=SESSION,
            from_state=BreakoutState.CLOSED_ABOVE,
            to_state=BreakoutState.CONFIRMATION_PENDING,
            reason=TransitionReason.AWAITING_EVIDENCE,
            breakout_quality=88.0,
            confirmation_score=42.0,
            evidence_coverage=63.0,
        )
        payload = transition.to_payload()
        assert payload["breakout_quality"] == 88.0
        assert payload["confirmation_score"] == 42.0
        assert payload["evidence_coverage"] == 63.0

    def test_a_transition_refuses_an_out_of_range_score(self):
        with pytest.raises(ConfigError, match=r"outside \[0, 100\]"):
            BreakoutTransition(
                session_date=SESSION,
                from_state=None,
                to_state=BreakoutState.APPROACHING,
                reason=TransitionReason.OPENED,
                breakout_quality=140.0,
                confirmation_score=0.0,
            )

    def test_every_failure_reason_is_a_real_reason(self):
        assert set(TransitionReason) >= FAILURE_REASONS


class TestTolerance:
    def test_the_zone_widens_with_volatility(self):
        config = ToleranceConfig()
        quiet = tolerance_for(level=100.0, atr=0.5, confidence=70.0, config=config)
        fast = tolerance_for(level=100.0, atr=5.0, confidence=70.0, config=config)
        assert fast > quiet

    def test_the_zone_widens_for_a_low_confidence_boundary(self):
        config = ToleranceConfig()
        sure = tolerance_for(level=100.0, atr=3.0, confidence=100.0, config=config)
        unsure = tolerance_for(level=100.0, atr=3.0, confidence=0.0, config=config)
        assert unsure > sure
        assert unsure / sure == pytest.approx(config.low_confidence_widening, rel=1e-6)

    def test_a_missing_atr_falls_back_to_the_percentage_floor(self):
        config = ToleranceConfig()
        value = tolerance_for(level=100.0, atr=None, confidence=100.0, config=config)
        assert value == pytest.approx(config.min_tolerance_pct)

    def test_the_zone_is_capped(self):
        config = ToleranceConfig()
        value = tolerance_for(level=10.0, atr=9.0, confidence=0.0, config=config)
        assert value == pytest.approx(config.max_tolerance_pct)

    def test_the_three_prices_are_ordered(self):
        b = boundary()
        low, high = b.zone()
        assert low < b.nominal < high
        assert b.threshold() == pytest.approx(high)
        assert b.floor() == pytest.approx(low)

    def test_a_close_inside_the_zone_is_not_a_breakout(self):
        b = boundary(tolerance_pct=0.005)
        assert not b.clears(100.4)
        assert b.contains(100.4)
        assert b.clears(100.6)

    def test_penetration_is_measured_from_the_threshold_not_the_level(self):
        """Clearing the ambiguity zone is worth zero, not a fraction of a point."""
        b = boundary(tolerance_pct=0.01, atr_at_open=2.0)
        assert b.penetration_atr(101.0) == pytest.approx(0.0)
        assert b.penetration_atr(103.0) == pytest.approx(1.0)

    def test_atr_relative_distance_is_none_rather_than_a_unit_switch(self):
        """A silent fallback to percent is how an ATR threshold ends up
        compared against a percentage, producing a plausible wrong number."""
        b = boundary(atr_at_open=None)
        assert b.distance_atr(105.0) is None
        assert b.penetration_atr(105.0) is None
        assert b.distance_pct(105.0) == pytest.approx(0.05)

    def test_a_sloped_boundary_follows_its_line(self):
        b = boundary(slope_per_session=0.1)
        assert b.level_on(0) == pytest.approx(100.0)
        assert b.level_on(10) == pytest.approx(101.0)
        assert b.threshold(10) > b.threshold(0)


class TestBoundaryConstruction:
    def test_a_support_line_is_refused(self):
        """Measuring penetration above support produces individually plausible
        numbers about nothing, which is the defect that survives review."""
        support = Boundary(kind="support", method="swing_lows", level=90.0, anchor_date=SESSION)
        with pytest.raises(ConfigError, match="must be resistance"):
            boundary_from_pattern(support, atr=2.0, config=ToleranceConfig())

    def test_a_pattern_boundary_carries_its_provenance(self):
        resistance = Boundary(
            kind="resistance",
            method="horizontal_cluster",
            level=100.0,
            anchor_date=SESSION,
            touches=(PricePoint(SESSION, 100.0), PricePoint(SESSION, 99.9)),
            confidence=64.0,
        )
        frozen = boundary_from_pattern(
            resistance,
            atr=2.0,
            config=ToleranceConfig(),
            pattern_key="abc",
            pattern_type="flat_base",
            pattern_quality=77.0,
        )
        assert frozen.is_attached
        assert frozen.touch_count == 2
        assert frozen.confidence == 64.0
        assert frozen.pattern_quality == 77.0

    def test_an_unattached_level_is_marked_rather_than_refused(self):
        """A level from prior highs is a real structure and a weaker object.
        The dataset has to be able to separate the two populations."""
        assert not boundary(pattern_key="").is_attached


class TestEventIdentity:
    def test_identity_is_stable_across_the_attempt(self):
        first = event_identity(
            instrument_id=1,
            timeframe=Bartimeframe.D1,
            pattern_key="p",
            boundary=boundary(),
            attempt_number=1,
        )
        again = event_identity(
            instrument_id=1,
            timeframe=Bartimeframe.D1,
            pattern_key="p",
            boundary=boundary(nominal=100.0),
            attempt_number=1,
        )
        assert first == again

    def test_each_attempt_gets_its_own_identity(self):
        """Item 26: three goes at a level are three records, not one that
        overwrites itself."""
        keys = {
            event_identity(
                instrument_id=1,
                timeframe=Bartimeframe.D1,
                pattern_key="p",
                boundary=boundary(),
                attempt_number=n,
            )
            for n in (1, 2, 3)
        }
        assert len(keys) == 3

    def test_timeframes_are_separate_events(self):
        """Item 39: a daily breakout and a weekly breakout are different events
        and both truths coexist."""
        daily = event_identity(
            instrument_id=1,
            timeframe=Bartimeframe.D1,
            pattern_key="p",
            boundary=boundary(),
            attempt_number=1,
        )
        weekly = event_identity(
            instrument_id=1,
            timeframe=Bartimeframe.W1,
            pattern_key="p",
            boundary=boundary(),
            attempt_number=1,
        )
        assert daily != weekly

    def test_an_unattached_level_still_produces_a_stable_key(self):
        args = {
            "instrument_id": 1,
            "timeframe": Bartimeframe.D1,
            "pattern_key": "",
            "boundary": boundary(pattern_key=""),
            "attempt_number": 1,
        }
        assert event_identity(**args) == event_identity(**args)  # type: ignore[arg-type]

    def test_attempt_zero_is_refused(self):
        with pytest.raises(ConfigError, match="at least 1"):
            event_identity(
                instrument_id=1,
                timeframe=Bartimeframe.D1,
                pattern_key="p",
                boundary=boundary(),
                attempt_number=0,
            )


class TestConfiguration:
    def test_the_default_config_is_valid(self):
        assert BreakoutEngineConfig().profile("balanced").name == "balanced"

    def test_an_unknown_profile_is_refused(self):
        with pytest.raises(ConfigError, match="active_profile"):
            BreakoutEngineConfig(active_profile="reckless")

    def test_retest_tolerance_must_stay_below_the_decisive_failure_threshold(self):
        """Otherwise an ordinary retest fails the event it is testing.

        Not a stylistic constraint: the whole retest engine exists to
        distinguish a test from a break, and a failure rule that fires first
        makes the distinction unreachable.
        """
        with pytest.raises(ConfigError, match="max_undercut_atr"):
            BreakoutEngineConfig(
                retest={"max_undercut_atr": 2.0},  # type: ignore[arg-type]
                failure={"decisive_close_atr": 1.2},  # type: ignore[arg-type]
            )

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ConfigError, match="sum to"):
            BreakoutEngineConfig(quality_weights={"boundary_quality": 0.9})  # type: ignore[arg-type]

    def test_volume_requirements_are_pattern_aware(self):
        """Item 7 forbids one universal relative-volume threshold.

        A flat base is a quiet structure by construction; demanding flag-like
        volume of it is demanding that it stop being a flat base.
        """
        config = BreakoutEngineConfig().volume
        assert config.full_for("flat_base") < config.full_for("high_tight_flag")
        assert config.full_for("bull_flag") == config.relative_volume_full
        assert config.full_for(None) == config.relative_volume_full

    def test_profiles_describe_evidence_not_risk(self):
        """The names are about how much evidence, never about how much money."""
        config = BreakoutEngineConfig()
        conservative = config.profile("conservative")
        aggressive = config.profile("aggressive")
        assert conservative.required_closes >= aggressive.required_closes
        assert conservative.min_follow_through > aggressive.min_follow_through
        assert conservative.min_evidence_coverage > aggressive.min_evidence_coverage
        for profile in config.profiles.values():
            assert not hasattr(profile, "position_size")
            assert not hasattr(profile, "risk")

    def test_a_profile_with_an_unknown_path_is_refused(self):
        with pytest.raises(ConfigError, match="unknown confirmation path"):
            ProfileConfig(name="x", paths=("telepathy",))

    def test_confirmation_weights_exclude_the_breakout_bar(self):
        """Folding the breakout's own quality into confirmation would correlate
        the two scores by construction and destroy the item-18 distinction."""
        names = set(BreakoutEngineConfig().confirmation_weights.as_mapping())
        assert not names & {"candle_quality", "penetration", "boundary_quality"}

    def test_the_config_digest_changes_with_any_number(self):
        from tradeit.breakouts.engine import BreakoutEngine

        base = BreakoutEngine()
        moved = BreakoutEngine(
            BreakoutEngineConfig(tolerance={"atr_multiple": 0.2})  # type: ignore[arg-type]
        )
        assert base.config_digest() != moved.config_digest()


class TestVocabularySeparation:
    def test_earnings_unknown_is_not_the_same_as_no_earnings(self):
        """A dataset built without a calendar must not be readable as one where
        the answer was no."""
        assert EarningsContext.UNKNOWN_EVENT_CONTEXT is not EarningsContext.NO_EARNINGS_NEARBY

    def test_no_confirmation_path_is_stored_explicitly(self):
        """Rather than left null, so the absence is a recorded fact."""
        assert str(ConfirmationPath.NONE) == "none"
