"""One command that reports the whole system's state.

Phase 12 asks for monitoring, and before that is worth writing there has to be
a single place answering *what is this system's state right now*. These tests
protect the three things that made the first version of it misleading.
"""

from __future__ import annotations

import argparse
import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.cli_status import _age_in_days, _redact, _scalar, _scalar_raw, cmd_status
from tradeit.storage.tables import Base


def _args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {"db": None, "deep": False, "as_of": None}
    base.update(overrides)
    return argparse.Namespace(**base)


def test_credentials_are_redacted_from_the_printed_dsn() -> None:
    """A status command is exactly the output somebody pastes into a chat
    window to ask for help."""
    redacted = _redact("postgresql+psycopg://tradeit:secret@localhost:5432/tradeit")
    assert "secret" not in redacted
    assert "localhost:5432/tradeit" in redacted


def test_a_dsn_with_no_credentials_is_left_alone() -> None:
    assert _redact("sqlite:///research01.sqlite") == "sqlite:///research01.sqlite"


def test_one_failed_probe_does_not_misreport_the_next(tmp_path: object) -> None:
    """The defect that made the first version misleading.

    A failed statement aborts the transaction on PostgreSQL, so the probe after
    it returned "current transaction is aborted" and was classified as
    `unreadable` -- the first row said "no such table" and five identical
    situations below it said something else. Each probe must report its own
    reason.
    """
    engine = create_engine("sqlite://", future=True)
    with Session(engine) as session:
        session.execute(text("create table present (id integer)"))
        session.execute(text("insert into present values (1), (2)"))
        # Committed, because `_scalar` rolls back on failure and uncommitted
        # rows would vanish with it. That is the documented precondition: the
        # session must be one the caller owns, which is why `cmd_status` opens
        # its own rather than accepting one.
        session.commit()
        assert _scalar(session, "select count(*) from absent") == "no such table"
        # The next probe must still work.
        assert _scalar(session, "select count(*) from present") == 2
        assert _scalar(session, "select count(*) from also_absent") == "no such table"
        assert _scalar(session, "select count(*) from present") == 2


def test_a_missing_table_is_never_reported_as_zero() -> None:
    """A missing table and an empty one send somebody to different places:
    one is a missing migration, the other is lost rows."""
    engine = create_engine("sqlite://", future=True)
    with Session(engine) as session:
        session.execute(text("create table empty_table (id integer)"))
        assert _scalar(session, "select count(*) from empty_table") == 0
        assert _scalar(session, "select count(*) from absent") == "no such table"


def test_corpus_age_parses_a_stored_string_date() -> None:
    """SQLite has no date type, so session_date arrives as a string -- and this
    project has already written zero rows once by passing a date to raw SQL."""
    assert _age_in_days("2026-09-14", "2026-10-18") == 34
    assert _age_in_days("2026-09-14 00:00:00", "2026-10-18") == 34


def test_an_unparseable_date_gives_none_rather_than_crashing() -> None:
    assert _age_in_days("not-a-date", "2026-10-18") is None


def test_the_default_run_asks_nothing_expensive(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """The design claim, as a test.

    The first version ran `count(*)` and `max(session_date)` over
    security_price_facts and had to be killed: on the real corpus `max(rowid)`
    returns in 0.01s and `max(session_date)` takes 134 seconds. A status command
    that takes two minutes does not get run.
    """
    path = tmp_path / "s.sqlite"  # type: ignore[operator]
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)

    assert cmd_status(_args(db=f"sqlite:///{path}")) == 0
    out = capsys.readouterr().out
    assert "price facts (upper bound)" in out
    assert "max(rowid)" in out
    assert "price facts (exact)" not in out
    assert "--deep" in out


def test_deep_adds_the_exact_counts(tmp_path: object, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "s.sqlite"  # type: ignore[operator]
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)

    assert cmd_status(_args(db=f"sqlite:///{path}", deep=True)) == 0
    out = capsys.readouterr().out
    assert "price facts (exact)" in out
    assert "securities with prices" in out


