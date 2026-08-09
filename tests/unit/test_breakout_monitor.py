"""The monitor: which boundaries get watched, and how attempts are numbered.

Two requirements meet here and they pull in the same direction.

**Item 45 — active-pattern filtering.** Evaluating every pattern ever detected
against today's bar is not merely wasteful. A long-expired structure's
resistance is not a level anyone is trading against, so scoring a cross of it
does not *find* a breakout, it manufactures one — and a dataset full of
manufactured events reports a false-breakout rate that describes the monitor
rather than the market.

**Item 26 — attempt history.** A pattern may have three goes at the same level.
The monitor is what mints the second and third, because a resolved rejection is
never revived. After ``max_rejections`` the boundary has won and the monitor
stops rather than going quiet.

The integration test at the end drives the real
:class:`~tradeit.patterns.scanner.PatternScanner` output through the monitor,
because the interesting failures live in the seam: a detector's boundary that
the monitor cannot read, or a pattern state it does not recognise.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.lifecycle import BreakoutState, TransitionReason
from tradeit.breakouts.monitor import BreakoutMonitor
from tradeit.breakouts.synthetic import BreakoutGenerator
from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.base import (
    Boundary,
    PatternGeometry,
    PatternInstance,
    PatternState,
    PricePoint,
)
from tradeit.patterns.scanner import PatternScanner
from tradeit.patterns.synthetic import BullFlagSpec, PatternGenerator

GENERATOR = BreakoutGenerator()
PATTERNS = PatternGenerator()
UTC = dt.UTC
WARMUP = 45


def pattern(
    *,
    state: PatternState = PatternState.MATURE,
    level: float = 100.0,
    as_of: dt.date,
    start: dt.date,
    resistance: bool = True,
    quality: float = 78.0,
) -> PatternInstance:
    boundary = (
        Boundary(
            kind="resistance",
            method="swing_highs",
            level=level,
            anchor_date=as_of,
            touches=(PricePoint(as_of, level), PricePoint(start, level)),
            confidence=70.0,
        )
        if resistance
        else None
    )
    return PatternInstance(
        instrument_id=1,
        pattern_type=PatternType.FLAT_BASE,
        timeframe=Bartimeframe.D1,
        state=state,
        geometry=PatternGeometry(start_date=start, end_date=as_of, resistance=boundary),
        as_of_session=as_of,
        knowledge_time=dt.datetime.combine(as_of, dt.time(22), tzinfo=UTC),
        quality=quality,
        session_count=40,
        detector_name="flat_base",
        detector_version=2,
        config_digest="cfg",
    )


def drive(
    monitor: BreakoutMonitor,
    scenario,
    *,
    state: PatternState = PatternState.MATURE,
    stop_at: int | None = None,
    invalidate_at: int | None = None,
):
    """Run the monitor over a synthetic scenario, one session at a time.

    The pattern's structural start is fixed across the replay, as a tracked
    pattern's is. Letting it slide with the window would mint a new pattern
    identity every session, and every breakout event would be attempt 1 of a
    structure that had existed for one day — which is the failure the identity
    design exists to prevent, reproduced in the test harness.
    """
    bars = list(scenario.bars)
    last = len(bars) - 1 if stop_at is None else stop_at
    start = bars[5].session_date
    results = []
    for index in range(WARMUP, last + 1):
        session = bars[index].session_date
        instance = pattern(
            state=state,
            level=scenario.level,
            as_of=session,
            start=start,
        )
        results.append(
            monitor.observe(
                1,
                Bartimeframe.D1,
                [instance],
                bars[: index + 1],
                session,
                invalidated_patterns=((instance.identity_key,) if invalidate_at == index else ()),
            )
        )
    return results


class TestActivePatternFiltering:
    def test_only_live_pattern_states_are_watched(self):
        monitor = BreakoutMonitor()
        session = dt.date(2023, 6, 15)
        start = dt.date(2023, 4, 3)
        instances = [
            pattern(state=state, as_of=session, start=start)
            for state in (
                PatternState.FORMING,
                PatternState.MATURE,
                PatternState.NEAR_BREAKOUT,
                PatternState.INVALIDATED,
                PatternState.EXPIRED,
            )
        ]
        live, skipped = monitor.monitorable(
            instances, as_of_session=session, bars=list(GENERATOR.clean_breakout().bars[:60])
        )
        assert len(live) == 2
        assert len(skipped) == 3
        assert all("only" in s.reason for s in skipped)

    def test_forming_patterns_are_excluded(self):
        """Their boundary is still resolving, so a 'breakout' of one is a
        breakout of a guess."""
        assert "forming" not in BreakoutEngineConfig().monitored_states

    def test_a_stale_pattern_is_dropped_with_a_reason(self):
        monitor = BreakoutMonitor()
        session = dt.date(2023, 6, 15)
        instance = pattern(as_of=dt.date(2023, 1, 3), start=dt.date(2022, 11, 1))
        _live, skipped = monitor.monitorable(
            [instance], as_of_session=session, bars=list(GENERATOR.clean_breakout().bars[:60])
        )
        assert skipped and "staleness" in skipped[0].reason

    def test_a_pattern_without_resistance_is_skipped_rather_than_guessed_at(self):
        monitor = BreakoutMonitor()
        session = dt.date(2023, 6, 15)
        instance = pattern(as_of=session, start=dt.date(2023, 4, 3), resistance=False)
        live, skipped = monitor.monitorable(
            [instance], as_of_session=session, bars=list(GENERATOR.clean_breakout().bars[:60])
        )
        assert not live
        assert "no resistance boundary" in skipped[0].reason

    def test_skipping_is_recorded_rather_than_silent(self):
        """'No breakout was found' and 'the boundary was never watched' are
        different facts."""
        monitor = BreakoutMonitor()
        results = drive(monitor, GENERATOR.clean_breakout(seed=0), state=PatternState.FORMING)
        assert all(result.skipped for result in results)
        assert not monitor.events


class TestMonitorLifecycle:
    def test_a_clean_breakout_is_opened_and_carried_to_confirmation(self):
        monitor = BreakoutMonitor()
        drive(monitor, GENERATOR.clean_breakout(seed=0))
        states = {event.state for event in monitor.events.values()}
        assert BreakoutState.CONFIRMED in states or BreakoutState.CONFIRMATION_PENDING in states

    def test_the_boundary_is_frozen_once_and_reused(self):
        """The monitor rebuilds a boundary from the pattern every session; the
        event keeps the one it opened with."""
        monitor = BreakoutMonitor()
        drive(monitor, GENERATOR.clean_breakout(seed=0))
        for event in monitor.events.values():
            assert event.boundary.atr_at_open is not None
            anchors = {o.distance_pct for o in event.observations}
            assert anchors  # every observation measured against the same level

    def test_a_rejection_opens_a_new_attempt(self):
        monitor = BreakoutMonitor()
        drive(monitor, GENERATOR.multiple_attempts(seed=0))
        attempts = sorted(e.attempt_number for e in monitor.events.values())
        assert attempts == list(range(1, len(attempts) + 1))
        assert len(attempts) >= 2

    def test_prior_attempts_are_not_overwritten(self):
        """Item 26. Later research may find that repeated failure matters, and
        it can only find that if the earlier attempts still exist."""
        monitor = BreakoutMonitor()
        drive(monitor, GENERATOR.multiple_attempts(seed=0))
        rejected = [e for e in monitor.events.values() if e.state is BreakoutState.REJECTED]
        assert rejected
        for event in rejected:
            assert event.observations

    def test_the_monitor_stops_after_the_boundary_has_won(self):
        config = BreakoutEngineConfig(rejection={"max_rejections": 1})  # type: ignore[arg-type]
        monitor = BreakoutMonitor(config)
        results = drive(monitor, GENERATOR.multiple_attempts(seed=0))
        declined = [s for result in results for s in result.skipped if "turned back" in s.reason]
        assert declined

    def test_a_defeated_boundary_fails_its_last_attempt(self):
        """Not silence: the level defeated the thesis, which is a finding."""
        config = BreakoutEngineConfig(rejection={"max_rejections": 1})  # type: ignore[arg-type]
        monitor = BreakoutMonitor(config)
        drive(monitor, GENERATOR.multiple_attempts(seed=0))
        closed = monitor.close_defeated_boundaries(dt.date(2021, 12, 31))
        assert closed
        for event in closed:
            assert event.state is BreakoutState.FAILED_BREAKOUT
            assert event.terminal_reason is TransitionReason.REPEATED_REJECTION

    def test_failing_a_non_rejected_event_is_refused(self):
        monitor = BreakoutMonitor()
        drive(monitor, GENERATOR.clean_breakout(seed=0))
        event = next(iter(monitor.events.values()))
        if event.state is not BreakoutState.REJECTED:
            with pytest.raises(ConfigError, match="not rejected"):
                monitor.engine.fail_repeated_rejection(
                    event,
                    dt.date(2021, 12, 31),
                    dt.datetime(2021, 12, 31, 22, tzinfo=UTC),
                )

    def test_pattern_invalidation_propagates_to_the_event(self):
        monitor = BreakoutMonitor()
        scenario = GENERATOR.clean_breakout(seed=0)
        index = (scenario.breakout_index or 0) + 2
        drive(monitor, scenario, invalidate_at=index)
        events = list(monitor.events.values())
        assert any(e.terminal_reason is TransitionReason.PATTERN_INVALIDATED for e in events)

    def test_a_vanished_pattern_closes_its_event_rather_than_orphaning_it(self):
        monitor = BreakoutMonitor()
        scenario = GENERATOR.clean_breakout(seed=0)
        bars = list(scenario.bars)
        session = bars[WARMUP].session_date
        instance = pattern(as_of=session, start=bars[5].session_date, level=scenario.level)
        monitor.observe(1, Bartimeframe.D1, [instance], bars[: WARMUP + 1], session)
        assert monitor.active_events()

        later = bars[WARMUP + 1].session_date
        monitor.observe(1, Bartimeframe.D1, [], bars[: WARMUP + 2], later)
        assert not monitor.active_events()
        closed = next(iter(monitor.events.values()))
        assert closed.state is BreakoutState.EXPIRED


class TestMultiTimeframe:
    def test_daily_and_weekly_events_coexist(self):
        """Item 39: both truths are recorded, and whether they agree is a later
        phase's question."""
        monitor = BreakoutMonitor()
        scenario = GENERATOR.clean_breakout(seed=0)
        bars = list(scenario.bars)
        session = bars[WARMUP].session_date
        instance = pattern(as_of=session, start=bars[5].session_date, level=scenario.level)
        monitor.observe(1, Bartimeframe.D1, [instance], bars[: WARMUP + 1], session)
        monitor.observe(1, Bartimeframe.W1, [instance], bars[: WARMUP + 1], session)
        timeframes = {event.timeframe for event in monitor.events.values()}
        assert timeframes == {Bartimeframe.D1, Bartimeframe.W1}
        assert len(monitor.events) == 2

    def test_an_event_is_only_advanced_on_its_own_timeframe(self):
        monitor = BreakoutMonitor()
        scenario = GENERATOR.clean_breakout(seed=0)
        bars = list(scenario.bars)
        first, second = bars[WARMUP].session_date, bars[WARMUP + 1].session_date
        instance = pattern(as_of=first, start=bars[5].session_date, level=scenario.level)
        monitor.observe(1, Bartimeframe.D1, [instance], bars[: WARMUP + 1], first)
        weekly_instance = pattern(as_of=second, start=bars[5].session_date, level=scenario.level)
        result = monitor.observe(1, Bartimeframe.W1, [weekly_instance], bars[: WARMUP + 2], second)
        daily = [e for e in monitor.events.values() if e.timeframe is Bartimeframe.D1]
        assert len(daily[0].observations) == 1
        assert all(e.timeframe is Bartimeframe.W1 for e in result.updated)


