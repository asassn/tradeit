"""The common calendar grid, and what it refuses to invent.

Each test names a way a sampler can quietly fabricate a cross-section: moving a
security to its nearest print, measuring securities on one date over different
windows, or reading past the end of the registered period.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.core.calendar import get_calendar
from tradeit.signals.sampling import Outcome, common_grid, grid_points, outcome_or_terminal

CAL = get_calendar()
START, END = dt.date(2010, 1, 4), dt.date(2011, 12, 30)


@pytest.fixture(scope="module")
def sessions() -> list[dt.date]:
    return CAL.sessions_between(START, END)


class TestTheGridIsTheCalendars:
    def test_every_stride_th_exchange_session(self, sessions: list[dt.date]) -> None:
        grid = common_grid(CAL, START, END, 21, 63)
        assert grid.dates[0] == sessions[0]
        assert grid.dates[1] == sessions[21]
        assert all(d in sessions for d in grid.dates)

    def test_outcome_is_exactly_horizon_exchange_sessions_on(self, sessions: list[dt.date]) -> None:
        grid = common_grid(CAL, START, END, 21, 63)
        for date, target in zip(grid.dates, grid.outcome_dates, strict=True):
            assert sessions.index(target) - sessions.index(date) == 63

    def test_a_holiday_is_skipped_by_the_calendar_not_counted(self) -> None:
        """Hurricane Sandy closed the exchange on 2012-10-29 and -30. A sampler
        counting calendar days, or a fallback calendar that only knows fixed
        holidays, would count them as sessions."""
        grid = common_grid(CAL, dt.date(2012, 10, 25), dt.date(2012, 11, 30), 1, 3)
        assert dt.date(2012, 10, 29) not in grid.dates
        assert dt.date(2012, 10, 30) not in grid.dates
        first = grid.dates.index(dt.date(2012, 10, 26))
        # Friday the 26th, three sessions on: 31st, 1st, 2nd -- not the 29th.
        assert grid.outcome_dates[first] == dt.date(2012, 11, 2)

    def test_no_outcome_is_measured_past_the_registered_end(self) -> None:
        """For a 2010-2019 study the last grid dates would otherwise be measured
        into 2020, reading data the registration never named."""
        grid = common_grid(CAL, START, END, 21, 63)
        assert max(grid.outcome_dates) <= END
        assert grid.outcome_dates[-1] > grid.dates[-1]


class TestNothingIsInvented:
    def test_complete_securities_are_sampled_on_identical_dates(
        self, sessions: list[dt.date]
    ) -> None:
        """The property the whole module exists for. Two securities that listed
        at different times, under the old per-security grids, never shared a
        sample date. On the common grid, any two that printed share them all."""
        grid = common_grid(CAL, START, END, 21, 63)
        early = grid_points(sessions, grid)
        late = grid_points(sessions[5:], grid)
        shared = {p.session_date for p in early} & {p.session_date for p in late}
        assert len(shared) == len(late)
        assert all(p.session_date in grid.dates for p in late)

    def test_a_missing_grid_print_is_skipped_not_moved(self, sessions: list[dt.date]) -> None:
        """Moving to the next print would read a day the grid date had not seen;
        moving to the previous would measure a different window from everyone
        else's. Neither is allowed."""
        grid = common_grid(CAL, START, END, 21, 63)
        # Index 1, not 3: with stride 21 and horizon 63, every grid date from
        # index 3 on is also the OUTCOME session of the date three before it,
        # so removing one would drop two points and blur what is being tested.
        missing = grid.dates[1]
        assert missing not in grid.outcome_dates
        bars = [d for d in sessions if d != missing]
        points = grid_points(bars, grid)
        assert missing not in {p.session_date for p in points}
        # Exactly one point lost, and every surviving point's signal bar IS its
        # grid date. A sampler that moved to a neighbouring print would keep the
        # point and record the grid date against a bar from another day -- which
        # only this second check can see, because the recorded date alone would
        # look correct.
        assert len(points) == len(grid_points(sessions, grid)) - 1
        assert all(bars[p.signal_index] == p.session_date for p in points)

    def test_a_missing_outcome_print_drops_the_point(self, sessions: list[dt.date]) -> None:
        grid = common_grid(CAL, START, END, 21, 63)
        bars = [d for d in sessions if d != grid.outcome_dates[2]]
        assert grid.dates[2] not in {p.session_date for p in grid_points(bars, grid)}

    def test_a_gap_inside_the_window_does_not_stretch_it(self, sessions: list[dt.date]) -> None:
        """The old samplers counted the security's own bars, so a security that
        skipped five sessions mid-window was measured 68 exchange sessions out
        while its neighbours were measured 63. Here the outcome bar is the same
        exchange session for everyone, and the gap is simply not needed."""
        grid = common_grid(CAL, START, END, 21, 63)
        date = grid.dates[4]
        start = sessions.index(date)
        gapped = sessions[: start + 10] + sessions[start + 15 :]
        point = next(p for p in grid_points(gapped, grid) if p.session_date == date)
        assert gapped[point.outcome_index] == grid.outcome_dates[4]
        # Five fewer of its own bars lie between the endpoints, and that is fine.
        assert point.outcome_index - point.signal_index == 63 - 5

    def test_history_is_counted_in_the_securitys_own_bars(self, sessions: list[dt.date]) -> None:
        grid = common_grid(CAL, START, END, 21, 63)
        points = grid_points(sessions, grid, min_history=100)
        assert all(p.signal_index + 1 >= 100 for p in points)
        assert points[0].session_date == grid.dates[5]


