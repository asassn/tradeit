"""One security's price history, on the basis the caller names.

Neither basis is the default answer to every question, and the tests here
protect the properties that stop a chart teaching the reader the wrong thing
about their own data.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tradeit.api import create_app
from tradeit.api.prices_route import prices_payload
from tradeit.storage.tables import Base


@pytest.fixture
def corpus(tmp_path: object) -> str:
    """A corpus-shaped database carrying both price views."""
    dsn = f"sqlite:///{tmp_path / 'corpus.sqlite'}"  # type: ignore[operator]
    engine = create_engine(dsn, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        for name, basis in (("v_prices_raw", "raw"), ("v_prices", "total")):
            c.execute(
                text(
                    f"create view {name} as select security_id, session_date, "
                    "open, high, low, close, volume, knowledge_time "
                    "from security_price_facts "
                    f"where adjustment_basis = '{basis}'"
                )
            )
    return dsn


def _bar(dsn: str, day: dt.date, close: float, basis: str = "raw") -> None:
    with create_engine(dsn, future=True).begin() as c:
        c.execute(
            text(
                "insert into security_price_facts (security_id, session_date, "
                "adjustment_basis, open, high, low, close, volume, "
                "volume_adjusted, event_time, knowledge_time, "
                "knowledge_source, knowledge_time_basis, source) "
                "values (1, :d, :b, :c, :c, :c, :c, 100, 100, :d, :d, "
                "        'vendor', 'session_close', 'vendor')"
            ),
            {"d": day.isoformat(), "b": basis, "c": close},
        )


def _split(dsn: str, day: dt.date, ratio: float) -> None:
    with create_engine(dsn, future=True).begin() as c:
        c.execute(
            text(
                "insert into security_corporate_action_facts (security_id, "
                "action_type, ex_date, ratio, event_time, knowledge_time, "
                "knowledge_source, currency, source) "
                "values (1, 'split', :d, :r, :d, :d, 'vendor', 'USD', 'vendor')"
            ),
            {"d": day.isoformat(), "r": ratio},
        )


def _read(dsn: str, **kw: object) -> dict[str, object]:
    with sessionmaker(bind=create_engine(dsn, future=True), future=True)() as s:
        return prices_payload(s, 1, **kw)  # type: ignore[arg-type]


def test_the_two_bases_are_different_series_and_the_payload_says_which(
    corpus: str,
) -> None:
    """A chart that silently picked one would teach the reader the wrong thing.

    Raw is what the market printed and what a multiple needs; adjusted is
    comparable across time and is **not point-in-time**. On Apple the two
    differ by a factor of 112.
    """
    _bar(corpus, dt.date(2020, 1, 2), 100.0, "raw")
    _bar(corpus, dt.date(2020, 1, 3), 110.0, "raw")
    _bar(corpus, dt.date(2020, 1, 2), 25.0, "total")
    _bar(corpus, dt.date(2020, 1, 3), 27.5, "total")

    raw = _read(corpus, basis="raw")
    adjusted = _read(corpus, basis="total")
    assert raw["high"] == pytest.approx(110.0)
    assert adjusted["high"] == pytest.approx(27.5)
    assert "what a trader saw" in raw["basis_note"]  # type: ignore[index]
    assert "not point-in-time" in adjusted["basis_note"].lower()  # type: ignore[union-attr]


def test_the_extremes_are_measured_over_every_row_not_the_sample(
    corpus: str,
) -> None:
    """**The property that makes a sampled chart safe.**

    A chart that skipped the high and then reported the sample's high would be
    wrong in the one place a reader checks. The line is bucketed; the figures
    beside it are not.
    """
    # The spike sits at row 57 of 201. With points=20 the stride is 10, so the
    # sample keeps rows 1, 10, 20 … 200 and 201 — and misses it. The first
    # version of this test dated the spike after every other bar, which made
    # it the LAST row, which the sample always keeps: the mutant survived and
    # the fixture was the reason.
    SPIKE = 57
    for index in range(201):
        _bar(
            corpus,
            dt.date(2020, 1, 1) + dt.timedelta(days=index),
            999.0 if index == SPIKE else 10.0 + index * 0.01,
        )

    body = _read(corpus, points=20)
    assert all(
        point["session"] != str(dt.date(2020, 1, 1) + dt.timedelta(days=SPIKE))
        for point in body["series"]  # type: ignore[union-attr]
    ), "the fixture must place the spike outside the sample"
    assert body["sampled"] is True
    assert body["drawn"] < body["sessions"]  # type: ignore[operator]
    assert body["high"] == pytest.approx(999.0), "the exact high was lost to sampling"
    assert body["peak_close"]["session"] == str(dt.date(2020, 1, 1) + dt.timedelta(days=SPIKE))  # type: ignore[index]


def test_the_sample_keeps_the_first_and_last_session(corpus: str) -> None:
    """A series that started three months in would misdate the history."""
    for index in range(100):
        _bar(corpus, dt.date(2020, 1, 1) + dt.timedelta(days=index), 10.0 + index)
    body = _read(corpus, points=10)
    series = body["series"]
    assert series[0]["session"] == "2020-01-01"  # type: ignore[index]
    assert series[-1]["session"] == body["last_session"]  # type: ignore[index]


def test_splits_travel_with_the_series_on_either_basis(corpus: str) -> None:
    """On raw they explain the cliffs; on adjusted they explain why there are
    none. A raw chart with an unexplained 7:1 drop looks like a crash."""
    _bar(corpus, dt.date(2020, 8, 28), 500.0, "raw")
    _bar(corpus, dt.date(2020, 9, 1), 125.0, "raw")
    _bar(corpus, dt.date(2020, 8, 28), 125.0, "total")
    _bar(corpus, dt.date(2020, 9, 1), 125.0, "total")
    _split(corpus, dt.date(2020, 8, 31), 4.0)

    for basis in ("raw", "total"):
        body = _read(corpus, basis=basis)
        assert len(body["splits"]) == 1, basis  # type: ignore[arg-type]
        assert body["splits"][0]["ratio"] == pytest.approx(4.0)  # type: ignore[index]


def test_a_security_with_no_series_says_so_rather_than_drawing_nothing(
    corpus: str,
) -> None:
    body = _read(corpus)
    assert body["available"] is False
    assert "no raw price series" in body["note"]  # type: ignore[operator]


def test_an_unknown_basis_falls_back_to_raw_rather_than_erroring(
    corpus: str,
) -> None:
    """The route validates the parameter, so this is defence in depth — but a
    fallback to the *printed* series is the safe direction: raw is what
    happened, adjusted is a current belief about it."""
    _bar(corpus, dt.date(2020, 1, 2), 100.0, "raw")
    body = _read(corpus, basis="nonsense")
    assert body["available"] is True
    assert "what a trader saw" in body["basis_note"]  # type: ignore[index]


def test_the_route_rejects_a_basis_it_does_not_serve(corpus: str) -> None:
    record = corpus  # any database with the tables will do for the record side
    response = TestClient(create_app(record, corpus)).get("/securities/1/prices?basis=sneaky")
    assert response.status_code == 422


def test_without_a_corpus_the_route_names_the_missing_database(
    tmp_path: object,
) -> None:
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    response = TestClient(create_app(record)).get("/securities/1/prices")
    assert response.status_code == 501
    assert "different database" in response.json()["detail"]
