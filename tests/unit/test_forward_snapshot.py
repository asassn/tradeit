"""The forward record's nightly snapshot, and the rule that it may never cost a capture.

**This is the one dataset in the project that cannot be rebuilt.** The corpus
can be reconstructed from EDGAR and vendor pulls given time and money; a
session not captured is gone at any price, which is the argument the whole
forward programme rests on.

Measured 2026-11-15: the record held 17 sessions and 3,269 symbol-sessions in a
single 3 MB file with **no backup of any kind** -- not in git, and `tmutil`
reported no Time Machine destination configured on the machine at all.

So `forward_daily.py` snapshots it before each capture. These tests hold the
two properties that make that safe rather than decorative.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_daily():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "forward_daily_undertest", REPO / "ops" / "forward_daily.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_failing_snapshot_does_not_stop_the_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**The rule that makes the backup safe to add at all.**

    A snapshot that raised would trade the thing being protected for the
    protection: the capture would abort, and a session not captured cannot be
    bought back at any price. So any backup error is reported and the run
    continues.

    Here the backup directory is pointed at a path that cannot be created --
    it is a *file* -- so `mkdir` fails.
    """
    daily = _load_daily()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("this is a file, so mkdir must fail")
    daily.BACKUPS = blocked
    daily.OUT = tmp_path

    daily.snapshot_record()  # must not raise

    captured = capsys.readouterr()
    assert "SNAPSHOT FAILED" in captured.err, "a silent backup failure is worse than none"
    assert "continuing with the capture" in captured.err


def test_the_snapshot_is_consistent_and_rotates(tmp_path: Path) -> None:
    """Uses sqlite's backup API, not a file copy, and keeps a bounded history.

    A plain copy of a database with a writer mid-transaction can be torn, and
    **a torn backup is worse than none because it looks like a backup**. The
    copy is therefore made through sqlite itself and read back here to prove it
    opens.
    """
    daily = _load_daily()
    out = tmp_path / "out"
    out.mkdir()
    record = out / "forward.sqlite"
    connection = sqlite3.connect(record)
    connection.execute("create table forward_observations (symbol text, session_date text)")
    connection.executemany(
        "insert into forward_observations values (?, ?)",
        [("AAPL", "2026-11-13"), ("MSFT", "2026-11-13")],
    )
    connection.commit()
    connection.close()

    daily.OUT = out
    daily.BACKUPS = tmp_path / "backups"
    daily.KEEP_BACKUPS = 3

    for _ in range(5):
        daily.snapshot_record()

    snapshots = sorted((tmp_path / "backups").glob("forward-*.sqlite"))
    assert len(snapshots) <= daily.KEEP_BACKUPS, "snapshots are not being rotated"
    assert snapshots, "no snapshot was taken"

    rows = (
        sqlite3.connect(snapshots[-1])
        .execute("select count(*) from forward_observations")
        .fetchone()[0]
    )
    assert rows == 2, "the snapshot does not open as a database with the rows in it"


def test_a_missing_record_is_reported_rather_than_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """First run, or a relocated record. Neither is an error worth aborting for."""
    daily = _load_daily()
    daily.OUT = tmp_path / "empty"
    daily.OUT.mkdir()
    daily.BACKUPS = tmp_path / "backups"

    daily.snapshot_record()

    assert "no record to snapshot yet" in capsys.readouterr().out


def _unused() -> None:  # pragma: no cover - keeps `sys` imported for the loader
    _ = sys