class TestArguments:
    @pytest.mark.parametrize("stride, horizon", [(0, 63), (21, 0)])
    def test_nonpositive_arguments_are_refused(self, stride: int, horizon: int) -> None:
        with pytest.raises(ValueError):
            common_grid(CAL, START, END, stride, horizon)


class TestOutcomeOrTerminal:
    """A year-long outcome must keep the companies that died during the year."""

    DATES = tuple(dt.date(2020, 1, d) for d in (2, 3, 6, 7, 8, 9, 10))

    def test_a_traded_bar_on_the_outcome_session_is_the_outcome(self) -> None:
        traded = [True] * 7
        assert outcome_or_terminal(self.DATES, traded, 0, dt.date(2020, 1, 8)) == Outcome(4, False)

    def test_a_company_that_stops_trading_is_kept_as_terminal(self) -> None:
        """The case ``grid_points`` drops: no print on the outcome session, ever again."""
        dates, traded = list(self.DATES[:4]), [True] * 4
        assert outcome_or_terminal(dates, traded, 0, dt.date(2020, 1, 10)) == Outcome(3, True)

    def test_placeholder_bars_after_death_do_not_make_it_alive(self) -> None:
        """A vendor that carries a dead company forward with zero volume (§0.7)."""
        traded = [True, True, True, False, False, False, False]
        assert outcome_or_terminal(self.DATES, traded, 0, dt.date(2020, 1, 9)) == Outcome(2, True)

    def test_a_gap_on_the_outcome_session_is_dropped_not_moved(self) -> None:
        """Untraded on the day but trading after: alive, and no drift to a neighbour."""
        traded = [True, True, True, True, False, True, True]
        assert outcome_or_terminal(self.DATES, traded, 0, dt.date(2020, 1, 8)) is None
        missing = [d for d in self.DATES if d != dt.date(2020, 1, 8)]
        assert outcome_or_terminal(missing, [True] * 6, 0, dt.date(2020, 1, 8)) is None

    def test_death_on_the_signal_bar_is_terminal_at_the_signal_bar(self) -> None:
        traded = [True, True, False, False, False, False, False]
        assert outcome_or_terminal(self.DATES, traded, 1, dt.date(2020, 1, 9)) == Outcome(1, True)

    def test_an_untraded_signal_bar_is_refused(self) -> None:
        with pytest.raises(ValueError):
            outcome_or_terminal(self.DATES, [False] + [True] * 6, 0, dt.date(2020, 1, 8))
