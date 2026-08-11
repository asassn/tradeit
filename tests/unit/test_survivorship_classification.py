"""A survivorship control is covered only when its usable history is here.

Two properties run through every test in this file, because both were violated
by the previous design:

* **A classification is not a pass.** Only ``COVERED`` counts, and only when
  real bars back it.
* **One universal request window is the wrong model.** Each control declares
  the history it needs; the acquisition is measured against *that*.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.data.validation_universe import (
    UniverseCategory,
    ValidationInstrument,
    default_universe,
)
from tradeit.validation.survivorship import (
    MIN_CONTROL_SESSIONS,
    AcquisitionOutcomeView,
    AcquisitionRecordView,
    ObservedSeries,
    SurvivorshipStatus,
    classify_roster,
    control_required_start,
    render_roster,
    required_history_start,
)

WINDOW_START = dt.date(2010, 1, 1)
WINDOW_END = dt.date(2026, 8, 7)


def control(
    ticker: str,
    last: dt.date,
    aliases: tuple[str, ...] = (),
    first: dt.date | None = None,
) -> ValidationInstrument:
    return ValidationInstrument(
        ticker=ticker,
        category=UniverseCategory.DELISTED,
        difficulty="bankruptcy",
        first_trade_date=first,
        last_trade_date=last,
        alias_candidates=aliases,
    )


def record(*rows: tuple[str, str, str]) -> AcquisitionRecordView:
    return AcquisitionRecordView(
        provider="test",
        requested_start=WINDOW_START,
        requested_end=WINDOW_END,
        outcomes=tuple(
            AcquisitionOutcomeView(ticker=t, symbol_status=s, fetch_status=f) for t, s, f in rows
        ),
    )


def good_series(ticker: str, last: dt.date, bars: int = 3000) -> ObservedSeries:
    """A history that genuinely reaches the end of the security's life."""
    return ObservedSeries(
        ticker=ticker,
        first_session=last - dt.timedelta(days=int(bars * 365.25 / 252)),
        last_session=last,
        bars=bars,
    )


def classify(instruments, **kwargs):
    kwargs.setdefault("present", set())
    kwargs.setdefault("record", AcquisitionRecordView())
    kwargs.setdefault("requested_start", WINDOW_START)
    kwargs.setdefault("requested_end", WINDOW_END)
    return classify_roster(
        instruments,
        kwargs.pop("present"),
        kwargs.pop("record"),
        **kwargs,
    )


class TestOnlyRealHistoryCounts:
    def test_only_covered_is_a_pass(self) -> None:
        for status in SurvivorshipStatus:
            assert status.is_covered is (status is SurvivorshipStatus.COVERED)

    def test_usable_bars_over_the_active_interval_are_coverage(self) -> None:
        last = dt.date(2023, 3, 9)
        roster = classify(
            [control("SIVB", last)],
            observed={"SIVB": good_series("SIVB", last)},
        )
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.COVERED
        assert entry.is_covered
        assert entry.provider_supplied_bars
        assert roster.uncovered == ()

    def test_a_ticker_row_with_too_few_bars_is_not_coverage(self) -> None:
        """The weakening this design exists to prevent.

        Forty bars proves nothing about what a screen would have seen a year
        before the collapse, and counting it would make the guarantee a
        formality.
        """
        last = dt.date(2023, 3, 9)
        roster = classify(
            [control("SIVB", last)],
            present={"SIVB"},
            observed={"SIVB": ObservedSeries("SIVB", last - dt.timedelta(days=60), last, 40)},
        )
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.INSUFFICIENT_HISTORY
        assert not entry.is_covered
        assert "40 bars" in entry.detail

    def test_a_series_that_stops_long_before_the_delisting_is_not_coverage(self) -> None:
        last = dt.date(2023, 3, 9)
        stops_early = ObservedSeries("SIVB", dt.date(2015, 1, 2), dt.date(2021, 6, 30), bars=1600)
        roster = classify([control("SIVB", last)], observed={"SIVB": stops_early})
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.INSUFFICIENT_HISTORY
        assert "before the recorded last trade" in entry.detail

    def test_a_final_print_a_few_sessions_early_is_still_coverage(self) -> None:
        # `last_trade_date` is a research figure and the vendor's final print
        # may legitimately be a few sessions earlier — a halt, or a last day
        # with no trade. That must not fail the control.
        last = dt.date(2023, 3, 9)
        series = good_series("SIVB", last - dt.timedelta(days=5))
        roster = classify([control("SIVB", last)], observed={"SIVB": series})
        assert roster.entries[0].status is SurvivorshipStatus.COVERED

    def test_unmeasured_bars_are_never_read_as_coverage(self) -> None:
        roster = classify([control("SIVB", dt.date(2023, 3, 9))], present={"SIVB"}, observed=None)
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.INSUFFICIENT_HISTORY
        assert not roster.observations_available
        assert "unproven" in entry.detail


