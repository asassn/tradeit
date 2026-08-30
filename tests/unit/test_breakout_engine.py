"""The engine: states, scoring, paths, retests, failures and expiry.

The load-bearing assertions are about *separation*:

* breakout quality is frozen at the breakout bar and confirmation is not, so the
  pair can move independently (item 18);
* quality, coverage and confidence are three numbers and a scenario can move one
  without moving the others (items 19 and 41);
* rejection, failure and expiry are three outcomes, and merging any pair would
  inflate a statistic a later phase will want to trust.

Every scenario runs through :func:`tradeit.breakouts.validation.run_scenario`,
so a test cannot pass by warming the boundary up differently from the report.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.breakouts.base import ConfirmationPath, EarningsContext
from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.context import (
    BreakoutContext,
    MarketReading,
    SectorReading,
    SectorSourceKind,
    measure_relative_strength,
)
from tradeit.breakouts.engine import BreakoutEngine, SessionInputs
from tradeit.breakouts.lifecycle import BreakoutState, TransitionReason
from tradeit.breakouts.profiles import (
    PathEvidence,
    evaluate_acceptance,
    evaluate_momentum,
    evaluate_profile,
    evaluate_retest,
)
from tradeit.breakouts.retest import retest_quality
from tradeit.breakouts.scoring import BREAKOUT_SCORER_VERSION
from tradeit.breakouts.synthetic import BreakoutGenerator, BreakoutSpec
from tradeit.breakouts.validation import BoundarySpec, replay_scenario, run_scenario
from tradeit.core.enums import MarketRegime
from tradeit.errors import ConfigError

GENERATOR = BreakoutGenerator()
UTC = dt.UTC


def event_for(name: str, **kwargs: object):
    scenario = getattr(GENERATOR, name)(seed=0)
    return run_scenario(scenario, **kwargs)  # type: ignore[arg-type]


class TestCleanBreakout:
    def test_a_clean_breakout_reaches_confirmed(self):
        event = event_for("clean_breakout")
        assert event.state is BreakoutState.CONFIRMED
        assert event.confirmed_path is not ConfirmationPath.NONE

    def test_the_three_timestamps_are_recorded_separately(self):
        """Item 1 asks for approach, penetration and qualifying close as three
        facts; they are frequently three different days."""
        event = event_for("clean_breakout")
        assert event.first_approach_session is not None
        assert event.first_penetration_session is not None
        assert event.first_qualifying_close_session is not None
        assert event.first_approach_session <= event.first_qualifying_close_session

    def test_the_history_is_append_only_and_ordered(self):
        event = event_for("clean_breakout")
        dates = [o.session_date for o in event.observations]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)

    def test_the_progression_survives_into_the_history(self):
        """Item 15: the sequence must remain recoverable."""
        event = event_for("clean_breakout")
        states = [o.state for o in event.observations]
        assert BreakoutState.CLOSED_ABOVE in states
        assert BreakoutState.CONFIRMED in states

    def test_the_explanation_stops_where_phase_five_stops(self):
        """No BUY, no size, no stop, no target — and nowhere for them to come
        from."""
        text = event_for("clean_breakout").explain().lower()
        for word in ("buy", "sell", "position size", "stop loss", "target"):
            assert word not in text
        assert "breakout quality" in text
        assert "evidence coverage" in text

    def test_the_scorer_version_travels_with_the_event(self):
        assert event_for("clean_breakout").scorer_version == BREAKOUT_SCORER_VERSION


class TestQualityOrdering:
    @pytest.mark.parametrize(
        ("stronger", "weaker"),
        [
            ("clean_breakout", "weak_breakout"),
            ("clean_breakout", "low_volume_breakout"),
            ("clean_breakout", "wick_breakout"),
            ("high_volume_breakout", "low_volume_breakout"),
        ],
    )
    def test_the_stronger_breakout_scores_higher(self, stronger, weaker):
        assert event_for(stronger).breakout_quality > event_for(weaker).breakout_quality

    def test_a_one_tick_penetration_never_qualifies(self):
        """A close a hair above the level sits inside any sane tolerance zone,
        which is what the zone is for."""
        assert not event_for("one_tick_penetration").has_broken_out

    def test_extension_is_reported_without_being_called_bad(self):
        """Item 24: a gap breakout can be strong and extended at once, and the
        engine represents both rather than picking."""
        event = event_for("extreme_extension")
        component = event.component("penetration")
        assert component is not None
        assert component.measurements["penetration_score"] > 80
        assert component.measurements["extension_score"] < 40
        assert event.gap_class is not None


class TestScoreSeparation:
    def test_quality_and_confirmation_move_independently(self):
        """The item-18 example: a strong first close with no follow-through
        yet is quality-high and confirmation-low."""
        scenario = GENERATOR.clean_breakout(seed=0)
        early = run_scenario(
            scenario, stop_at=(scenario.breakout_index or 0) + 1, profile="conservative"
        )
        late = run_scenario(scenario, profile="conservative")
        assert early.breakout_quality == pytest.approx(late.breakout_quality)
        assert early.confirmation_score < late.confirmation_score

    def test_quality_is_frozen_after_the_breakout_bar(self):
        """Every later observation carries the same number, by construction."""
        event = event_for("clean_breakout")
        after = [
            o.breakout_quality
            for o in event.observations
            if o.session_date >= (event.first_qualifying_close_session or o.session_date)
        ]
        assert len(set(round(v, 9) for v in after)) == 1

    def test_coverage_is_separate_from_quality(self):
        """A missing sector series lowers coverage, not quality: 'we could not
        measure the sector' is not a bearish observation about the sector."""
        scenario = GENERATOR.clean_breakout(seed=0)
        bare = run_scenario(scenario)
        closes = [float(b.close) * 0.5 for b in scenario.bars]
        rich = run_scenario(
            scenario,
            context=BreakoutContext(
                benchmark_closes=closes,
                sector=SectorReading(strength_score=72.0, source=SectorSourceKind.ETF_PROXY),
                market=MarketReading(regime=MarketRegime.BULL_TRENDING),
            ),
        )
        assert rich.evidence_coverage > bare.evidence_coverage
        assert bare.coverage_gaps()

    def test_confidence_is_not_quality(self):
        """Item 41. Identical price action against a one-touch unattached level
        should read as an equally good breakout, identified less certainly."""
        scenario = GENERATOR.clean_breakout(seed=0)
        strong = run_scenario(scenario, spec=BoundarySpec())
        weak = run_scenario(scenario, spec=BoundarySpec.weak())
        assert weak.confidence < strong.confidence - 20
        assert weak.breakout_quality > strong.breakout_quality * 0.8

    def test_the_components_reconcile_with_the_composite(self):
        """Guards a late adjustment applied to the score without being recorded
        as a component."""
        event = event_for("clean_breakout")
        total = sum(c.effective_weight for c in event.quality_components)
        expected = sum(c.contribution for c in event.quality_components) / total
        assert expected == pytest.approx(event.breakout_quality, abs=1e-6)

    def test_an_unavailable_component_renormalises_rather_than_scoring_zero(self):
        event = event_for("clean_breakout")
        missing = [c for c in event.quality_components if c.unavailable]
        assert missing
        assert all(c.unavailable_reason for c in missing)
        assert event.breakout_quality > 0


