"""The report-only check for moves trading cannot explain.

`docs/ELME_VENUE_BREAK_2026-11-15.md` measured a -97.6% single-session move
across a venue break that the capture got ``ok`` across sixteen times, and that
a person found three weeks later. These tests hold the properties that make the
detector worth having rather than decorative.

**Each test here was run against a deliberately broken copy of the module and
shown to fail.** The mutants are named in the test that caught them, because a
test nobody proved can fail is a test that passes for unknown reasons -- which
is how two tests in this repository went vacuous.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from tradeit.forward.discontinuity import (
    DISCONTINUITY_RATIO,
    find_discontinuities,
    scan_record,
)


@dataclass(frozen=True)
class _Bar:
    instrument_id: int
    session_date: dt.date
    close: Decimal


@dataclass(frozen=True)
class _Action:
    instrument_id: int
    action_type: str
    ex_date: dt.date
    ratio: Decimal


def _bars(instrument_id: int, *closes: str, start: int = 1) -> list[_Bar]:
    """Consecutive-in-the-record bars, one per calendar day from ``start``."""
    return [
        _Bar(instrument_id, dt.date(2026, 10, start + offset), Decimal(close))
        for offset, close in enumerate(closes)
    ]


def test_the_elme_shape_is_reported() -> None:
    """The move the detector exists for: 1.77 -> 0.0419 at a venue break."""
    found = find_discontinuities(_bars(1700, "1.77", "0.0419"))

    assert len(found) == 1
    assert found[0].instrument_id == 1700
    assert found[0].ratio > 42
    assert found[0].direction == "down"


def test_the_largest_trading_move_in_the_record_is_not_reported() -> None:
    """**The noise test, pinned to measured evidence rather than to taste.**

    1.490x (-32.9%) is the largest consecutive-session move in the forward
    record once ELME is set aside -- a penny stock trading, measured 2026-11-15
    over 4,132 pairs. A detector that flagged it would fire on ordinary
    volatility and be ignored within a week.

    Mutant: ``ratio = Decimal("1.4")``. Caught.
    """
    found = find_discontinuities(_bars(1463, "0.2981", "0.2"))

    assert found == [], "the detector must not fire on the loudest real trading move measured"


def test_the_threshold_is_direction_free() -> None:
    """A reverse split and a share consolidation move the price *up*.

    Checking only for collapses would catch ELME and miss every reverse split,
    which is the same class of defect in the opposite direction.

    Mutant: ``return close / previous``. Caught -- the doubling survived, the
    halving was missed.
    """
    down = find_discontinuities(_bars(1, "10", "5"))
    up = find_discontinuities(_bars(2, "5", "10"))

    assert len(down) == 1, "a halving is a discontinuity"
    assert len(up) == 1, "so is a doubling"
    assert down[0].direction == "down"
    assert up[0].direction == "up"


def test_the_threshold_stays_inside_its_derived_bounds() -> None:
    """**Asserts the derivation, not the number.**

    Two measured constraints fix the threshold (see the module docstring): it
    must sit above 1.490x, the largest trading move in the record, and at or
    below 2.0, the smallest ratio a corporate action produces -- below which
    routine splits would escape. This test fails if someone moves the threshold
    out of that window without re-deriving it, which is the move the module
    docstring warns against: *raising a detector's threshold to silence it is
    how a safeguard becomes decoration.*

    Pinning ``== 2`` instead would be a documented count, and
    `tests/unit/test_documented_counts.py` exists because three of those rotted.
    """
    # Bound to a lower-case local because ruff reads an UPPER_CASE name on the
    # left of a comparison as a Yoda condition (SIM300) and would have the
    # bounds written backwards, which is the harder way round to read a window.
    threshold = DISCONTINUITY_RATIO
    assert threshold > Decimal("1.49"), (
        "below the largest trading move measured, so it would fire on real volatility"
    )
    assert threshold <= Decimal("2"), (
        "above 2:1, so an ordinary split would pass unflagged -- the §(3) limitation"
    )


def test_a_gap_in_the_sessions_still_forms_a_pair() -> None:
    """Consecutive *in the record*, not calendar-adjacent.

    A security that stops printing for a week and resumes a factor of ten lower
    is the exact shape being looked for. Requiring adjacent dates would skip the
    halt-then-reprice case, which is most of what a venue change looks like.

    Mutant: skip pairs more than one day apart. Caught.
    """
    series = [
        _Bar(900, dt.date(2026, 10, 20), Decimal("8")),
        _Bar(900, dt.date(2026, 11, 3), Decimal("0.5")),
    ]

    assert len(find_discontinuities(series)) == 1


def test_two_instruments_never_form_a_pair() -> None:
    """A $400 stock beside a $0.05 one is not a 8000x move.

    Mutant: drop the per-instrument grouping and scan one flat list. Caught --
    it reported three discontinuities where there are none.
    """
    series = [
        _Bar(1, dt.date(2026, 10, 20), Decimal("400")),
        _Bar(2, dt.date(2026, 10, 20), Decimal("0.05")),
        _Bar(3, dt.date(2026, 10, 20), Decimal("400")),
    ]

    assert find_discontinuities(series) == []


def test_bars_arriving_unsorted_give_the_same_answer() -> None:
    """Insurance against a caller that forgot an ``ORDER BY``.

    A scrambled series produces findings that look right and are not -- the
    worst failure shape in this project, and the one *"never answer by
    accident"* is about. So the function sorts rather than trusting.

    Mutant: remove ``series.sort(...)``. Caught -- the shuffled input reported
    a spurious pair and the real one against the wrong previous session.
    """
    ordered = _bars(1700, "1.77", "1.76", "0.0419")
    shuffled = [ordered[2], ordered[0], ordered[1]]

    assert find_discontinuities(shuffled) == find_discontinuities(ordered)
    assert len(find_discontinuities(shuffled)) == 1
    assert find_discontinuities(shuffled)[0].previous_session == dt.date(2026, 10, 2)


def test_a_zero_close_is_skipped_rather_than_raising() -> None:
    """**This runs unattended.** One unreadable bar must not take the report down.

    Same choice `capture_forward._row_session` makes, and for the same reason: a
    report that crashes on a bad row is a report nobody gets on the night it
    mattered. The surrounding real pair is still found.

    **This test asserts the behaviour, not the mechanism, because the first
    version of it passed for the wrong reason.** Removing the ``<= 0`` guard
    alone left it green: the surrounding ``try`` catches ``DivisionByZero`` and
    returns ``None`` anyway, so the test was proving the net rather than the
    guard it named. The mutant that catches it is the naive implementation
    somebody would actually write in its place -- ``return max(close /
    previous, previous / close)`` with neither guard nor net -- and against that
    it fails. Either mechanism satisfies the invariant; neither does not.

    A negative close is covered by the same assertion: it must not raise and
    must not flag, since a direction-free ratio built from a negative price is
    not a move, it is bad data.
    """
    series = _bars(1, "10", "0", "10", "0.5", "-0.25")

    found = find_discontinuities(series)

    assert [hit.previous_session for hit in found] == [dt.date(2026, 10, 3)]


def test_an_action_is_reported_beside_the_move_never_instead_of_it() -> None:
    """**The crying-wolf fix that must not become a suppression.**

    An action in the record is a *candidate explanation*, so the operator can
    see a flagged 2:1 split is a known split. It is never a reason to drop the
    finding: a 2:1 split recorded beside a 42x move does not explain it, and
    deciding whether an action accounts for a move is a judgement this does not
    make. Fail closed -- report both and let a person rule.

    Mutant: ``continue`` when an action matches. Caught.
    """
    series = _bars(1700, "1.77", "0.0419")
    actions = [_Action(1700, "split", dt.date(2026, 10, 2), Decimal("2"))]

    found = find_discontinuities(series, actions=actions)

    assert len(found) == 1, "an action must never suppress the finding"
    assert found[0].candidate_actions
    assert "split" in found[0].describe()


def test_an_action_on_a_different_session_is_not_offered_as_an_explanation() -> None:
    """Mutant: match on instrument alone, ignoring the date. Caught."""
    series = _bars(1700, "1.77", "0.0419")
    actions = [_Action(1700, "split", dt.date(2026, 9, 1), Decimal("2"))]

    assert find_discontinuities(series, actions=actions)[0].candidate_actions == ()


def test_a_record_with_no_actions_says_so_rather_than_implying_unexplained(
    db_session: Session,
) -> None:
    """**Honesty about what the record could have explained.**

    While nothing writes corporate actions to the forward record -- measured
    2026-11-15: zero rows -- *every* finding is unexplained, and a report saying
    "unexplained" without saying that overstates itself. The §(3) limitation of
    `CLAUDE.md` is precisely that unrecorded splits are not caught; a scan must
    not imply it checked against a table that is empty.

    Mutant: ``actions_available=True``. Caught.
    """
    _write_bars(db_session, 1700, [("2026-10-23", "1.77"), ("2026-10-26", "0.0419")])

    scan = scan_record(db_session)

    assert scan.actions_available is False
    assert "no corporate action at all" in scan.describe()
    assert len(scan.findings) == 1


def test_a_clean_record_is_reported_as_clean_not_as_silence(db_session: Session) -> None:
    """A scan that found nothing must say it looked.

    An empty report is indistinguishable from a report that never ran, which is
    the same confusion `CaptureReport.session_unobserved` exists to prevent one
    level up.

    Mutant: return early with no ``describe`` line when there are no findings.
    Caught.
    """
    _write_bars(db_session, 1, [("2026-10-20", "10"), ("2026-10-21", "10.5")])

    scan = scan_record(db_session)

    assert scan.findings == ()
    assert scan.bars == 2
    assert scan.pairs == 1
    assert "no single-session move beyond the threshold" in scan.describe()


def test_the_scan_counts_pairs_not_bars(db_session: Session) -> None:
    """Two instruments of three bars each are four pairs, not six.

    Mutant: ``pairs=len(bars)``. Caught. The count is what the threshold's
    derivation rests on, so a report that inflates it overstates the evidence
    behind the number.
    """
    _write_bars(db_session, 1, [("2026-10-20", "1"), ("2026-10-21", "1"), ("2026-10-22", "1")])
    _write_bars(db_session, 2, [("2026-10-20", "1"), ("2026-10-21", "1"), ("2026-10-22", "1")])

    scan = scan_record(db_session)

    assert (scan.bars, scan.instruments, scan.pairs) == (6, 2, 4)


def test_since_keeps_the_pair_that_spans_the_boundary(db_session: Session) -> None:
    """A nightly run reports what is new **without losing the join.**

    The move is *between* the last old session and the first new one, so a
    filter applied to both bars would drop exactly the finding a nightly scan
    exists to produce -- ELME's own shape, where the last NYSE bar is old and
    the first OTC bar is new.

    Mutant: filter on ``hit.previous_session >= since``. Caught.
    """
    _write_bars(db_session, 1700, [("2026-10-23", "1.77"), ("2026-10-26", "0.0419")])

    scan = scan_record(db_session, since=dt.date(2026, 10, 26))

    assert len(scan.findings) == 1, "the pair spanning the boundary must survive the filter"


def _write_bars(session: Session, instrument_id: int, rows: list[tuple[str, str]]) -> None:
    from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
    from tradeit.storage.tables import OhlcvBar

    for day, close in rows:
        session_date = dt.date.fromisoformat(day)
        session.add(
            OhlcvBar(
                instrument_id=instrument_id,
                timeframe=str(Bartimeframe.D1),
                session_date=session_date,
                event_time=dt.datetime.combine(session_date, dt.time(20), tzinfo=dt.UTC),
                knowledge_time=dt.datetime(2026, 11, 15, tzinfo=dt.UTC),
                knowledge_source=str(KnowledgeTimeSource.VENDOR_INGEST),
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal(1000),
                source="eodhd",
            )
        )
    session.flush()


def _load_daily():  # type: ignore[no-untyped-def]
    import importlib.util
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "forward_daily_discontinuity_undertest", repo / "ops" / "forward_daily.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_nightly_report_never_fails_the_job(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """**The rule that makes it safe to add to an unattended job at all.**

    A report that failed the capture would make the job go red for a *correct
    observation* -- and a job that reports failure when it is working is a job
    whose output gets switched off. So any error is printed and the run stands.

    Here the record is a file that is not a database, which is the realistic
    failure: a torn snapshot, a truncated write, a half-copied file.

    Mutant: ``raise`` in place of the ``except`` body. Caught.
    """
    daily = _load_daily()
    daily.OUT = tmp_path
    (tmp_path / "forward.sqlite").write_text("this is not a database")

    daily.report_discontinuities()  # must not raise

    assert "discontinuity scan failed" in capsys.readouterr().err


def test_the_nightly_report_is_silent_when_there_is_no_record(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """No record yet is not an error -- it is the first night.

    Mutant: drop the ``record.exists()`` check. Caught -- sqlite creates the
    file, the scan reports 0 bars, and the first night's output gains a line
    claiming a clean record where there is no record.
    """
    daily = _load_daily()
    daily.OUT = tmp_path

    daily.report_discontinuities()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "discontinuity scan" not in captured.out