class TestEachControlDeclaresItsOwnWindow:
    def test_the_required_start_leaves_room_to_warm_an_indicator_up(self) -> None:
        last = dt.date(2008, 9, 17)
        required = control_required_start(control("LEH", last))
        assert required is not None
        assert required < last
        # About a trading year of room, not a single session.
        assert 300 <= (last - required).days <= 420

    def test_a_recorded_first_trade_date_wins_over_the_derived_one(self) -> None:
        listed = dt.date(1994, 5, 3)
        assert control_required_start(control("X", dt.date(2008, 9, 17), first=listed)) == listed

    def test_the_preflight_number_covers_every_configured_control(self) -> None:
        universe = default_universe()
        needed = required_history_start(universe)
        assert needed is not None
        for instrument in universe.delisted:
            required = control_required_start(instrument)
            assert required is not None
            assert needed <= required, (
                f"{instrument.ticker} needs history before the preflight date"
            )

    def test_a_window_that_excludes_a_controls_life_is_our_defect(self) -> None:
        roster = classify(
            [control("BSC", dt.date(2008, 5, 30))],
            record=record(("BSC", "not_found", "rejected")),
        )
        entry = roster.entries[0]
        # The vendor said `not_found`, and that answer is about 2010-2026 —
        # years in which Bear Stearns did not exist.
        assert entry.status is SurvivorshipStatus.REQUEST_WINDOW_EXCLUDED
        assert entry.status.is_our_defect
        assert not entry.is_covered
        assert not entry.provider_supplied_bars

    def test_a_control_inside_the_window_is_judged_on_the_vendor_answer(self) -> None:
        roster = classify(
            [control("TWTR", dt.date(2022, 10, 27))],
            record=record(("TWTR", "unavailable_historically", "empty")),
        )
        assert roster.entries[0].status is SurvivorshipStatus.PROVIDER_HISTORY_UNAVAILABLE

    def test_a_start_early_enough_removes_the_window_finding_entirely(self) -> None:
        universe = default_universe()
        roster = classify(universe.delisted, requested_start=required_history_start(universe))
        assert not any(
            e.status is SurvivorshipStatus.REQUEST_WINDOW_EXCLUDED for e in roster.entries
        )


class TestSymbolIdentity:
    def test_not_found_with_untried_aliases_is_ours_not_the_vendors(self) -> None:
        roster = classify(
            [control("SIVB", dt.date(2023, 3, 9), ("SIVBQ",))],
            record=record(("SIVB", "not_found", "rejected")),
        )
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.WRONG_ALIAS
        assert "SIVBQ" in entry.detail

    def test_an_alias_that_was_tried_leaves_the_vendor_answer_standing(self) -> None:
        roster = classify(
            [control("SIVB", dt.date(2023, 3, 9), ("SIVBQ",))],
            record=record(("SIVB", "not_found", "rejected"), ("SIVBQ", "not_found", "rejected")),
        )
        assert roster.entries[0].status is SurvivorshipStatus.NOT_FOUND

    def test_an_alias_present_in_the_snapshot_does_not_cover_the_control(self) -> None:
        # WM is Waste Management today. Letting the string match satisfy the
        # control would manufacture the exact identity failure WAMUQ exists to
        # provoke.
        last = dt.date(2009, 3, 20)
        roster = classify(
            [control("WAMUQ", last, ("WAMU", "WM"))],
            present={"WM"},
            record=record(("WM", "valid", "ok")),
            requested_start=dt.date(2000, 1, 1),
            observed={"WM": good_series("WM", dt.date(2026, 8, 7))},
        )
        assert not roster.entries[0].is_covered