class TestRejection:
    def test_an_intraday_break_that_closes_below_is_rejected(self):
        event = event_for("intraday_break_close_below", reopen=False)
        assert event.state is BreakoutState.REJECTED
        assert not event.has_broken_out

    def test_a_gap_that_fades_is_rejected_rather_than_failed(self):
        """Nothing was ever held, so there is no breakout to have failed."""
        event = event_for("gap_and_fade", reopen=False)
        assert event.state is BreakoutState.REJECTED

    def test_a_rejection_is_resolved_but_not_terminal(self):
        event = event_for("immediate_rejection", reopen=False)
        assert event.state.is_resolved
        assert not event.state.is_terminal

    def test_a_later_attempt_is_a_new_event(self):
        """Item 26: attempts stay countable because each has its own identity."""
        attempts = replay_scenario(GENERATOR.multiple_attempts(seed=0))
        assert len(attempts) >= 2
        assert [e.attempt_number for e in attempts] == list(range(1, len(attempts) + 1))
        assert len({e.event_key for e in attempts}) == len(attempts)


class TestFailure:
    def test_a_false_breakout_fails_rather_than_expires(self):
        """Merging the two would inflate every failure rate with non-events —
        or, here, hide a real failure among them."""
        event = event_for("false_breakout")
        assert event.state is BreakoutState.FAILED_BREAKOUT
        assert event.terminal_reason is not None

    def test_a_failed_retest_fails(self):
        assert event_for("failed_retest").state is BreakoutState.FAILED_BREAKOUT

    def test_failure_does_not_erase_the_earlier_states(self):
        """The brief's own T0..T+7 example."""
        event = event_for("false_breakout")
        states = [o.state for o in event.observations]
        assert BreakoutState.CLOSED_ABOVE in states
        assert states[-1] is BreakoutState.FAILED_BREAKOUT

    def test_adverse_excursion_excludes_the_breakout_bar_s_own_low(self):
        """A wide-range breakout bar has a low well below its close. Counting it
        as adverse excursion would make any decisive breakout fail itself on the
        next session, whatever price did.
        """
        event = event_for("clean_breakout")
        assert event.state is not BreakoutState.FAILED_BREAKOUT
        assert event.breakout_low is not None
        assert event.lowest_low_since_breakout is not None
        assert event.lowest_low_since_breakout >= event.breakout_low

    def test_pattern_invalidation_ends_the_event_and_stays_attached(self):
        """Item 38: the breakout is not silently detached from its pattern."""
        scenario = GENERATOR.clean_breakout(seed=0)
        index = (scenario.breakout_index or 0) + 2
        event = run_scenario(scenario, invalidate_at=index)
        assert event.state is BreakoutState.FAILED_BREAKOUT
        assert event.terminal_reason is TransitionReason.PATTERN_INVALIDATED
        assert event.boundary.pattern_key


