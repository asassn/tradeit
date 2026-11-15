"""Reading the filing index for a silence, and the paths that get it wrong.

The classifier in `test_forward_silence.py` is fed filings. These tests cover
how those filings are *found* -- which quarters are read, how the index's age is
established, and the two path bugs that made the wiring fail before it worked.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tradeit.cli_forward import _sqlite_url
from tradeit.forward.silence import load_index_filings, quarters_covering

#: A two-row index in EDGAR's fixed-width form.idx layout.
#: A two-row index in EDGAR's **fixed-width** ``form.idx`` layout, kept in a
#: file rather than inline. The parser slices by column, so the widths are
#: load-bearing and must stay verbatim -- `CLAUDE.md`'s rule that quoted text is
#: never normalised applies to a layout as much as to a form type. Inline, ruff
#: would flag the lines as over-length and the obvious fix would be to reflow
#: them, which silently breaks the thing under test.
FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form-idx-sample.idx"
).read_text(encoding="latin-1")


def _index(root: Path, year: int, quarter: int, text: str) -> None:
    path = root / str(year) / f"QTR{quarter}"
    path.mkdir(parents=True)
    (path / "form.idx").write_text(text, encoding="latin-1")


def test_only_the_quarters_covering_the_window_are_named() -> None:
    """The archive is 32 years and about 1.3 GB; a silence is days old.

    Mutant: return every quarter from 1994. Caught -- and in practice it is the
    difference between a nightly report that takes seconds and one somebody
    turns off.
    """
    assert quarters_covering(dt.date(2026, 5, 1), dt.date(2026, 11, 15)) == [
        (2026, 2),
        (2026, 3),
        (2026, 4),
    ]


def test_the_window_crosses_a_year_boundary() -> None:
    """Mutant: increment the quarter without rolling the year. Caught -- it
    loops forever, which a test that only ever stays inside one year never
    reaches."""
    assert quarters_covering(dt.date(2025, 11, 2), dt.date(2026, 2, 3)) == [
        (2025, 4),
        (2026, 1),
    ]


def test_a_backwards_window_is_read_rather_than_returning_nothing() -> None:
    """A caller that swaps the arguments gets the quarters, not silence.

    Returning `[]` would make the report say "the index is empty" about an index
    that is fine -- a wrong answer blamed on the data.

    Mutant: drop the swap. Caught.
    """
    assert quarters_covering(dt.date(2026, 11, 15), dt.date(2026, 5, 1)) == [
        (2026, 2),
        (2026, 3),
        (2026, 4),
    ]


def test_the_index_age_counts_rows_it_did_not_keep(tmp_path: Path) -> None:
    """**The age is a property of the index, not of the registrants asked about.**

    A caller narrowing to four CIKs still needs to know how current the whole
    index is, because that is what decides whether any answer may be trusted. If
    age were measured only from the rows kept, asking about a company that stops
    filing would make the index look stale and the report would blame the wrong
    thing.

    Mutant: compute `newest` after the `wanted` filter. Caught -- the index
    looks like it ends 2026-11-06 rather than 2026-11-12.
    """
    _index(tmp_path, 2026, 4, FIXTURE)

    filings, through = load_index_filings(
        tmp_path, since=dt.date(2026, 10, 1), until=dt.date(2026, 11, 15), ciks=[104894]
    )

    assert through == dt.date(2026, 11, 12), "the newest row read, not the newest row kept"
    assert set(filings) == {104894}, "only the CIK asked about is retained"


def test_a_missing_quarter_lowers_the_age_rather_than_raising(tmp_path: Path) -> None:
    """**This is how the three-month staleness becomes visible.**

    An absent quarter is a coverage fact, not an error. On 2026-11-15 the 2026
    QTR4 file simply did not exist, and the only honest consequence is that the
    index reaches an older date -- which `classify_silence` then refuses to
    answer from.

    Mutant: raise on a missing quarter. Caught -- the nightly report dies
    instead of reporting the gap.
    """
    _index(tmp_path, 2026, 3, FIXTURE)  # QTR4 deliberately absent

    filings, through = load_index_filings(
        tmp_path, since=dt.date(2026, 7, 1), until=dt.date(2026, 11, 15)
    )

    assert through == dt.date(2026, 11, 12)
    assert filings, "the quarter that does exist is still read"


def test_an_empty_index_reports_no_age_rather_than_today(tmp_path: Path) -> None:
    """No rows at all must not read as "current".

    Mutant: default `newest` to today. Caught -- an empty archive would license
    every verdict in the report.
    """
    filings, through = load_index_filings(
        tmp_path, since=dt.date(2026, 7, 1), until=dt.date(2026, 11, 15)
    )

    assert through is None
    assert filings == {}


@pytest.mark.parametrize("given", ["research01.sqlite", "./research01.sqlite"])
def test_a_relative_database_path_resolves_before_it_becomes_a_url(given: str) -> None:
    """**The bug that broke this wiring on its first run.**

    `sqlite:///` plus a relative path is read against the process's working
    directory; `sqlite:////` plus one is read from the filesystem root. So
    `sqlite:////research01.sqlite` looks for `/research01.sqlite` and fails with
    "unable to open database file" -- an error that never names the path it
    tried. The default corpus argument is a relative name, so the four-slash
    form that is right for an absolute path is wrong here.

    Mutant: `f"sqlite:////{path}"`. Caught.
    """
    url = _sqlite_url(Path(given))

    assert url.startswith("sqlite:////"), "an absolute path keeps its own leading slash"
    assert "/research01.sqlite" in url
    assert url != "sqlite:////research01.sqlite", "that is the filesystem root, not here"
    assert Path(url.removeprefix("sqlite:///")).is_absolute()


def _load_daily():  # type: ignore[no-untyped-def]
    import importlib.util

    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "forward_daily_silence_undertest", repo / "ops" / "forward_daily.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_nightly_silence_report_never_fails_the_job(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same rule as the snapshot and the discontinuity scan.

    A report that failed the capture would turn a correct observation into a red
    run, and a job that goes red when it is working is a job whose output gets
    switched off. Here the record is a file that is not a database -- the
    realistic failure.

    Mutant: `raise` in place of the `except` body. Caught.
    """
    daily = _load_daily()
    daily.OUT = tmp_path
    (tmp_path / "forward.sqlite").write_text("this is not a database")

    daily.report_silences()  # must not raise

    captured = capsys.readouterr()
    assert "silence report failed" in captured.err or captured.err == "", (
        "either it reported the failure or it declined to run; never a traceback"
    )


def test_the_nightly_silence_report_is_silent_without_a_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Identity lives in the corpus, so without one there is nothing to say.

    Not an error: the forward record deliberately holds no issuers, and a
    machine without the corpus simply cannot answer this question.

    Mutant: drop the `corpus.exists()` check. Caught -- it reports a failure
    every night on any machine without the 68 GB corpus, which trains the
    operator to ignore the report.
    """
    daily = _load_daily()
    daily.OUT = tmp_path
    daily.REPO = tmp_path  # no research01.sqlite here
    (tmp_path / "forward.sqlite").write_text("x")

    daily.report_silences()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "silences" not in captured.out
