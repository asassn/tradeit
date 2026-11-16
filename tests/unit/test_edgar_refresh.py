"""Refreshing the local EDGAR index without ever installing a worse file.

Measured 2026-11-15: the archive held filings only to 2026-08-13 -- QTR3
downloaded mid-quarter and never replaced, QTR4 absent -- and nothing reported
it, so the silence classifier answered "no exit found" about companies whose
deregistration filings were simply not there.

Fixing that introduces a new way to lose: a refresh that overwrites three good
months with a truncated download. These tests hold the two rules that stop it,
and **the fetch is injected**, because a truncated response and an SEC error
page cannot be produced on demand from the real endpoint -- which is exactly why
they would otherwise go untested until the night they happened.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tradeit.edgar.refresh import (
    RefreshAction,
    inspect_index,
    quarters_to_refresh,
    refresh_quarter,
)

HEADER = """Description:           Master Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    November 14, 2026
Comments:              webmaster@sec.gov

Form Type   Company Name                                   CIK         Date Filed  File Name
-----------------------------------------------------------------------------------------------
"""


def _idx(*rows: tuple[str, str, str]) -> str:
    body = "".join(
        f"{form:<12}{name:<47}{cik:<12}{day}  edgar/data/{cik}/x.txt\n"
        for form, name, cik, day in ((f, n, c, d) for f, (n, c, d) in ((r[0], r[1:]) for r in rows))
    )
    return HEADER + body


def _rows(count: int, *, day: str) -> str:
    return HEADER + "".join(
        f"{'8-K':<12}{'CO ' + str(n):<47}{1000 + n:<12}{day}  edgar/data/{1000 + n}/x.txt\n"
        for n in range(count)
    )


def _write(root: Path, year: int, quarter: int, text: str) -> Path:
    path = root / str(year) / f"QTR{quarter}"
    path.mkdir(parents=True, exist_ok=True)
    target = path / "form.idx"
    target.write_text(text, encoding="latin-1")
    return target


def _refresh(root: Path, superseded: Path, text: str | Exception, **kw):  # type: ignore[no-untyped-def]
    def fetch(year: int, quarter: int) -> str:
        if isinstance(text, Exception):
            raise text
        return text

    return refresh_quarter(
        2026, 4, root=root, superseded=superseded, fetch=fetch, stamp="20261115T000000Z", **kw
    )


# ---------------------------------------------------------------------------
# What a file holds, measured from its rows rather than its header.
# ---------------------------------------------------------------------------


def test_the_reach_is_read_from_rows_not_the_header() -> None:
    """The header is the file's claim about itself, and one went three months
    stale with nobody checking.

    Mutant: parse `Last Data Received`. Caught -- it says November 14 while the
    rows stop on the 12th.
    """
    snapshot = inspect_index(_rows(3, day="2026-11-12"))

    assert snapshot.newest == dt.date(2026, 11, 12)
    assert snapshot.dated_lines == 3


def test_an_impossible_date_in_the_header_is_not_counted() -> None:
    """EDGAR headers carry things like `9997-26-00`.

    Mutant: `date.fromisoformat` without the guard. Caught -- it raises, and a
    refresh that raises on a real SEC file is a refresh that never runs.
    """
    snapshot = inspect_index("Anonymous FTP: 9997-26-00\n" + _rows(1, day="2026-11-12"))

    assert snapshot.newest == dt.date(2026, 11, 12)


def test_a_response_with_no_dated_row_is_not_an_index() -> None:
    """Mutant: `usable` returns `dated_lines >= 0`. Caught."""
    assert not inspect_index("<html>503 Service Unavailable</html>").usable
    assert not inspect_index(HEADER).usable, "a header alone is not an index"


# ---------------------------------------------------------------------------
# Rule 1: never install a file that knows less.
# ---------------------------------------------------------------------------


def test_a_truncated_response_is_refused_and_the_good_file_stands(tmp_path: Path) -> None:
    """**The rule that makes an automatic refresh safe to run unattended.**

    Without it a partial download overwrites three months of index, and the
    nightly report then calls the archive current -- because the file it just
    wrote is new.

    Mutant: install whatever was fetched. Caught.
    """
    target = _write(tmp_path / "idx", 2026, 4, _rows(500, day="2026-11-12"))

    outcome = _refresh(tmp_path / "idx", tmp_path / "sup", _rows(3, day="2026-11-12"))

    assert outcome.action is RefreshAction.REFUSED
    assert "knows less" in outcome.reason
    assert inspect_index(target.read_text(encoding="latin-1")).dated_lines == 500


def test_a_file_reaching_further_but_holding_less_is_still_refused(tmp_path: Path) -> None:
    """**Both conditions, and this is why.**

    A partial response that happens to include one recent filing reaches further
    than what is held while holding a fraction of the rows. Comparing only the
    newest date would install it and delete most of the archive.

    Mutant: compare `newest` alone. Caught.
    """
    _write(tmp_path / "idx", 2026, 4, _rows(500, day="2026-11-12"))

    outcome = _refresh(tmp_path / "idx", tmp_path / "sup", _rows(4, day="2026-11-13"))

    assert outcome.action is RefreshAction.REFUSED


def test_a_failed_fetch_leaves_the_file_alone(tmp_path: Path) -> None:
    """A refresh that could not fetch must not be able to damage what is held.

    Mutant: let the exception escape. Caught -- the nightly job dies on a
    transient network error.
    """
    target = _write(tmp_path / "idx", 2026, 4, _rows(500, day="2026-11-12"))

    outcome = _refresh(tmp_path / "idx", tmp_path / "sup", TimeoutError("sec.gov timed out"))

    assert outcome.action is RefreshAction.FAILED
    assert "timed out" in outcome.reason
    assert inspect_index(target.read_text(encoding="latin-1")).dated_lines == 500


def test_a_fuller_file_is_installed(tmp_path: Path) -> None:
    """The ordinary case, which the rules above must not block.

    Mutant: refuse unless `newest` strictly increases. Caught -- a mid-quarter
    refresh on the same day adds rows without moving the date, which is most of
    what a daily refresh does.
    """
    target = _write(tmp_path / "idx", 2026, 4, _rows(10, day="2026-11-12"))

    outcome = _refresh(tmp_path / "idx", tmp_path / "sup", _rows(40, day="2026-11-12"))

    assert outcome.action is RefreshAction.INSTALLED
    assert inspect_index(target.read_text(encoding="latin-1")).dated_lines == 40


def test_a_first_download_is_installed_with_nothing_to_compare(tmp_path: Path) -> None:
    """QTR4 did not exist at all on 2026-11-15.

    Mutant: require an existing file. Caught -- the missing quarter stays
    missing forever.
    """
    outcome = _refresh(tmp_path / "idx", tmp_path / "sup", _rows(5, day="2026-11-13"))

    assert outcome.action is RefreshAction.INSTALLED
    assert (tmp_path / "idx" / "2026" / "QTR4" / "form.idx").exists()


def test_an_identical_file_is_left_alone(tmp_path: Path) -> None:
    """No superseded copy, no withdrawn list, no write.

    Mutant: reinstall regardless. Caught -- a daily refresh would otherwise
    accumulate an identical 23 MB superseded copy every night.
    """
    text = _rows(10, day="2026-11-12")
    _write(tmp_path / "idx", 2026, 4, text)

    outcome = _refresh(tmp_path / "idx", tmp_path / "sup", text)

    assert outcome.action is RefreshAction.UNCHANGED
    assert not (tmp_path / "sup").exists()


# ---------------------------------------------------------------------------
# Rule 2: never destroy a withdrawn line silently.
# ---------------------------------------------------------------------------


def test_lines_sec_no_longer_serves_are_preserved(tmp_path: Path) -> None:
    """**Measured, not hypothetical.** Replacing 2026 QTR3 by hand removed 15
    lines SEC no longer serves in that file.

    A withdrawn index line is evidence about EDGAR. A refresh that discarded it
    would be rewriting history to make a problem disappear, which this project
    forbids outright.

    Mutant: skip the superseded write. Caught.
    """
    held = _rows(10, day="2026-11-12")
    gone = held.splitlines()[-1]
    fresher = (
        "\n".join(held.splitlines()[:-1])
        + "\n"
        + "".join(
            f"{'8-K':<12}{'NEW ' + str(n):<47}{9000 + n:<12}"
            f"2026-11-13  edgar/data/{9000 + n}/x.txt\n"
            for n in range(5)
        )
    )
    _write(tmp_path / "idx", 2026, 4, held)

    outcome = _refresh(tmp_path / "idx", tmp_path / "sup", fresher)

    assert outcome.action is RefreshAction.INSTALLED
    assert gone in outcome.withdrawn
    saved = list((tmp_path / "sup").glob("2026-QTR4-form.idx.withdrawn-*.txt"))
    assert len(saved) == 1
    assert gone in saved[0].read_text(encoding="latin-1")
    superseded = list((tmp_path / "sup").glob("2026-QTR4-form.idx.superseded-*"))
    assert superseded and superseded[0].read_text(encoding="latin-1") == held


# ---------------------------------------------------------------------------
# Which quarters get refreshed.
# ---------------------------------------------------------------------------


def test_the_previous_quarter_is_refreshed_too(tmp_path: Path) -> None:
    """**This is the bug that caused the three-month gap.**

    QTR3 was downloaded mid-quarter on 2026-08-14 and never revisited, so it
    stopped at 2026-08-13 permanently. Refreshing only the current quarter would
    leave every quarter incomplete the moment the calendar turned.

    Mutant: return the current quarter alone. Caught.
    """
    assert quarters_to_refresh(dt.date(2026, 11, 15)) == [(2026, 3), (2026, 4)]


def test_the_first_quarter_reaches_back_into_last_year() -> None:
    """Mutant: `quarter - 1` without the year roll. Caught -- it asks for
    `QTR0`, which does not exist, so every January the previous quarter is
    never completed."""
    assert quarters_to_refresh(dt.date(2027, 1, 4)) == [(2026, 4), (2027, 1)]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (dt.date(2026, 1, 1), [(2025, 4), (2026, 1)]),
        (dt.date(2026, 3, 31), [(2025, 4), (2026, 1)]),
        (dt.date(2026, 4, 1), [(2026, 1), (2026, 2)]),
        (dt.date(2026, 12, 31), [(2026, 3), (2026, 4)]),
    ],
)
def test_quarter_boundaries(day: dt.date, expected: list[tuple[int, int]]) -> None:
    assert quarters_to_refresh(day) == expected


# ---------------------------------------------------------------------------
# The nightly wiring.
# ---------------------------------------------------------------------------


def _load_daily():  # type: ignore[no-untyped-def]
    import importlib.util

    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "forward_daily_refresh_undertest", repo / "ops" / "forward_daily.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_without_an_identifying_user_agent_sec_is_not_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**SEC fair-access guidance requires one**, and an unidentified automated
    client is both rude and liable to be blocked.

    Declining is not an error: the capture still runs and the silence report
    still prints the index's age, which will simply show it getting older.

    Mutant: fetch with an empty User-Agent. Caught.
    """
    daily = _load_daily()
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)

    daily.refresh_edgar_index()

    captured = capsys.readouterr()
    assert "no EDGAR_USER_AGENT" in captured.out
    # The header the fetch loop prints, not the words "filing index" -- the
    # decline message contains those too, so the first version of this
    # assertion failed against correct code.
    assert "--- filing index" not in captured.out, "it did not reach the fetch"