class TestExpiration:
    def test_an_approach_that_never_breaks_expires_rather_than_failing(self):
        """Nothing happened, which is not a failure. Recording it as one would
        put non-events into the failure rate."""
        config = BreakoutEngineConfig(
            expiration={"max_sessions_approaching": 5},  # type: ignore[arg-type]
        )
        event = run_scenario(GENERATOR.no_breakout(seed=0), config=config, reopen=False)
        assert event.state is BreakoutState.EXPIRED
        assert event.terminal_reason is TransitionReason.TIMED_OUT

    def test_a_confirmed_event_is_resolved_rather_than_failed_at_the_end(self):
        """A confirmed event monitored to the end of its window is neither a
        failure nor a success — the engine simply stops having anything to say
        about it, and the reason says so."""
        config = BreakoutEngineConfig(
            expiration={"max_sessions_confirmed": 2},  # type: ignore[arg-type]
        )
        attempts = replay_scenario(
            GENERATOR.strong_follow_through(seed=0), config=config, profile="aggressive"
        )
        resolved = [
            event for event in attempts if event.terminal_reason is TransitionReason.RESOLVED
        ]
        assert resolved, [str(e.state) for e in attempts]
        for event in resolved:
            assert event.state is BreakoutState.EXPIRED
            assert BreakoutState.CONFIRMED in {o.state for o in event.observations}

    def test_confirmation_wins_over_expiry_on_the_same_session(self):
        """An event that satisfies its profile on the last session of its window
        confirmed; it did not expire."""
        config = BreakoutEngineConfig(
            expiration={"max_sessions_pending": 1},  # type: ignore[arg-type]
        )
        attempts = replay_scenario(
            GENERATOR.strong_follow_through(seed=0), config=config, profile="aggressive"
        )
        states = {o.state for event in attempts for o in event.observations}
        assert BreakoutState.CONFIRMED in states


