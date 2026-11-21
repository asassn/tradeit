"""The nightly capture agent's surface.

Until 2026-11-17 the only live-updating data in this system had no HTTP route
at all, because `create_app` could open a record and a corpus and nothing
else. These tests hold the properties that make the view worth having.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tradeit.api import create_app
from tradeit.api.forward_route import SESSION_COUNTS_FROM, STALE_AFTER_DAYS
from tradeit.storage.tables import Base

NOW = dt.datetime(2026, 11, 17, 12, 0, tzinfo=dt.UTC)


def _forward(path: str, rows: list[tuple[str, str, str, str, int]]) -> str:
    dsn = f"sqlite:///{path}"
    engine = create_engine(dsn, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for symbol, session_date, asked_at, status, returned in rows:
            connection.execute(
                text(
                    "insert into forward_observations "
                    "(symbol, session_date, asked_at, status, rows_returned) "
                    "values (:s, :d, :a, :st, :r)"
                ),
                {"s": symbol, "d": session_date, "a": asked_at, "st": status, "r": returned},
            )
    return dsn


def _fresh(tmp_path: object) -> str:
    """A record captured recently, **relative to the real clock**.

    It has to be relative because the one test that uses it goes through
    `create_app`, which reads the wall clock; every other test here injects
    `as_of=NOW` instead. With the timestamps hardcoded at 2026-11-17 this
    fixture was "fresh" for exactly four days and then asserted the opposite of
    what it meant — it failed on 2026-11-21 because `STALE_AFTER_DAYS` is 4,
    not because anything regressed.

    Bumping the date would buy another four days. The invariant is *recent*,
    so the fixture says recent.
    """
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    asked_at = recent.strftime("%Y-%m-%d %H:%M:%S")
    session_date = (recent - dt.timedelta(days=1)).date().isoformat()
    # Comfortably past the staleness threshold, and still a real timestamp.
    old = recent - dt.timedelta(days=STALE_AFTER_DAYS + 25)
    return _forward(
        str(tmp_path / "forward.sqlite"),  # type: ignore[operator]
        [
            ("AAPL", session_date, asked_at, "ok", 1),
            ("MSFT", session_date, asked_at, "ok", 1),
            # Silent: the vendor returned nothing for a symbol that used to print.
            ("DEAD", session_date, asked_at, "ok", 0),
            # Older semantics: counted the fetch window, not the session.
            ("AAPL", old.date().isoformat(), old.strftime("%Y-%m-%d %H:%M:%S"), "ok", 5),
        ],
    )


def test_without_a_forward_record_the_route_says_so(tmp_path: object) -> None:
    """A different database from the decision record, and 501 rather than an
    empty payload — which would read as "the agent recorded nothing"."""
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    response = TestClient(create_app(record)).get("/forward/health")
    assert response.status_code == 501
    assert "different database" in response.json()["detail"]


def test_liveness_is_reported_as_an_age_not_a_count(tmp_path: object) -> None:
    """The failure mode of a scheduled job.

    It does not crash; it stops, and the record simply ceases to grow. A
    payload of healthy-looking totals with no age would show 5,176
    observations and a green tick while the agent had been dead a week.
    """
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    body = TestClient(create_app(record, None, _fresh(tmp_path))).get("/forward/health").json()
    assert "age_days" in body
    assert "latest_ask" in body
    assert body["stale"] is False


def test_an_agent_that_stopped_is_flagged(tmp_path: object) -> None:
    from sqlalchemy.orm import sessionmaker

    from tradeit.api.forward_route import forward_payload

    dsn = _forward(
        str(tmp_path / "stopped.sqlite"),  # type: ignore[operator]
        [("AAPL", "2026-10-01", "2026-10-01 03:00:00", "ok", 1)],
    )
    with sessionmaker(bind=create_engine(dsn, future=True), future=True)() as session:
        body = forward_payload(session, as_of=NOW)
    assert body["stale"] is True
    assert body["age_days"] > STALE_AFTER_DAYS


def test_a_weekend_gap_is_not_a_stopped_agent(tmp_path: object) -> None:
    """The threshold exists because the capture runs on trading sessions.

    A Friday-night ask is three days old by Monday morning and nothing is
    wrong. A one-day threshold would cry wolf every week, and an alert that
    fires every weekend is an alert nobody reads.
    """
    from sqlalchemy.orm import sessionmaker

    from tradeit.api.forward_route import forward_payload

    friday = NOW - dt.timedelta(days=3)
    dsn = _forward(
        str(tmp_path / "weekend.sqlite"),  # type: ignore[operator]
        [("AAPL", "2026-11-13", friday.strftime("%Y-%m-%d %H:%M:%S"), "ok", 1)],
    )
    with sessionmaker(bind=create_engine(dsn, future=True), future=True)() as session:
        body = forward_payload(session, as_of=NOW)
    assert body["stale"] is False


def test_a_silent_symbol_session_is_evidence_not_a_failure(tmp_path: object) -> None:
    """It is what a delisting is made of.

    Counting it as a capture failure would train an operator to ignore the
    one signal this record exists to produce.
    """
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    body = TestClient(create_app(record, None, _fresh(tmp_path))).get("/forward/health").json()
    assert body["silent_observations"] == 1
    assert "evidence" in body["silent_note"]


def test_rows_with_the_older_semantics_are_counted_apart(tmp_path: object) -> None:
    """`rows_returned` counted the fetch window before the cutover.

    Summing them into the evidence count would inflate it with rows that
    answer a different question, which is the kind of number that is wrong
    without ever looking wrong.
    """
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    body = TestClient(create_app(record, None, _fresh(tmp_path))).get("/forward/health").json()
    assert body["asks"] == 4
    assert body["interpretable_asks"] == 3
    assert body["earlier_asks"] == 1
    assert SESSION_COUNTS_FROM in body["earlier_note"]


def test_an_empty_forward_record_says_so_rather_than_erroring(tmp_path: object) -> None:
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    empty = _forward(str(tmp_path / "empty.sqlite"), [])  # type: ignore[operator]
    body = TestClient(create_app(record, None, empty)).get("/forward/health").json()
    assert body["asks"] == 0
    assert "no capture has run" in body["note"]


def test_health_says_whether_a_capture_record_is_attached(tmp_path: object) -> None:
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    assert TestClient(create_app(record)).get("/health").json()["forward_attached"] is False
    attached = TestClient(create_app(record, None, _fresh(tmp_path))).get("/health").json()
    assert attached["forward_attached"] is True