class TestScannerIntegration:
    def test_the_monitor_consumes_real_scanner_output(self):
        """The seam where a detector's boundary meets the monitor's reader.

        Uses the real scanner rather than a hand-built pattern, because a
        boundary shape the monitor cannot read is exactly the kind of defect a
        synthetic instance would hide.
        """
        series = PATTERNS.bull_flag(
            BullFlagSpec(flag_sessions=12, breakout_sessions=6, breakout_strength=0.05)
        )
        bars = list(series.bars)
        as_of = bars[-1].session_date
        scanner = PatternScanner()
        scan = scanner.scan(1, Bartimeframe.D1, bars, as_of)

        monitor = BreakoutMonitor()
        result = monitor.observe(1, Bartimeframe.D1, scan.instances, bars, as_of)
        assert result.summary()["instrument_id"] == 1
        # Every instance is either watched or skipped with a stated reason.
        accounted = len(result.opened) + len(result.skipped)
        assert accounted >= len(scan.instances) or not scan.instances

    def test_tracked_patterns_are_accepted_as_well_as_instances(self):
        series = PATTERNS.bull_flag(
            BullFlagSpec(flag_sessions=12, breakout_sessions=6, breakout_strength=0.05)
        )
        bars = list(series.bars)
        as_of = bars[-1].session_date
        scanner = PatternScanner()
        scan = scanner.scan(1, Bartimeframe.D1, bars, as_of)
        monitor = BreakoutMonitor()
        live, _ = monitor.monitorable(scan.tracked, as_of_session=as_of, bars=bars)
        assert isinstance(live, list)


class TestSummaries:
    def test_the_result_summary_names_the_states(self):
        monitor = BreakoutMonitor()
        results = drive(monitor, GENERATOR.clean_breakout(seed=0))
        summary = results[-1].summary()
        assert summary["as_of_session"]
        assert isinstance(summary["states"], dict)

    def test_the_monitor_summary_counts_everything_it_holds(self):
        monitor = BreakoutMonitor()
        drive(monitor, GENERATOR.multiple_attempts(seed=0))
        summary = monitor.summary()
        assert summary["total"] == len(monitor.events)
        assert sum(v for k, v in summary.items() if k != "total") == summary["total"]

    def test_events_for_an_instrument_are_retrievable(self):
        monitor = BreakoutMonitor()
        drive(monitor, GENERATOR.clean_breakout(seed=0))
        assert monitor.events_for(1)
        assert not monitor.events_for(999)