class TestRetest:
    def test_a_retest_is_judged_against_the_original_level(self):
        """The boundary is frozen at the event, so a level refitted through the
        pullback low — which would make every retest hold — cannot happen."""
        scenario = GENERATOR.successful_retest(seed=0)
        event = run_scenario(scenario)
        assert event.boundary.nominal == pytest.approx(scenario.level)
        assert event.retest is not None

    def test_a_shallow_undercut_that_recovers_is_not_a_failure(self):
        event = event_for("successful_retest")
        assert event.retest is not None
        assert event.retest.held
        assert BreakoutState.RETEST_HOLDING in {o.state for o in event.observations}

    def test_a_deep_undercut_fails(self):
        event = event_for("failed_retest")
        assert event.state is BreakoutState.FAILED_BREAKOUT

    def test_retest_quality_rises_with_a_shallower_undercut(self):
        from tradeit.breakouts.base import RetestRecord
        from tradeit.breakouts.config import RetestConfig

        config = RetestConfig()
        shallow = RetestRecord(
            started_session=dt.date(2023, 1, 3),
            low=99.5,
            low_session=dt.date(2023, 1, 4),
            max_undercut_atr=0.1,
            sessions=3,
            sessions_below_level=1,
            volume_ratio=0.4,
            range_ratio=0.5,
            recovery_closes=1,
        )
        deep = RetestRecord(
            started_session=dt.date(2023, 1, 3),
            low=97.0,
            low_session=dt.date(2023, 1, 4),
            max_undercut_atr=0.5,
            sessions=3,
            sessions_below_level=3,
            volume_ratio=0.4,
            range_ratio=0.5,
            recovery_closes=1,
        )
        assert retest_quality(shallow, config=config) > retest_quality(deep, config=config)

    def test_the_retest_record_survives_its_own_resolution(self):
        """A breakout that survived a 0.5-ATR undercut is a different object
        from one that never pulled back."""
        event = event_for("successful_retest")
        assert event.retest is not None
        assert not event.retest_active or event.state.is_retesting


class TestConfirmationPaths:
    def test_momentum_needs_the_breakout_bar_s_own_volume(self):
        """Using today's relative volume would let an event whose breakout came
        on half its average volume satisfy the requirement days later on an
        unrelated busy session."""
        from tradeit.breakouts.config import ProfileConfig
        from tradeit.breakouts.measures import AcceptanceReading, FollowThroughReading

        profile = ProfileConfig(name="p", min_relative_volume=1.5, follow_through_window=0)
        evidence = PathEvidence(
            qualifying_closes=1,
            consecutive_closes=1,
            breakout_quality=90.0,
            relative_volume=0.9,
            acceptance=AcceptanceReading(1, 1, None, None, 100.0),
            follow_through=FollowThroughReading(1, None, None, 1, None, None, 80.0),
            retest=None,
            evidence_coverage=100.0,
            sessions_since_breakout=1,
        )
        assert not evaluate_momentum(evidence, profile).confirmed

    def test_a_profile_reports_what_blocked_it(self):
        """'Awaiting evidence' is not an answer a reviewer can act on."""
        from tradeit.breakouts.config import ProfileConfig
        from tradeit.breakouts.measures import AcceptanceReading, FollowThroughReading

        profile = ProfileConfig(name="p", min_relative_volume=1.5, acceptance_closes=6)
        evidence = PathEvidence(
            qualifying_closes=1,
            consecutive_closes=1,
            breakout_quality=90.0,
            relative_volume=0.9,
            acceptance=AcceptanceReading(1, 1, None, 0.5, 20.0),
            follow_through=FollowThroughReading(1, None, None, 1, None, None, 80.0),
            retest=None,
            evidence_coverage=100.0,
            sessions_since_breakout=1,
        )
        decision = evaluate_profile(evidence, profile)
        assert not decision.confirmed
        assert "relative volume" in decision.reason()

    def test_a_retest_that_has_not_recovered_does_not_confirm(self):
        from tradeit.breakouts.base import RetestRecord
        from tradeit.breakouts.config import ProfileConfig
        from tradeit.breakouts.measures import AcceptanceReading, FollowThroughReading

        record = RetestRecord(
            started_session=dt.date(2023, 1, 3),
            low=99.0,
            low_session=dt.date(2023, 1, 4),
            max_undercut_atr=0.2,
            quality=90.0,
        )
        evidence = PathEvidence(
            qualifying_closes=1,
            consecutive_closes=0,
            breakout_quality=90.0,
            relative_volume=2.0,
            acceptance=AcceptanceReading(1, 0, None, None, 0.0),
            follow_through=FollowThroughReading(3, None, None, 0, None, None, 30.0),
            retest=record,
            evidence_coverage=100.0,
            sessions_since_breakout=3,
        )
        assert not evaluate_retest(evidence, ProfileConfig(name="p")).confirmed

    def test_acceptance_needs_consecutive_closes(self):
        from tradeit.breakouts.config import ProfileConfig
        from tradeit.breakouts.measures import AcceptanceReading, FollowThroughReading

        profile = ProfileConfig(name="p", acceptance_closes=4)
        evidence = PathEvidence(
            qualifying_closes=5,
            consecutive_closes=2,
            breakout_quality=60.0,
            relative_volume=1.0,
            acceptance=AcceptanceReading(5, 2, None, 0.5, 50.0),
            follow_through=FollowThroughReading(5, None, None, 3, None, None, 60.0),
            retest=None,
            evidence_coverage=100.0,
            sessions_since_breakout=5,
        )
        assert not evaluate_acceptance(evidence, profile).confirmed

    def test_low_coverage_blocks_confirmation_with_a_stated_reason(self):
        """Not a trading threshold: below it the confirmation score is computed
        from too little evidence to mean what it says."""
        from tradeit.breakouts.config import ProfileConfig
        from tradeit.breakouts.measures import AcceptanceReading, FollowThroughReading

        profile = ProfileConfig(name="p", min_evidence_coverage=80.0)
        evidence = PathEvidence(
            qualifying_closes=3,
            consecutive_closes=3,
            breakout_quality=95.0,
            relative_volume=3.0,
            acceptance=AcceptanceReading(3, 3, None, 0.4, 100.0),
            follow_through=FollowThroughReading(3, None, None, 3, None, None, 95.0),
            retest=None,
            evidence_coverage=40.0,
            sessions_since_breakout=3,
        )
        decision = evaluate_profile(evidence, profile)
        assert not decision.confirmed
        assert "coverage" in decision.reason()

    def test_the_path_that_confirmed_is_recorded(self):
        event = event_for("clean_breakout")
        assert event.confirmed_path in (
            ConfirmationPath.MOMENTUM,
            ConfirmationPath.RETEST,
            ConfirmationPath.ACCEPTANCE,
        )
        assert event.confirmed_session is not None

    def test_a_retest_confirmation_takes_the_retest_path(self):
        event = event_for("successful_retest")
        if event.state is BreakoutState.CONFIRMED:
            assert event.confirmed_path is ConfirmationPath.RETEST


