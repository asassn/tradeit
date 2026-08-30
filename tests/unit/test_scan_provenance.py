"""Three dates, three questions, and the one that used to answer all of them.

``phase4.causality`` reported 5,849 of 156,433 patterns as detected before their
own evidence. Re-running each detector over exactly the bar prefix available on
the failing session reproduced the geometry with an end date *on* the detection
session every time — the detectors were causal, and the check was comparing
against a field that moves after detection.

These pin the distinction so it cannot quietly collapse back into one field.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.base import Boundary, PatternGeometry, PatternInstance, PatternState
from tradeit.patterns.lifecycle import StateTransition, TransitionReason
from tradeit.patterns.persistence import _known_through
from tradeit.patterns.tracking import PatternTracker, TrackedPattern
from tradeit.validation.scope import ScanScope

FIRST = dt.date(2026, 2, 10)
LATER = dt.date(2026, 5, 20)
STRUCTURE_START = dt.date(2026, 1, 5)


def make_instance(
    *,
    start: dt.date = STRUCTURE_START,
    end: dt.date,
    session: dt.date | None = None,
    state: PatternState = PatternState.MATURE,
) -> PatternInstance:
    return PatternInstance(
        instrument_id=1,
        pattern_type=PatternType.BULL_FLAG,
        timeframe=Bartimeframe.D1,
        state=state,
        geometry=PatternGeometry(
            start_date=start,
            end_date=end,
            resistance=Boundary(kind="resistance", method="swing", level=12.0, anchor_date=end),
            support=Boundary(kind="support", method="swing", level=10.0, anchor_date=start),
        ),
        as_of_session=session or end,
        knowledge_time=dt.datetime.combine(session or end, dt.time(21), tzinfo=dt.UTC),
        quality=70.0,
        detector_name="bull_flag",
    )


def transition(session: dt.date, structure_end: dt.date | None) -> StateTransition:
    return StateTransition(
        session_date=session,
        from_state=None,
        to_state=PatternState.MATURE,
        reason=TransitionReason.DETECTED,
        quality=70.0,
        structure_end=structure_end,
    )


class TestTheDetectorCannotSeeAFutureBar:
    """Why the 5,849 were never a causality failure.

    A ``PatternInstance`` refuses at construction to carry geometry ending after
    the session it was detected on. Every detection is therefore causal *by
    construction*, first detection included — so a persisted
    ``structure_known_through`` can never exceed ``first_detected_session``, and
    the check comparing them can only fail if this guard is removed.
    """

    def test_a_geometry_ending_after_its_own_session_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="not allowed to see"):
            make_instance(end=LATER, session=FIRST)

    def test_a_geometry_ending_on_its_own_session_is_allowed(self) -> None:
        assert make_instance(end=FIRST, session=FIRST).geometry.end_date == FIRST

    def test_the_field_the_check_reads_can_never_exceed_the_detection_session(self) -> None:
        instance = make_instance(end=FIRST, session=FIRST)
        tracked = TrackedPattern(
            identity_key="k",
            instrument_id=1,
            pattern_type=instance.pattern_type,
            current=instance,
            first_seen=FIRST,
            last_seen=FIRST,
            history=(transition(FIRST, instance.geometry.end_date),),
        )
        assert _known_through(tracked) <= tracked.first_seen


class TestWhatWasKnownAtDetection:
    def test_it_is_read_from_the_first_transition_not_the_latest_measurement(self) -> None:
        """A pattern is often persisted after several sessions of tracking.

        ``tracked.current`` is by then a later measurement, so taking the value
        from it would reintroduce exactly the retrospective smearing the column
        exists to prevent.
        """
        instance = make_instance(start=dt.date(2026, 1, 5), end=LATER)
        tracked = TrackedPattern(
            identity_key="k",
            instrument_id=1,
            pattern_type=instance.pattern_type,
            current=instance,
            first_seen=FIRST,
            last_seen=LATER,
            history=(transition(FIRST, FIRST), transition(LATER, LATER)),
        )
        assert _known_through(tracked) == FIRST
        assert tracked.current.geometry.end_date == LATER

    def test_a_pattern_saved_on_its_birth_session_has_the_two_agree(self) -> None:
        instance = make_instance(start=dt.date(2026, 1, 5), end=FIRST)
        tracked = TrackedPattern(
            identity_key="k",
            instrument_id=1,
            pattern_type=instance.pattern_type,
            current=instance,
            first_seen=FIRST,
            last_seen=FIRST,
            history=(transition(FIRST, FIRST),),
        )
        assert _known_through(tracked) == instance.geometry.end_date == FIRST

    def test_a_history_written_before_the_field_existed_falls_back(self) -> None:
        instance = make_instance(start=dt.date(2026, 1, 5), end=LATER)
        tracked = TrackedPattern(
            identity_key="k",
            instrument_id=1,
            pattern_type=instance.pattern_type,
            current=instance,
            first_seen=FIRST,
            last_seen=LATER,
            history=(transition(FIRST, None),),
        )
        assert _known_through(tracked) == LATER


class TestTheTrackerRecordsItPerSession:
    def test_every_transition_carries_the_structure_end_measured_that_session(self) -> None:
        tracker = PatternTracker()
        first = make_instance(start=dt.date(2026, 1, 5), end=FIRST, session=FIRST)
        tracker.observe([first], FIRST)
        second = make_instance(start=dt.date(2026, 1, 5), end=LATER, session=LATER)
        tracker.observe([second], LATER)

        tracked = tracker.get(first.identity_key)
        assert tracked is not None
        ends = [t.structure_end for t in tracked.history]
        assert ends == [FIRST, LATER], (
            "the history must record what was measured on each session, not the "
            "latest measurement repeated"
        )


class TestTheScanScope:
    """`diag-01` covered 7 instruments inside a 78-instrument snapshot."""

    def test_instrument_years_sums_each_instrument_over_its_own_span(self) -> None:
        """Not `(latest - earliest) * count`.

        That form credits an instrument listed in 2020 with a decade of history
        because something else in the universe had one, inflating the
        denominator of every per-year rate — and inflating it most for exactly
        the young, volatile names such a rate is most interested in.
        """
        scope = ScanScope(
            snapshot=frozenset({1, 2, 3}),
            requested=frozenset({1, 2}),
            completed=frozenset({1, 2}),
            runs=(("diag-01", "completed"),),
            spans={
                1: (dt.date(2010, 1, 4), dt.date(2020, 1, 3)),
                2: (dt.date(2019, 1, 2), dt.date(2020, 1, 3)),
            },
        )
        naive = (dt.date(2020, 1, 3) - dt.date(2010, 1, 4)).days / 365.25 * 2
        assert scope.instrument_years() == pytest.approx(10.0 + 1.0, abs=0.05)
        assert scope.instrument_years() < naive

    def test_an_unscanned_instrument_is_not_a_silent_detector(self) -> None:
        scope = ScanScope(
            snapshot=frozenset(range(1, 79)),
            requested=frozenset({1, 2, 3, 4, 5, 6, 7}),
            completed=frozenset({1, 2, 3, 4, 5, 6, 7}),
            runs=(("diag-01", "completed"),),
        )
        assert len(scope.unscanned) == 71
        assert scope.requested_but_incomplete == frozenset()
        assert scope.describe()["completed_instruments"] == 7

    def test_an_interrupted_instrument_is_neither_scanned_nor_ignored(self) -> None:
        scope = ScanScope(
            snapshot=frozenset({1, 2, 3}),
            requested=frozenset({1, 2, 3}),
            completed=frozenset({1}),
            runs=(("diag-01", "failed"),),
        )
        assert scope.requested_but_incomplete == frozenset({2, 3})
        assert scope.unscanned == frozenset()

    def test_no_ledger_means_unknown_rather_than_the_snapshot(self) -> None:
        empty = ScanScope(
            snapshot=frozenset({1, 2}), requested=frozenset({1, 2}), completed=frozenset({1, 2})
        )
        assert not empty.is_known
