"""A 536-session hole is not a gap in one history. It is two histories.

The snapshot that motivated this holds 3,638 bars under the ticker ``BBBY``
running 2010 to 2026, with a hole from 2023-07-05 to 2025-08-21. Bed Bath &
Beyond stopped trading in May 2023. These tests pin the boundary between "state
is preserved" and "state is reset", and the reasoning behind each side of it.
"""

from __future__ import annotations

import datetime as dt

from tradeit.core.calendar import get_calendar
from tradeit.scanning.episodes import BREAK_SESSIONS, segment_sessions
from tradeit.validation.continuity import STRUCTURAL_RUN_SESSIONS

CALENDAR = get_calendar()


def sessions(start: dt.date, end: dt.date) -> list[dt.date]:
    return CALENDAR.sessions_between(start, end)


def without(all_sessions: list[dt.date], start: dt.date, end: dt.date) -> list[dt.date]:
    removed = set(sessions(start, end))
    return [day for day in all_sessions if day not in removed]


class TestTheRealCase:
    def test_bbby_splits_into_two_episodes_at_the_reported_break(self) -> None:
        observed = without(
            sessions(dt.date(2010, 1, 4), dt.date(2026, 8, 7)),
            dt.date(2023, 7, 5),
            dt.date(2025, 8, 21),
        )
        episodes = segment_sessions(observed)
        assert len(episodes) == 2
        first, second = episodes
        assert first.start == dt.date(2010, 1, 4)
        assert first.end < dt.date(2023, 7, 5)
        assert second.start > dt.date(2025, 8, 21)
        assert second.end == dt.date(2026, 8, 7)
        assert second.break_sessions == 536, "the gate reported 536; the scanner must agree"
        assert second.is_continuation

    def test_no_session_belongs_to_two_episodes_and_none_is_invented(self) -> None:
        """No bar is manufactured and none is counted twice."""
        observed = without(
            sessions(dt.date(2010, 1, 4), dt.date(2026, 8, 7)),
            dt.date(2023, 7, 5),
            dt.date(2025, 8, 21),
        )
        episodes = segment_sessions(observed)
        rebuilt = [day for episode in episodes for day in episode.sessions]
        assert rebuilt == observed
        assert len(set(rebuilt)) == len(rebuilt)


class TestWhereTheBoundaryIs:
    def test_the_scanner_and_the_gate_use_one_constant(self) -> None:
        """A gap the gate calls structural is a gap the scanner resets on."""
        assert BREAK_SESSIONS == STRUCTURAL_RUN_SESSIONS

    def test_a_gap_one_session_short_of_the_threshold_preserves_state(self) -> None:
        window = sessions(dt.date(2020, 1, 2), dt.date(2021, 12, 31))
        start = window[100]
        end = window[100 + BREAK_SESSIONS - 2]
        assert len(segment_sessions(without(window, start, end))) == 1

    def test_a_gap_exactly_at_the_threshold_resets(self) -> None:
        window = sessions(dt.date(2020, 1, 2), dt.date(2021, 12, 31))
        start = window[100]
        end = window[100 + BREAK_SESSIONS - 1]
        assert len(segment_sessions(without(window, start, end))) == 2

    def test_holidays_and_weekends_are_never_breaks(self) -> None:
        """Measured in sessions the exchange held, not in calendar days.

        A Thanksgiving week or a Christmas stretch spans many calendar days and
        few sessions; treating the calendar difference as the gap would reset
        every instrument several times a year.
        """
        window = sessions(dt.date(2019, 1, 2), dt.date(2021, 12, 31))
        assert len(segment_sessions(window)) == 1

    def test_scattered_absences_do_not_reset_state(self) -> None:
        """The NKLA shape: 31 missing sessions across 11 runs over five years.

        Absent observations inside a continuous listing. Resetting on these
        would report each missing print as a new security, inflate the count of
        distinct patterns, and make any rate computed over them meaningless.
        """
        window = sessions(dt.date(2020, 6, 4), dt.date(2025, 12, 18))
        observed = [day for index, day in enumerate(window) if index % 43 not in (0, 1, 2)]
        assert len(window) - len(observed) > 31, "fixture must remove more than NKLA did"
        assert len(segment_sessions(observed)) == 1

    def test_one_missing_session_from_a_quarantined_bar_is_not_a_break(self) -> None:
        # Each of the four quarantined rows removes exactly one session from
        # its instrument's series. None of them can start a new episode.
        window = sessions(dt.date(2012, 1, 3), dt.date(2023, 12, 29))
        for date in (dt.date(2012, 5, 23), dt.date(2021, 5, 5), dt.date(2023, 1, 24)):
            observed = [day for day in window if day != date]
            assert len(segment_sessions(observed)) == 1


class TestDegenerateInputs:
    def test_no_sessions_gives_no_episodes(self) -> None:
        assert segment_sessions([]) == []

    def test_one_session_gives_one_episode(self) -> None:
        day = dt.date(2021, 3, 4)
        episodes = segment_sessions([day])
        assert len(episodes) == 1
        assert episodes[0].start == episodes[0].end == day
        assert episodes[0].break_sessions == 0

    def test_three_breaks_give_four_episodes_each_carrying_its_own_gap(self) -> None:
        window = sessions(dt.date(2015, 1, 2), dt.date(2021, 12, 31))
        observed = window
        for offset in (200, 600, 1000):
            observed = without(
                observed, observed_at(window, offset), observed_at(window, offset + 40)
            )
        episodes = segment_sessions(observed)
        assert len(episodes) == 4
        assert episodes[0].break_sessions == 0
        assert all(e.break_sessions >= BREAK_SESSIONS for e in episodes[1:])


def observed_at(window: list[dt.date], index: int) -> dt.date:
    return window[index]
