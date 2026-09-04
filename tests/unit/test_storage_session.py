"""Engine configuration that several long-running jobs depend on.

Python's ``sqlite3`` defaults to a **five second** busy timeout -- measured
here rather than assumed, because the first version of this said zero and was
wrong. Five seconds is ample against another writer and useless against a long
reader: a ``pragma integrity_check`` over 45 GB holds its lock for half an hour,
and it killed a multi-hour paid backfill on its second symbol.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from tradeit.storage.session import SQLITE_BUSY_TIMEOUT_MS, install_sqlite_busy_timeout


def test_a_sqlite_engine_is_given_the_timeout_it_was_asked_for(tmp_path: Path) -> None:
    engine = install_sqlite_busy_timeout(
        create_engine(f"sqlite:///{tmp_path / 'x.sqlite'}"), timeout_ms=1234
    )
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 1234


def test_the_default_is_minutes_rather_than_the_driver_s_zero(tmp_path: Path) -> None:
    engine = install_sqlite_busy_timeout(create_engine(f"sqlite:///{tmp_path / 'y.sqlite'}"))
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == SQLITE_BUSY_TIMEOUT_MS
    assert SQLITE_BUSY_TIMEOUT_MS > 0


#: Measured from the driver, not read from documentation.
DRIVER_DEFAULT_MS = 5_000


def test_the_driver_default_is_seconds_which_is_the_whole_problem(tmp_path: Path) -> None:
    """Pins the number the helper exists to raise.

    If a future driver changes it this test says so, which is the only way the
    comment above stays true.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'z.sqlite'}")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == DRIVER_DEFAULT_MS
    assert SQLITE_BUSY_TIMEOUT_MS > DRIVER_DEFAULT_MS


def test_it_is_a_no_op_on_another_dialect() -> None:
    """Returned unchanged rather than raising: the scripts that call it may
    point at PostgreSQL, where the statement timeout is the relevant control."""
    engine = create_engine("postgresql+psycopg://u@localhost/x")
    assert install_sqlite_busy_timeout(engine) is engine