def test_the_refresh_never_fails_the_capture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same rule as the snapshot and the two reports.

    A session not captured cannot be bought back at any price, so nothing in
    this job may abort it -- least of all a housekeeping step.

    The failure is injected into `quarters_to_refresh`, which `refresh_quarter`
    does not guard, so this exercises the **outer** handler rather than the
    per-quarter one. The first version of this test pointed the index root at an
    unwritable path instead, which would have made a real request to sec.gov
    from the unit suite before getting anywhere near the error.

    Mutant: `raise` in place of the `except` body. Caught.
    """
    import tradeit.edgar.refresh as refresh_module

    daily = _load_daily()
    monkeypatch.setenv("EDGAR_USER_AGENT", "TradeIt test <nobody@example.invalid>")

    def boom(_today: dt.date) -> list[tuple[int, int]]:
        raise RuntimeError("the calendar broke")

    monkeypatch.setattr(refresh_module, "quarters_to_refresh", boom)

    daily.refresh_edgar_index()  # must not raise

    assert "index refresh failed" in capsys.readouterr().err


def test_a_quarter_that_cannot_be_fetched_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A transient network error is a line in the report, not a dead job.

    `fetch_quarter` is replaced, so nothing here touches the network.

    Mutant: let the fetch error escape `refresh_quarter`. Caught.
    """
    import tradeit.edgar.refresh as refresh_module

    daily = _load_daily()
    monkeypatch.setenv("EDGAR_USER_AGENT", "TradeIt test <nobody@example.invalid>")

    def unreachable(*_args: object, **_kw: object) -> str:
        raise TimeoutError("sec.gov timed out")

    monkeypatch.setattr(refresh_module, "fetch_quarter", unreachable)
    monkeypatch.setattr(refresh_module, "quarters_to_refresh", lambda _t: [(2026, 4)])

    daily.refresh_edgar_index()

    captured = capsys.readouterr()
    assert "failed" in captured.out and "timed out" in captured.out
    assert captured.err == "", "a fetch failure is reported in the run, not as an error"