class TestUnrecordedIsNotUnfailed:
    def test_no_record_means_unresolved_not_a_confident_diagnosis(self) -> None:
        roster = classify([control("BBBY", dt.date(2023, 5, 3), ("BBBYQ",))])
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.UNRESOLVED
        assert roster.record_is_empty
        assert "acquire" in entry.status.remedy

    def test_an_unrecognised_vendor_status_falls_through_to_unresolved(self) -> None:
        roster = classify(
            [control("FRC", dt.date(2023, 5, 1))],
            record=record(("FRC", "something_new_in_a_later_build", "")),
        )
        assert roster.entries[0].status is SurvivorshipStatus.UNRESOLVED

    def test_entitlement_and_transport_are_not_evidence_of_absence(self) -> None:
        for status in (SurvivorshipStatus.PLAN_RESTRICTED, SurvivorshipStatus.PROVIDER_ERROR):
            assert not status.is_evidence_of_absence
        for status in (
            SurvivorshipStatus.NOT_FOUND,
            SurvivorshipStatus.PROVIDER_HISTORY_UNAVAILABLE,
        ):
            assert status.is_evidence_of_absence

    def test_every_status_has_a_remedy(self) -> None:
        for status in SurvivorshipStatus:
            assert status.remedy


class TestTheRealUniverse:
    def test_four_controls_predate_a_2010_start_and_the_report_says_which(self) -> None:
        roster = classify(default_universe().delisted)
        excluded = {
            e.ticker
            for e in roster.entries
            if e.status is SurvivorshipStatus.REQUEST_WINDOW_EXCLUDED
        }
        assert excluded == {"LEH", "BSC", "WAMUQ", "ENRNQ"}
        assert len(roster.uncovered) == len(roster.entries)

    def test_the_rendered_roster_lists_every_control_and_the_preflight_date(self) -> None:
        universe = default_universe()
        roster = classify(universe.delisted)
        text = "\n".join(render_roster(roster))
        for instrument in universe.delisted:
            assert instrument.ticker in text
        assert "PREFLIGHT" in text
        needed = required_history_start(universe)
        assert needed is not None and needed.isoformat() in text

    def test_full_coverage_requires_every_control_to_have_real_bars(self) -> None:
        universe = default_universe()
        observed = {
            i.ticker: good_series(i.ticker, i.last_trade_date)
            for i in universe.delisted
            if i.last_trade_date
        }
        roster = classify(
            universe.delisted,
            requested_start=required_history_start(universe),
            observed=observed,
        )
        assert roster.uncovered == ()
        assert len(roster.covered) == len(universe.delisted)

    def test_bankruptcy_renames_are_recorded_for_the_names_that_had_one(self) -> None:
        universe = default_universe()
        assert universe.get("LEH").alias_candidates == ("LEHMQ",)
        assert "WM" in universe.get("WAMUQ").alias_candidates


class TestTheWarningBeforeTheDownload:
    """The cheapest place to catch this is before any credits are spent."""

    def test_a_2010_start_warns_about_the_four_it_cannot_return(self) -> None:
        from tradeit.cli_data import _unreachable_delisted

        text = "\n".join(_unreachable_delisted(default_universe().tickers, WINDOW_START))
        for ticker in ("LEH", "BSC", "WAMUQ", "ENRNQ"):
            assert ticker in text
        assert "TWTR" not in text, "TWTR traded well inside the window"
        needed = required_history_start(default_universe())
        assert needed is not None and f"--start {needed.isoformat()}" in text

    def test_an_early_enough_start_says_nothing(self) -> None:
        from tradeit.cli_data import _unreachable_delisted

        needed = required_history_start(default_universe())
        assert needed is not None
        assert _unreachable_delisted(default_universe().tickers, needed) == []

    def test_a_symbol_list_without_delisted_names_says_nothing(self) -> None:
        from tradeit.cli_data import _unreachable_delisted

        assert _unreachable_delisted(["SPY", "AAPL"], dt.date(2020, 1, 1)) == []


@pytest.mark.parametrize(
    ("symbol_status", "expected"),
    [
        ("not_found", SurvivorshipStatus.NOT_FOUND),
        ("unavailable_historically", SurvivorshipStatus.PROVIDER_HISTORY_UNAVAILABLE),
        ("plan_restricted", SurvivorshipStatus.PLAN_RESTRICTED),
        ("ambiguous", SurvivorshipStatus.AMBIGUOUS),
    ],
)
def test_each_vendor_symbol_status_maps_to_its_own_finding(
    symbol_status: str, expected: SurvivorshipStatus
) -> None:
    roster = classify(
        [control("ZZZ", dt.date(2023, 1, 1))],
        record=record(("ZZZ", symbol_status, "rejected")),
    )
    assert roster.entries[0].status is expected
    assert not roster.entries[0].is_covered


def test_the_minimum_is_a_trading_year_and_is_stated_once() -> None:
    assert MIN_CONTROL_SESSIONS == 252