class TestProfiles:
    def test_conservative_confirms_no_more_readily_than_aggressive(self):
        scenario = GENERATOR.weak_breakout(seed=0)
        aggressive = run_scenario(scenario, profile="aggressive")
        conservative = run_scenario(scenario, profile="conservative")
        confirmed = {
            name: event.state is BreakoutState.CONFIRMED
            for name, event in (
                ("aggressive", aggressive),
                ("conservative", conservative),
            )
        }
        assert not (confirmed["conservative"] and not confirmed["aggressive"])

    def test_the_profile_is_recorded_on_the_event(self):
        """Two events under different policies are not comparable, and a state
        that does not say which policy produced it cannot be interpreted."""
        assert run_scenario(GENERATOR.clean_breakout(), profile="conservative").profile_name == (
            "conservative"
        )

    def test_an_unknown_profile_is_refused_at_construction(self):
        with pytest.raises(ConfigError, match="no confirmation profile"):
            BreakoutEngine(profile="telepathic")


class TestContext:
    def test_a_bear_market_does_not_reject_the_breakout(self):
        """Item 21. Regime is stored as evidence; whether it disqualifies a
        setup is a later phase's judgement."""
        scenario = GENERATOR.clean_breakout(seed=0)
        closes = [float(b.close) * 0.4 for b in scenario.bars]
        bear = run_scenario(
            scenario,
            context=BreakoutContext(
                benchmark_closes=closes,
                market=MarketReading(regime=MarketRegime.BEAR_TRENDING),
            ),
        )
        assert bear.has_broken_out
        assert bear.state in (BreakoutState.CONFIRMED, BreakoutState.CONFIRMATION_PENDING)

    def test_missing_sector_data_is_not_treated_as_bearish(self):
        event = event_for("clean_breakout")
        sector = event.component("sector_context")
        assert sector is not None
        assert sector.unavailable
        assert sector.unavailable_reason

    def test_a_misaligned_benchmark_is_refused_rather_than_used(self):
        """A plausible number computed from mismatched days is worse than none."""
        reading = measure_relative_strength([1.0] * 20, [1.0] * 15)
        assert not reading.available
        assert "misaligned" in reading.unavailable_reason

    def test_earnings_context_defaults_to_unknown_not_absent(self):
        assert event_for("clean_breakout").earnings_context is (
            EarningsContext.UNKNOWN_EVENT_CONTEXT
        )

    def test_a_supplied_calendar_produces_a_real_answer(self):
        scenario = GENERATOR.clean_breakout(seed=0)
        index = scenario.breakout_index or 0
        event = run_scenario(
            scenario,
            context=BreakoutContext(
                earnings_calendar_available=True,
                earnings_sessions=(scenario.bars[index].session_date,),
            ),
        )
        assert event.earnings_context in (
            EarningsContext.EARNINGS_EVENT_NEARBY,
            EarningsContext.POST_EARNINGS_GAP,
        )


