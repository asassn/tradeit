"""What the adjudicator may and may not conclude from a series' shape.

The first test here is a regression against a mistake made while measuring
this: a naive "largest gap" scan reported a boundary in 23 series whose largest
hole was the four sessions the US market was closed after September 11th 2001.
The expected answer arrived, and it was not an answer to the question asked.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.research01.adjudicate import (
    DORMANCY_DAYS,
    MIN_SECOND_RUN,
    REGIME_BREAK_THRESHOLD,
    Adjudication,
    BoundaryEvidence,
    RegimeBreak,
    Verdict,
    adjudicate_series,
    detect_regime_break,
)

D = dt.date


def _run(start: D, count: int, *, step: int = 1) -> list[D]:
    return [start + dt.timedelta(days=step * i) for i in range(count)]


def test_september_2001_closure_is_not_a_boundary() -> None:
    """The registrant's span is set in 1998 deliberately, so the grace window is

    long past and the 180-day dormancy threshold is the only thing refusing this
    hole. Without that threshold the seven days between the 10th and the 17th
    are the largest gap in 23 real series, and each would be cut there.
    """
    before = (D(2001, 9, 10) - D(1998, 1, 5)).days + 1
    sessions = _run(D(1998, 1, 5), before) + _run(D(2001, 9, 17), 400)
    result = adjudicate_series(sessions=sessions, last_filing=D(1998, 6, 1))
    assert result.verdict is Verdict.UNRESOLVED
    assert result.boundary is None


def test_dormancy_while_the_registrant_was_still_filing_is_a_coverage_hole() -> None:
    """A hole in 1999 cannot be a handover when the registrant filed until 2003.

    The series is made to overrun so the coherence gate is not what saves it:
    the only thing refusing the 1999 hole is that it closed before the
    registrant did.
    """
    sessions = _run(D(1998, 1, 5), 60) + _run(D(2000, 6, 1), 4000)
    result = adjudicate_series(sessions=sessions, last_filing=D(2003, 3, 31))
    assert result.verdict is Verdict.UNRESOLVED
    assert result.boundary is None


def test_dormancy_resuming_inside_the_grace_window_is_not_a_handover() -> None:
    sessions = _run(D(2002, 1, 7), 100) + _run(D(2003, 1, 6), 1200)
    result = adjudicate_series(sessions=sessions, last_filing=D(2002, 6, 3))
    assert result.verdict is Verdict.UNRESOLVED
    assert result.boundary is None


def test_dormancy_then_a_substantial_run_locates_a_splice() -> None:
    era = _run(D(1999, 1, 4), 300)
    later = _run(D(2016, 1, 4), 500)
    result = adjudicate_series(sessions=era + later, last_filing=D(2000, 2, 1))
    assert result.verdict is Verdict.SPLICE_LOCATED
    assert result.boundary == era[-1]
    assert result.kept_bars == 300
    assert result.dropped_bars == 500
    assert BoundaryEvidence.DORMANCY in result.evidence
    assert result.dormancy_days == (later[0] - era[-1]).days


def test_dormancy_then_a_handful_of_prints_is_a_tail_artefact() -> None:
    era = _run(D(1998, 1, 5), 215)
    result = adjudicate_series(
        sessions=[*era, D(2018, 8, 28), D(2018, 8, 29)],
        last_filing=D(1998, 11, 6),
    )
    assert result.verdict is Verdict.TAIL_ARTEFACT
    assert result.dropped_bars == 2
    assert result.kept_bars == 215


def test_the_tail_threshold_is_the_only_difference_between_the_two() -> None:
    era = _run(D(1999, 1, 4), 100)
    at_threshold = era + _run(D(2015, 1, 5), MIN_SECOND_RUN)
    below = era + _run(D(2015, 1, 5), MIN_SECOND_RUN - 1)
    assert adjudicate_series(sessions=at_threshold, last_filing=D(2000, 1, 3)).verdict is (
        Verdict.SPLICE_LOCATED
    )
    assert adjudicate_series(sessions=below, last_filing=D(2000, 1, 3)).verdict is (
        Verdict.TAIL_ARTEFACT
    )


def test_a_series_with_no_bar_in_the_registrants_lifetime_is_wholly_misattributed() -> None:
    result = adjudicate_series(
        sessions=_run(D(2022, 1, 25), 341),
        last_filing=D(2004, 5, 13),
    )
    assert result.verdict is Verdict.WHOLLY_MISATTRIBUTED
    assert result.kept_bars == 0
    assert result.dropped_bars == 341
    assert result.boundary is None
    assert BoundaryEvidence.NO_OVERLAP in result.evidence


def test_a_single_stray_bar_decades_later_is_wholly_misattributed_not_a_tail() -> None:
    """No overlap outranks dormancy: there is no registrant era to cut away from."""
    result = adjudicate_series(sessions=[D(2018, 3, 13)], last_filing=D(2003, 9, 26))
    assert result.verdict is Verdict.WHOLLY_MISATTRIBUTED


def test_registry_reuse_without_a_break_refuses_to_guess_a_boundary() -> None:
    result = adjudicate_series(
        sessions=_run(D(1999, 1, 4), 4000),
        last_filing=D(2001, 2, 13),
        registrant_cik=1088755,
        current_holder_cik=1452477,
    )
    assert result.verdict is Verdict.CONTAMINATED_BOUNDARY_UNKNOWN
    assert result.boundary is None
    assert result.evidence == (BoundaryEvidence.REGISTRY_REUSE,)
    assert not result.is_actionable


def test_the_same_cik_holding_the_symbol_today_is_not_reuse() -> None:
    result = adjudicate_series(
        sessions=_run(D(1999, 1, 4), 4000),
        last_filing=D(2001, 2, 13),
        registrant_cik=1088755,
        current_holder_cik=1088755,
    )
    assert result.verdict is Verdict.UNRESOLVED


def test_registry_reuse_corroborates_a_located_boundary_without_moving_it() -> None:
    era = _run(D(1999, 1, 4), 300)
    with_reuse = adjudicate_series(
        sessions=era + _run(D(2016, 1, 4), 500),
        last_filing=D(2000, 2, 1),
        registrant_cik=1,
        current_holder_cik=2,
    )
    without = adjudicate_series(sessions=era + _run(D(2016, 1, 4), 500), last_filing=D(2000, 2, 1))
    assert with_reuse.boundary == without.boundary
    assert BoundaryEvidence.REGISTRY_REUSE in with_reuse.evidence
    assert BoundaryEvidence.REGISTRY_REUSE not in without.evidence


def test_a_filed_exit_never_authorises_cutting_trading_the_registrant_still_reported() -> None:
    """The anchor is the LATER of the two dates, and this is why.

    A Form 25 in 2003 beside filings that continue to 2005 must not licence a
    cut in 2003: the registrant demonstrably existed until 2005.
    """
    sessions = _run(D(2002, 1, 7), 200) + _run(D(2004, 6, 1), 900)
    kwargs = {"sessions": sessions, "filed_exit": D(2003, 1, 6)}
    assert adjudicate_series(last_filing=D(2005, 2, 15), **kwargs).boundary is None

    # The same series anchored on the Form 25 alone WOULD be cut, which is what
    # makes this a test of the anchor rather than of the thresholds.
    assert adjudicate_series(last_filing=None, **kwargs).boundary == sessions[199]


def test_a_filed_exit_is_recorded_as_corroboration_when_a_boundary_is_found() -> None:
    era = _run(D(1999, 1, 4), 300)
    result = adjudicate_series(
        sessions=era + _run(D(2016, 1, 4), 500),
        last_filing=D(2000, 2, 1),
        filed_exit=D(2000, 6, 1),
    )
    assert result.verdict is Verdict.SPLICE_LOCATED
    assert BoundaryEvidence.FILED_EXIT in result.evidence


def test_the_earliest_qualifying_dormancy_wins() -> None:
    """Everything after the first genuine break is suspect, including a second break."""
    era = _run(D(1999, 1, 4), 200)
    middle = _run(D(2008, 1, 7), 200)
    late = _run(D(2020, 1, 6), 200)
    result = adjudicate_series(sessions=era + middle + late, last_filing=D(2000, 2, 1))
    assert result.boundary == era[-1]
    assert result.dropped_bars == 400


def test_an_unknown_filing_span_is_unresolved_and_not_coherent() -> None:
    result = adjudicate_series(sessions=_run(D(1999, 1, 4), 100), last_filing=None)
    assert result.verdict is Verdict.UNRESOLVED
    assert "unknown" in result.note


def test_no_sessions_is_unresolved() -> None:
    assert adjudicate_series(sessions=[], last_filing=D(2000, 1, 1)).verdict is Verdict.UNRESOLVED


@pytest.mark.parametrize(
    ("verdict", "actionable"),
    [
        (Verdict.WHOLLY_MISATTRIBUTED, True),
        (Verdict.SPLICE_LOCATED, True),
        (Verdict.TAIL_ARTEFACT, True),
        (Verdict.CONTAMINATED_BOUNDARY_UNKNOWN, False),
        (Verdict.UNRESOLVED, False),
        (Verdict.COHERENT, False),
    ],
)
def test_only_a_located_boundary_may_be_written_to_the_corpus(
    verdict: Verdict, actionable: bool
) -> None:
    assert Adjudication(verdict, None).is_actionable is actionable


def test_the_boundary_is_inclusive_of_the_registrants_last_session() -> None:
    era = _run(D(1999, 1, 4), 50)
    result = adjudicate_series(sessions=era + _run(D(2016, 1, 4), 100), last_filing=D(2000, 2, 1))
    assert result.boundary == era[-1]
    assert result.kept_bars == len(era)


@pytest.mark.parametrize(
    ("gap", "expected"),
    [(DORMANCY_DAYS - 1, Verdict.UNRESOLVED), (DORMANCY_DAYS, Verdict.SPLICE_LOCATED)],
)
def test_the_dormancy_threshold_is_a_threshold(gap: int, expected: Verdict) -> None:
    """The era runs past the grace window so this isolates the day count alone."""
    era = _run(D(1999, 1, 4), 500)
    result = adjudicate_series(
        sessions=era + _run(era[-1] + dt.timedelta(days=gap), 100),
        last_filing=D(1999, 1, 1),
    )
    assert result.verdict is expected


def test_a_coherent_series_is_not_contaminated_by_a_vendor_disambiguated_symbol() -> None:
    """The defect a first version of this shipped, caught before it was applied.

    Every ``_OLD`` symbol has its plain form held by somebody else today -- that
    is what the suffix means. Asking the registry question of a series that ends
    where its registrant does returned 36 corpus securities as contaminated, and
    not one of them was among the 79 the measurement had flagged. The vendor had
    already separated them correctly.
    """
    result = adjudicate_series(
        sessions=_run(D(1996, 1, 3), 1200),
        last_filing=D(1999, 6, 18),
        registrant_cik=1001603,
        current_holder_cik=897448,
    )
    assert result.verdict is Verdict.COHERENT
    assert not result.is_actionable
    assert result.boundary is None


def test_coherent_is_not_a_certificate_that_the_series_is_clean() -> None:
    """It says the ending does not betray a splice, and nothing more."""
    result = adjudicate_series(sessions=_run(D(1999, 1, 4), 400), last_filing=D(2000, 6, 1))
    assert result.verdict is Verdict.COHERENT
    assert result.kept_bars == 400
    assert result.dropped_bars == 0


class TestRegimeBreak:
    """The last resort, for the shape that defeats every other rule.

    A vendor that never stops emitting rows leaves no dormancy to find. What it
    leaves instead is a series whose price level and traded volume both change
    by a large factor at one date, with no split behind it.
    """

    def _bars(
        self,
        start: D,
        count: int,
        close: float,
        volume: float,
    ) -> list[tuple[D, float, float]]:
        return [(start + dt.timedelta(days=i), close, volume) for i in range(count)]

    def test_price_and_volume_must_BOTH_break(self) -> None:
        """Taking the smaller of the two ratios is the whole design.

        A penny stock's price triples routinely and a thin quote's volume goes
        from nothing to something all the time. Either alone establishes nothing.
        """
        price_only = self._bars(D(2000, 1, 3), 80, 0.05, 1000) + self._bars(
            D(2000, 3, 23), 80, 50.0, 1000
        )
        volume_only = self._bars(D(2000, 1, 3), 80, 1.0, 1) + self._bars(
            D(2000, 3, 23), 80, 1.0, 100_000
        )
        assert detect_regime_break(price_only, after=D(2000, 1, 3)) is None
        assert detect_regime_break(volume_only, after=D(2000, 1, 3)) is None

    def test_both_breaking_together_is_found(self) -> None:
        bars = self._bars(D(2000, 1, 3), 80, 0.05, 0) + self._bars(
            D(2000, 3, 23), 80, 8.93, 130_000
        )
        found = detect_regime_break(bars, after=D(2000, 1, 3))
        assert found is not None
        assert found.session_date == D(2000, 3, 23)
        assert found.score >= REGIME_BREAK_THRESHOLD

    def test_a_break_beside_a_split_is_the_split(self) -> None:
        bars = self._bars(D(2000, 1, 3), 80, 0.05, 0) + self._bars(
            D(2000, 3, 23), 80, 8.93, 130_000
        )
        assert (
            detect_regime_break(bars, after=D(2000, 1, 3), split_ex_dates=[D(2000, 3, 25)]) is None
        )

    def test_the_anchor_itself_is_a_candidate(self) -> None:
        """A ticker that changed hands the moment its registrant went quiet
        leaves no break *inside* the later run: the break is at the anchor."""
        bars = self._bars(D(2000, 1, 3), 80, 0.05, 0) + self._bars(
            D(2000, 3, 23), 80, 8.93, 130_000
        )
        found = detect_regime_break(bars, after=D(2000, 3, 23))
        assert found is not None and found.session_date == D(2000, 3, 23)

    def test_a_break_before_the_anchor_is_not_considered(self) -> None:
        bars = self._bars(D(2000, 1, 3), 80, 0.05, 0) + self._bars(
            D(2000, 3, 23), 80, 8.93, 130_000
        )
        assert detect_regime_break(bars, after=D(2001, 1, 1)) is None

    def test_a_series_shorter_than_two_windows_yields_nothing(self) -> None:
        assert detect_regime_break(self._bars(D(2000, 1, 3), 40, 1.0, 10), after=None) is None

    def test_the_verdict_cuts_at_the_last_session_of_the_old_regime(self) -> None:
        sessions = _run(D(2000, 1, 3), 80) + _run(D(2000, 3, 23), 80)
        result = adjudicate_series(
            sessions=sessions,
            last_filing=D(1999, 3, 1),
            regime_break=RegimeBreak(D(2000, 3, 23), 178.6, 0.05, 8.93, 0, 130_000),
        )
        assert result.verdict is Verdict.REGIME_BREAK
        assert result.boundary == D(2000, 1, 3) + dt.timedelta(days=79)
        assert result.kept_bars == 80
        assert result.dropped_bars == 80
        assert result.is_actionable

    def test_dormancy_outranks_a_regime_break(self) -> None:
        """Dormancy is direct evidence of absence; a regime break is inference
        from behaviour. Where both are present the stronger one decides."""
        era = _run(D(1999, 1, 4), 300)
        later = _run(D(2016, 1, 4), 500)
        result = adjudicate_series(
            sessions=era + later,
            last_filing=D(2000, 2, 1),
            regime_break=RegimeBreak(later[100], 200.0, 1, 100, 0, 100),
        )
        assert result.verdict is Verdict.SPLICE_LOCATED
        assert result.boundary == era[-1]

    def test_a_break_at_or_before_the_anchor_is_refused_by_the_verdict(self) -> None:
        sessions = _run(D(2000, 1, 3), 900)
        result = adjudicate_series(
            sessions=sessions,
            last_filing=D(2000, 4, 1),
            regime_break=RegimeBreak(D(2000, 3, 23), 178.6, 0.05, 8.93, 0, 130_000),
        )
        assert result.verdict is not Verdict.REGIME_BREAK

    def test_the_verdict_is_not_called_a_splice(self) -> None:
        """The cause may be a ticker changing hands -- or the vendor stitching
        two sources, or re-denominating a quote. The evidence supports "not the
        same tradable thing", and naming it a splice would claim more."""
        assert Verdict.REGIME_BREAK.value == "regime_break"
        assert "splice" not in Verdict.REGIME_BREAK.value