def test_the_database_actually_read_is_always_named(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not decoration. The configured DSN and the research-01 corpus are
    different databases, so a command that read one silently while a reader
    assumed the other would report zero price facts and send somebody hunting a
    data loss that never happened.
    """
    path = tmp_path / "s.sqlite"  # type: ignore[operator]
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)

    cmd_status(_args(db=f"sqlite:///{path}"))
    out = capsys.readouterr().out
    assert "database read" in out
    assert str(path) in out


def test_the_interlock_is_reported_as_engaged_not_as_a_missing_feature(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.sqlite"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(f"sqlite:///{path}", future=True))
    cmd_status(_args(db=f"sqlite:///{path}"))
    out = capsys.readouterr().out
    assert "live trading authorised" in out
    # Whichever way it reads on this machine, the framing must be present.
    assert "interlock" in out.lower()


def test_unapproved_thresholds_are_named_as_blocking(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.sqlite"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(f"sqlite:///{path}", future=True))
    cmd_status(_args(db=f"sqlite:///{path}"))
    out = capsys.readouterr().out
    assert "thresholds awaiting approval" in out
    assert "blocks promotion" in out


def test_an_unreachable_database_reports_it_and_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cmd_status(_args(db="postgresql+psycopg://nobody@127.0.0.1:1/none")) == 1
    assert "unreachable" in capsys.readouterr().out


class _AbortingSession:
    """A session that fails like PostgreSQL does, and records rollbacks.

    Needed because **SQLite does not abort a transaction on a failed
    statement**, so the test above passes with or without the rollback: a
    mutant that removed it survived. The behaviour the rollback exists for only
    appears on PostgreSQL, which the unit suite does not run, so it is
    exercised directly rather than left to a backend that cannot show it.
    """

    def __init__(self) -> None:
        self.rollbacks = 0

    def execute(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError('relation "absent" does not exist')

    def rollback(self) -> None:
        self.rollbacks += 1


def test_a_failed_probe_rolls_back_so_the_next_one_is_not_poisoned() -> None:
    session = _AbortingSession()
    assert _scalar(session, "select count(*) from absent") == "no such table"  # type: ignore[arg-type]
    assert session.rollbacks == 1, (
        "no rollback: on PostgreSQL the next probe would report 'current transaction "
        "is aborted' and be misclassified, which is what made the first version of "
        "this command misleading"
    )


def test_a_failed_raw_probe_also_rolls_back() -> None:
    session = _AbortingSession()
    assert _scalar_raw(session, "select max(session_date) from absent") is None  # type: ignore[arg-type]
    assert session.rollbacks == 1


def test_the_forward_record_reports_freshness_not_just_presence(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """The failure a scheduled job actually has.

    A daily capture that silently stops leaves a record that simply ceases to
    grow, with no error anywhere. The age of the most recent ask is the one
    number that catches it, so `status` reports the timestamp and its age
    rather than only a row count.
    """
    import datetime as dt
    from decimal import Decimal

    from tradeit.storage.tables import ForwardObservation

    path = tmp_path / "f.sqlite"  # type: ignore[operator]
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            ForwardObservation(
                symbol="AAPL",
                session_date=dt.date(2026, 10, 20),
                asked_at=dt.datetime(2026, 10, 20, 22, 10, tzinfo=dt.UTC),
                status="ok",
                rows_returned=5,
            )
        )
        session.add(
            ForwardObservation(
                symbol="GONE",
                session_date=dt.date(2026, 10, 20),
                asked_at=dt.datetime(2026, 10, 20, 22, 10, tzinfo=dt.UTC),
                status="empty",
                rows_returned=0,
            )
        )
        session.commit()
    _ = Decimal

    cmd_status(_args(db=f"sqlite:///{path}", as_of="2026-10-27"))
    out = capsys.readouterr().out
    assert "forward observations" in out
    # These rows are dated 2026-10-20, before `SESSION_COUNTS_FROM`, so they
    # are window-count rows and the command must say so rather than counting
    # their zero as a delisting. The label changed from "of which silent" when
    # that distinction arrived; the subject of this test is the age below.
    assert "earlier rows" in out
    assert "silent, interpretable only" in out
    assert "most recent ask" in out
    assert "7 days" in out, "a stale record did not report its age"


def test_an_unrun_capture_says_so_rather_than_reading_as_healthy(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "f.sqlite"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(f"sqlite:///{path}", future=True))
    cmd_status(_args(db=f"sqlite:///{path}"))
    out = capsys.readouterr().out
    assert "has not run against this record" in out


def test_a_pre_cutoff_silence_is_not_reported_as_delisting_evidence(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """**The number this command used to assert and the record did not hold.**

    It printed the total observation count beside *"of which silent: asked and
    got nothing: what a delisting is made of"*. Before
    `SESSION_COUNTS_FROM`, `rows_returned` counted the whole fetch window, so a
    zero there meant "nothing in seven days" and said nothing about the session
    the row is dated to -- and two of the record's four zeros were tickers dead
    since 2018 and 2022, carrying no survivorship information at all.

    So a reader was being handed delisting evidence that did not exist, by the
    one command written to report the system honestly.

    The silence count is now taken from interpretable rows only. Here the
    pre-cutoff row is silent and the post-cutoff row is not, so a command that
    still counted everything would report one silence where the honest answer
    is zero.
    """
    from tradeit.cli_status import SESSION_COUNTS_FROM
    from tradeit.storage.tables import ForwardObservation

    path = tmp_path / "fwd.sqlite"  # type: ignore[operator]
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as session:
        session.add_all(
            [
                # Window-count era: zero rows across seven days.
                ForwardObservation(
                    symbol="OLD",
                    session_date=dt.date(2026, 10, 21),
                    asked_at=dt.datetime(2026, 10, 21, 16, 39, tzinfo=dt.UTC),
                    status="empty",
                    rows_returned=0,
                ),
                # Session-count era: the session printed.
                ForwardObservation(
                    symbol="NEW",
                    session_date=dt.date(2026, 10, 22),
                    asked_at=dt.datetime(2026, 10, 24, 2, 10, tzinfo=dt.UTC),
                    status="ok",
                    rows_returned=1,
                ),
            ]
        )
        session.commit()

    assert cmd_status(_args(db=f"sqlite:///{path}")) == 0
    out = capsys.readouterr().out

    assert "forward observations" in out
    assert "of which interpretable" in out
    assert "earlier rows" in out, "the window-count rows must be named, not folded into the total"
    assert SESSION_COUNTS_FROM in out, "a reader cannot apply the rule without the cutoff"

    silent_line = next(line for line in out.splitlines() if "silent" in line)
    assert "0" in silent_line.split(), (
        f"a pre-cutoff window silence was counted as delisting evidence: {silent_line!r}"
    )
