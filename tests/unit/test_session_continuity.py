"""536 missing sessions in one block and 536 scattered are different defects.

The check used to report only the count. These tests pin the distinction it now
draws, and — more importantly — pin the boundary rule: sessions before a
security listed and after it delisted are never counted as missing.
"""

from __future__ import annotations

import datetime as dt

from tradeit.core.calendar import get_calendar
from tradeit.validation.continuity import (
    STRUCTURAL_RUN_SESSIONS,
    GapShape,
    analyse_series,
)

CALENDAR = get_calendar()
WINDOW = CALENDAR.sessions_between(dt.date(2015, 1, 2), dt.date(2020, 12, 31))


def analyse(observed: list[dt.date]):
    expected = CALENDAR.sessions_between(min(observed), max(observed))
    return analyse_series(13, "TEST", observed, expected)


class TestTheListingIntervalBoundsTheExpectation:
    def test_a_security_that_listed_late_is_not_missing_the_years_before_it(self) -> None:
        """The rule that must hold whatever else changes.

        A 2018 IPO measured against a window opening in 2015 would otherwise
        report three years of "missing" sessions for a company that did not
        exist.
        """
        listed = [d for d in WINDOW if d >= dt.date(2018, 6, 1)]
        result = analyse(listed)
        assert result.missing_sessions == 0
        assert result.shape is GapShape.COMPLETE
        assert result.first_bar >= dt.date(2018, 6, 1)
        assert result.expected_sessions == len(listed)

    def test_a_security_that_delisted_early_is_not_missing_the_years_after_it(self) -> None:
        delisted = [d for d in WINDOW if d <= dt.date(2017, 3, 9)]
        result = analyse(delisted)
        assert result.missing_sessions == 0
        assert result.shape is GapShape.COMPLETE

    def test_a_security_that_both_listed_late_and_delisted_early(self) -> None:
        span = [d for d in WINDOW if dt.date(2017, 1, 3) <= d <= dt.date(2019, 4, 12)]
        result = analyse(span)
        assert result.missing_sessions == 0
        assert result.expected_sessions == len(span)

    def test_a_complete_series_over_the_whole_window_has_no_gaps(self) -> None:
        result = analyse(list(WINDOW))
        assert result.missing_sessions == 0
        assert result.runs == ()


class TestTheShapeOfTheHoles:
    def test_one_long_absence_is_a_structural_break(self) -> None:
        removed = set(WINDOW[400:700])
        result = analyse([d for d in WINDOW if d not in removed])
        assert result.missing_sessions == 300
        assert result.shape is GapShape.STRUCTURAL
        assert result.shape.breaks_the_series
        longest = result.longest_run
        assert longest is not None
        assert longest.sessions == 300
        assert longest.start == WINDOW[400]
        assert longest.end == WINDOW[699]
        assert "spliced" in result.diagnosis

    def test_many_short_absences_are_scattered(self) -> None:
        removed = {WINDOW[i] for i in range(10, 900, 7)}
        result = analyse([d for d in WINDOW if d not in removed])
        assert result.missing_sessions == len(removed)
        assert result.shape is GapShape.SCATTERED
        assert not result.shape.breaks_the_series
        assert len(result.runs) == len(removed), "each absence is its own run"
        assert "thin liquidity" in result.diagnosis

    def test_the_same_total_gives_two_different_findings(self) -> None:
        """The point of the whole module, in one assertion."""
        block = set(WINDOW[300:400])
        spread = {WINDOW[i] for i in range(5, 1005, 10)}
        assert len(block) == len(spread) == 100

        as_block = analyse([d for d in WINDOW if d not in block])
        as_spread = analyse([d for d in WINDOW if d not in spread])
        assert as_block.missing_sessions == as_spread.missing_sessions
        assert as_block.shape is not as_spread.shape

    def test_a_long_hole_with_a_few_stray_days_is_still_one_break(self) -> None:
        removed = set(WINDOW[400:600]) | {WINDOW[900], WINDOW[950]}
        result = analyse([d for d in WINDOW if d not in removed])
        assert result.shape is GapShape.STRUCTURAL

    def test_a_long_hole_plus_substantial_scatter_is_mixed(self) -> None:
        removed = set(WINDOW[400:440]) | {WINDOW[i] for i in range(700, 900, 4)}
        result = analyse([d for d in WINDOW if d not in removed])
        assert result.shape is GapShape.MIXED
        assert result.shape.breaks_the_series

    def test_the_structural_threshold_is_where_it_is_declared(self) -> None:
        just_under = set(WINDOW[100 : 100 + STRUCTURAL_RUN_SESSIONS - 1])
        just_over = set(WINDOW[100 : 100 + STRUCTURAL_RUN_SESSIONS])
        assert analyse([d for d in WINDOW if d not in just_under]).shape is GapShape.SCATTERED
        assert analyse([d for d in WINDOW if d not in just_over]).shape is GapShape.STRUCTURAL


class TestRunBoundaries:
    def test_two_holes_separated_by_one_session_stay_two_runs(self) -> None:
        removed = set(WINDOW[100:110]) | set(WINDOW[111:120])
        result = analyse([d for d in WINDOW if d not in removed])
        assert len(result.runs) == 2
        assert [r.sessions for r in result.runs] == [10, 9]

    def test_a_hole_running_to_the_last_expected_session_is_closed(self) -> None:
        # A gap ending at the final expected session must still be emitted; the
        # loop that walks the calendar has to flush the run it is holding.
        observed = [*WINDOW[:-5], WINDOW[-1]]
        result = analyse(observed)
        assert result.missing_sessions == 4
        assert len(result.runs) == 1
        assert result.runs[0].end == WINDOW[-2]

    def test_runs_are_reported_in_calendar_order(self) -> None:
        removed = set(WINDOW[500:520]) | set(WINDOW[100:105])
        result = analyse([d for d in WINDOW if d not in removed])
        assert [r.start for r in result.runs] == sorted(r.start for r in result.runs)

    def test_the_payload_carries_every_run(self) -> None:
        removed = set(WINDOW[200:210]) | set(WINDOW[400:405])
        result = analyse([d for d in WINDOW if d not in removed])
        payload = result.to_payload()
        assert payload["missing_sessions"] == 15
        assert len(payload["gap_runs"]) == 2
        assert payload["ticker"] == "TEST"
        assert payload["shape"] == str(result.shape)

    def test_the_rendering_names_the_symbol_not_only_the_surrogate_key(self) -> None:
        removed = set(WINDOW[200:400])
        result = analyse([d for d in WINDOW if d not in removed])
        text = "\n".join(result.render())
        assert "TEST" in text
        assert "instrument 13" in text
        assert "structural_break" in text