class TestEngineGuards:
    def test_a_second_observation_for_one_session_is_refused(self):
        """A history with a duplicated session cannot be replayed against the
        prefix that produced it."""
        scenario = GENERATOR.clean_breakout(seed=0)
        bars = list(scenario.bars)
        engine = BreakoutEngine()
        from tradeit.breakouts.validation import build_boundary

        boundary = build_boundary(
            scenario, config=engine.config, spec=BoundarySpec(), warmup_index=45
        )
        event = engine.open_event(
            instrument_id=1,
            timeframe=bars[0].timeframe,
            boundary=boundary,
            session=bars[45].session_date,
        )
        inputs = SessionInputs(
            bars=bars[:47],
            as_of_session=bars[46].session_date,
            knowledge_time=dt.datetime.combine(bars[46].session_date, dt.time(22), tzinfo=UTC),
        )
        event = engine.advance(event, inputs)
        with pytest.raises(ConfigError, match="prefix replay"):
            engine.advance(event, inputs)

    def test_bars_after_the_evaluation_session_are_refused(self):
        """The guard that catches an unsorted series.

        A sorted series trips the end-of-series check first; this one exists for
        a later bar hiding in the middle, which is what an accidental
        concatenation of two windows looks like.
        """
        scenario = GENERATOR.clean_breakout(seed=0)
        bars = list(scenario.bars)
        scrambled = [*bars[:40], bars[80], bars[40]]
        with pytest.raises(ConfigError, match="after the evaluation session"):
            SessionInputs(
                bars=scrambled,
                as_of_session=bars[40].session_date,
                knowledge_time=dt.datetime.combine(bars[40].session_date, dt.time(22), tzinfo=UTC),
            )

    def test_a_series_that_does_not_end_on_the_session_is_refused(self):
        scenario = GENERATOR.clean_breakout(seed=0)
        bars = list(scenario.bars)
        with pytest.raises(ConfigError, match="score the wrong day"):
            SessionInputs(
                bars=bars[:50],
                as_of_session=bars[60].session_date,
                knowledge_time=dt.datetime.combine(bars[60].session_date, dt.time(22), tzinfo=UTC),
            )

    def test_advancing_a_terminal_event_is_a_no_op(self):
        event = event_for("false_breakout")
        engine = BreakoutEngine()
        scenario = GENERATOR.false_breakout(seed=0)
        inputs = SessionInputs(
            bars=list(scenario.bars),
            as_of_session=scenario.bars[-1].session_date,
            knowledge_time=dt.datetime.combine(
                scenario.bars[-1].session_date, dt.time(22), tzinfo=UTC
            ),
        )
        assert engine.advance(event, inputs) is event


class TestReproducibility:
    def test_the_same_series_produces_the_same_event(self):
        first = run_scenario(GENERATOR.clean_breakout(seed=3))
        second = run_scenario(GENERATOR.clean_breakout(seed=3))
        assert first.to_payload() == second.to_payload()

    def test_a_different_seed_produces_an_independent_sample(self):
        first = run_scenario(GENERATOR.clean_breakout(seed=3))
        second = run_scenario(GENERATOR.clean_breakout(seed=11))
        assert first.to_payload() != second.to_payload()

    def test_a_spec_change_moves_the_result(self):
        quiet = run_scenario(GENERATOR.build(BreakoutSpec(breakout_volume_multiple=0.8), name="q"))
        loud = run_scenario(GENERATOR.build(BreakoutSpec(breakout_volume_multiple=3.5), name="l"))
        assert loud.breakout_quality > quiet.breakout_quality
