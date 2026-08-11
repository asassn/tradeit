"""A missing delisted control is still a FAIL — but not still a mystery.

The property under test throughout: classification changes the *remedy* and
never the *verdict*. Every test that asserts a status also asserts that the
name is still uncovered, because the whole risk of adding a taxonomy here is
that some category eventually starts meaning "acceptable".
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
    AcquisitionOutcomeView,
    AcquisitionRecordView,
    SurvivorshipStatus,
    classify_roster,
    render_roster,
)

WINDOW_START = dt.date(2010, 1, 1)


def delisted(ticker: str, last: dt.date, aliases: tuple[str, ...] = ()) -> ValidationInstrument:
    return ValidationInstrument(
        ticker=ticker,
        category=UniverseCategory.DELISTED,
        difficulty="bankruptcy",
        last_trade_date=last,
        alias_candidates=aliases,
    )


def record(*rows: tuple[str, str, str]) -> AcquisitionRecordView:
    return AcquisitionRecordView(
        provider="test",
        requested_start=WINDOW_START,
        requested_end=dt.date(2026, 8, 7),
        outcomes=tuple(
            AcquisitionOutcomeView(ticker=t, symbol_status=s, fetch_status=f) for t, s, f in rows
        ),
    )


class TestTheVerdictIsNeverSoftened:
    def test_every_status_but_present_is_uncovered(self) -> None:
        for status in SurvivorshipStatus:
            assert status.is_covered is (status is SurvivorshipStatus.PRESENT)

    def test_a_name_absent_for_our_own_reason_is_still_absent(self) -> None:
        # The most dangerous reading of this taxonomy: "it was never
        # obtainable, so it does not count against us". It counts.
        roster = classify_roster(
            [delisted("LEH", dt.date(2008, 9, 17))],
            present=set(),
            record=AcquisitionRecordView(),
            requested_start=WINDOW_START,
        )
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.OUTSIDE_REQUESTED_WINDOW
        assert entry.status.is_our_defect
        assert not entry.is_covered
        assert roster.missing == (entry,)

    def test_entitlement_and_transport_are_not_evidence_of_absence(self) -> None:
        for status in (
            SurvivorshipStatus.NOT_AVAILABLE_ON_PLAN,
            SurvivorshipStatus.PROVIDER_ERROR,
        ):
            assert not status.is_evidence_of_absence
        for status in (
            SurvivorshipStatus.NOT_FOUND_AT_PROVIDER,
            SurvivorshipStatus.NO_HISTORY_AT_PROVIDER,
        ):
            assert status.is_evidence_of_absence


class TestTheRequestedWindow:
    def test_a_security_that_died_before_the_request_is_our_defect(self) -> None:
        roster = classify_roster(
            [delisted("BSC", dt.date(2008, 5, 30))],
            present=set(),
            record=record(("BSC", "not_found", "rejected")),
            requested_start=WINDOW_START,
        )
        # The vendor said `not_found`, and that answer is about 2010-2026 —
        # years in which Bear Stearns did not exist. Reporting it as a vendor
        # coverage gap would send the fix to the wrong side of the boundary.
        assert roster.entries[0].status is SurvivorshipStatus.OUTSIDE_REQUESTED_WINDOW

    def test_a_security_inside_the_window_is_classified_by_the_vendor_answer(self) -> None:
        roster = classify_roster(
            [delisted("TWTR", dt.date(2022, 10, 27))],
            present=set(),
            record=record(("TWTR", "unavailable_historically", "empty")),
            requested_start=WINDOW_START,
        )
        assert roster.entries[0].status is SurvivorshipStatus.NO_HISTORY_AT_PROVIDER

    def test_last_trade_exactly_on_the_start_date_is_inside_the_window(self) -> None:
        roster = classify_roster(
            [delisted("X", WINDOW_START)],
            present=set(),
            record=record(("X", "not_found", "rejected")),
            requested_start=WINDOW_START,
        )
        assert roster.entries[0].status is SurvivorshipStatus.NOT_FOUND_AT_PROVIDER

    def test_an_unknown_window_does_not_invent_a_window_defect(self) -> None:
        roster = classify_roster(
            [delisted("LEH", dt.date(2008, 9, 17))],
            present=set(),
            record=AcquisitionRecordView(),
            requested_start=None,
        )
        assert roster.entries[0].status is SurvivorshipStatus.UNRESOLVED


class TestSymbolIdentity:
    def test_not_found_with_untried_aliases_is_our_defect_not_the_vendors(self) -> None:
        roster = classify_roster(
            [delisted("SIVB", dt.date(2023, 3, 9), ("SIVBQ",))],
            present=set(),
            record=record(("SIVB", "not_found", "rejected")),
            requested_start=WINDOW_START,
        )
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.SYMBOL_ALIAS_NOT_ATTEMPTED
        assert "SIVBQ" in entry.detail

    def test_an_alias_that_was_tried_leaves_the_vendor_answer_standing(self) -> None:
        roster = classify_roster(
            [delisted("SIVB", dt.date(2023, 3, 9), ("SIVBQ",))],
            present=set(),
            record=record(("SIVB", "not_found", "rejected"), ("SIVBQ", "not_found", "rejected")),
            requested_start=WINDOW_START,
        )
        # Both strings were asked for and both were refused. That is now a
        # statement about the vendor, and calling it "alias not attempted"
        # would be false.
        assert roster.entries[0].status is SurvivorshipStatus.NOT_FOUND_AT_PROVIDER

    def test_a_name_with_no_recorded_alias_stays_a_vendor_finding(self) -> None:
        roster = classify_roster(
            [delisted("VMW", dt.date(2023, 11, 21))],
            present=set(),
            record=record(("VMW", "not_found", "rejected")),
            requested_start=WINDOW_START,
        )
        assert roster.entries[0].status is SurvivorshipStatus.NOT_FOUND_AT_PROVIDER


class TestUnrecordedIsNotUnfailed:
    def test_no_record_means_unresolved_not_a_confident_diagnosis(self) -> None:
        # A package written before outcomes were recorded cannot distinguish
        # "requested and refused" from "never requested", and must not pretend
        # it can.
        roster = classify_roster(
            [delisted("BBBY", dt.date(2023, 5, 3), ("BBBYQ",))],
            present=set(),
            record=AcquisitionRecordView(),
            requested_start=WINDOW_START,
        )
        entry = roster.entries[0]
        assert entry.status is SurvivorshipStatus.UNRESOLVED
        assert roster.record_is_empty
        assert "acquire" in entry.status.remedy

    def test_an_unrecognised_vendor_status_falls_through_to_unresolved(self) -> None:
        roster = classify_roster(
            [delisted("FRC", dt.date(2023, 5, 1))],
            present=set(),
            record=record(("FRC", "something_new_in_a_later_build", "")),
            requested_start=WINDOW_START,
        )
        assert roster.entries[0].status is SurvivorshipStatus.UNRESOLVED

    def test_every_status_has_a_remedy(self) -> None:
        for status in SurvivorshipStatus:
            assert status.remedy


class TestPresence:
    def test_a_present_name_is_covered_whatever_the_record_says(self) -> None:
        roster = classify_roster(
            [delisted("TWTR", dt.date(2022, 10, 27))],
            present={"TWTR"},
            record=record(("TWTR", "not_found", "rejected")),
            requested_start=WINDOW_START,
        )
        # The snapshot is the authority on what the snapshot contains. A stale
        # or wrong acquisition record must not be able to report a name absent
        # when its bars are right there.
        assert roster.entries[0].status is SurvivorshipStatus.PRESENT
        assert roster.missing == ()


class TestTheRealUniverse:
    """The eleven canonical controls, against the window actually acquired."""

    def test_four_controls_predate_a_2010_start_and_the_report_says_which(self) -> None:
        universe = default_universe()
        roster = classify_roster(
            universe.delisted,
            present=set(),
            record=AcquisitionRecordView(),
            requested_start=WINDOW_START,
        )
        window = SurvivorshipStatus.OUTSIDE_REQUESTED_WINDOW
        outside = {e.ticker for e in roster.entries if e.status is window}
        assert outside == {"LEH", "BSC", "WAMUQ", "ENRNQ"}
        assert len(roster.missing) == len(universe.delisted)

    def test_a_start_early_enough_makes_none_of_them_a_window_defect(self) -> None:
        universe = default_universe()
        earliest = min(
            i.last_trade_date for i in universe.delisted if i.last_trade_date is not None
        )
        roster = classify_roster(
            universe.delisted,
            present=set(),
            record=AcquisitionRecordView(),
            requested_start=earliest,
        )
        assert not any(
            e.status is SurvivorshipStatus.OUTSIDE_REQUESTED_WINDOW for e in roster.entries
        )

    def test_the_rendered_roster_lists_every_name_and_truncates_nothing(self) -> None:
        universe = default_universe()
        roster = classify_roster(
            universe.delisted,
            present=set(),
            record=AcquisitionRecordView(),
            requested_start=WINDOW_START,
        )
        text = "\n".join(render_roster(roster))
        for instrument in universe.delisted:
            assert instrument.ticker in text


class TestUniverseAliases:
    def test_bankruptcy_renames_are_recorded_for_the_names_that_had_one(self) -> None:
        universe = default_universe()
        assert universe.get("LEH").alias_candidates == ("LEHMQ",)
        assert "WM" in universe.get("WAMUQ").alias_candidates

    def test_an_alias_present_in_the_snapshot_does_not_cover_the_control(self) -> None:
        # The alias is a lead, not a substitution. A snapshot holding WM (Waste
        # Management today) does not hold Washington Mutual, and letting the
        # string match satisfy the control is precisely the identity failure
        # this instrument exists to provoke.
        roster = classify_roster(
            [delisted("WAMUQ", dt.date(2009, 3, 20), ("WAMU", "WM"))],
            present={"WM"},
            record=record(("WM", "valid", "ok")),
            requested_start=dt.date(2000, 1, 1),
        )
        assert not roster.entries[0].is_covered


@pytest.mark.parametrize(
    ("symbol_status", "expected"),
    [
        ("not_found", SurvivorshipStatus.NOT_FOUND_AT_PROVIDER),
        ("unavailable_historically", SurvivorshipStatus.NO_HISTORY_AT_PROVIDER),
        ("plan_restricted", SurvivorshipStatus.NOT_AVAILABLE_ON_PLAN),
        ("ambiguous", SurvivorshipStatus.AMBIGUOUS_AT_PROVIDER),
    ],
)
def test_each_vendor_symbol_status_maps_to_its_own_finding(
    symbol_status: str, expected: SurvivorshipStatus
) -> None:
    roster = classify_roster(
        [delisted("ZZZ", dt.date(2023, 1, 1))],
        present=set(),
        record=record(("ZZZ", symbol_status, "rejected")),
        requested_start=WINDOW_START,
    )
    assert roster.entries[0].status is expected
    assert not roster.entries[0].is_covered
