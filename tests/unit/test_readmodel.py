"""The corpus summary that the listing views read.

**Measured 2026-11-17: a corpus-wide count takes 2 minutes 31 seconds** over
83.8M price rows, so a list cannot be served from a scan. These tests protect
the properties that make a cache safe to read.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.api.securities_route import securities_payload
from tradeit.research01.readmodel import TABLE, build_summary, summary_state
from tradeit.storage.tables import Base


@pytest.fixture
def corpus(tmp_path: object) -> sessionmaker[Session]:
    dsn = f"sqlite:///{tmp_path / 'corpus.sqlite'}"  # type: ignore[operator]
    engine = create_engine(dsn, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(
            text(
                "create view v_security_tickers as select a.security_id, "
                "s.issuer_id, a.alias_value as ticker, a.valid_from as first_day, "
                "case when a.valid_to is null then null "
                "     else date(a.valid_to, '-1 day') end as last_day, "
                "a.valid_to is null as is_current, a.knowledge_source as evidence, "
                "a.citation from symbol_aliases a join securities s "
                "on s.security_id = a.security_id where a.alias_kind = 'ticker'"
            )
        )
        c.execute(
            text(
                "insert into issuers (issuer_id, display_name, source) "
                "values (1, 'ACME CORP', 'edgar'), (2, 'GHOST CORP', 'edgar')"
            )
        )
        for sid, issuer in ((1, 1), (2, 2)):
            c.execute(
                text(
                    "insert into securities (security_id, issuer_id, security_type, "
                    "currency, source) values (:s, :i, 'common', 'USD', 'edgar')"
                ),
                {"s": sid, "i": issuer},
            )
    return sessionmaker(bind=engine, future=True)


def _ticker(factory: sessionmaker[Session], sid: int, ticker: str) -> None:
    with factory() as s:
        s.execute(
            text(
                "insert into symbol_aliases (security_id, alias_kind, alias_value, "
                "valid_from, knowledge_time, knowledge_source, source) "
                "values (:s, 'ticker', :t, '1990-01-01', '1990-01-01', 'sec', 'sec')"
            ),
            {"s": sid, "t": ticker},
        )
        s.commit()


def _bar(factory: sessionmaker[Session], sid: int, day: str, close: float, volume: int) -> None:
    with factory() as s:
        s.execute(
            text(
                "insert into security_price_facts (security_id, session_date, "
                "adjustment_basis, open, high, low, close, volume, volume_adjusted, "
                "event_time, knowledge_time, knowledge_source, knowledge_time_basis, "
                "source) values (:s, :d, 'raw', :c, :c, :c, :c, :v, :v, :d, :d, "
                "'vendor', 'session_close', 'vendor')"
            ),
            {"s": sid, "d": day, "c": close, "v": volume},
        )
        s.commit()


def test_a_summary_that_has_not_been_built_says_so(
    corpus: sessionmaker[Session],
) -> None:
    """Not an empty list. An empty list reads as "there are no securities",
    which is a claim about the corpus rather than about this cache."""
    with corpus() as s:
        state = summary_state(s)
    assert state["built"] is False
    assert "build it with" in state["note"]  # type: ignore[operator]

    with corpus() as s:
        body = securities_payload(s)
    assert body["available"] is False
    assert body["securities"] == []


def test_the_summary_reports_its_own_age(corpus: sessionmaker[Session]) -> None:
    """**The point of carrying a build time.**

    A list served from a cache with no build time is a set of numbers of
    unknown vintage being read as current — the same failure the capture
    health view exists to prevent, in a different place.
    """
    _ticker(corpus, 1, "ACME")
    with corpus() as s:
        build_summary(s)
        state = summary_state(s)
    assert state["built"] is True
    assert "age_days" in state
    assert state["age_days"] == pytest.approx(0, abs=0.01)
    assert "not a live read of the corpus" in state["note"]  # type: ignore[operator]


def test_rows_that_never_traded_are_counted_apart_from_rows_that_did(
    corpus: sessionmaker[Session],
) -> None:
    """**The defect a list view found and a detail view never would.**

    ASKE holds 329 rows that are all `999999.9999` at zero volume — a vendor
    sentinel stored as a price — and `v_prices_raw` returns nothing for it.
    A summary carrying only the raw row count would have said "329 rows, last
    close $1,000,000" about a series the chart declines to draw. Measured
    2026-11-17: 26 securities in the corpus are in that state.
    """
    _ticker(corpus, 1, "REAL")
    _ticker(corpus, 2, "GHOST")
    _bar(corpus, 1, "2020-01-02", 10.0, 1_000)
    _bar(corpus, 1, "2020-01-03", 11.0, 1_200)
    _bar(corpus, 2, "2020-01-02", 999_999.9999, 0)
    _bar(corpus, 2, "2020-01-03", 999_999.9999, 0)

    with corpus() as s:
        build_summary(s)
        rows = dict(
            s.execute(
                text(f"select ticker, priced_rows from {TABLE} where ticker is not null")
            ).all()
        )
        held = dict(
            s.execute(
                text(f"select ticker, price_rows from {TABLE} where ticker is not null")
            ).all()
        )
    assert held["GHOST"] == 2, "the rows are there"
    assert rows["GHOST"] == 0, "and none of them traded"
    assert rows["REAL"] == 2


def test_the_listing_hides_securities_whose_rows_never_traded(
    corpus: sessionmaker[Session],
) -> None:
    """A row that is not a price is a dead end: it cannot be charted or
    valued, and listing it invites a reader to open nothing."""
    _ticker(corpus, 1, "REAL")
    _ticker(corpus, 2, "GHOST")
    _bar(corpus, 1, "2020-01-02", 10.0, 1_000)
    _bar(corpus, 2, "2020-01-02", 999_999.9999, 0)

    with corpus() as s:
        build_summary(s)
        body = securities_payload(s)
    tickers = [row["ticker"] for row in body["securities"]]  # type: ignore[union-attr]
    assert "REAL" in tickers
    assert "GHOST" not in tickers


def test_the_listing_orders_by_coverage_and_says_it_is_not_a_ranking(
    corpus: sessionmaker[Session],
) -> None:
    """A directory, not a recommendation. Ordering by how much the corpus
    holds must not be read as ordering by how attractive anything is."""
    _ticker(corpus, 1, "BIG")
    _ticker(corpus, 2, "SMALL")
    for day in ("2020-01-02", "2020-01-03", "2020-01-06"):
        _bar(corpus, 1, day, 10.0, 100)
    _bar(corpus, 2, "2020-01-02", 10.0, 100)

    with corpus() as s:
        build_summary(s)
        body = securities_payload(s)
    assert [row["ticker"] for row in body["securities"]] == ["BIG", "SMALL"]  # type: ignore[union-attr]
    assert "not a ranking" in body["note"]  # type: ignore[operator]


def test_search_matches_a_ticker_or_a_company_name(
    corpus: sessionmaker[Session],
) -> None:
    _ticker(corpus, 1, "ACME")
    _bar(corpus, 1, "2020-01-02", 10.0, 100)
    with corpus() as s:
        build_summary(s)
        by_ticker = securities_payload(s, query="ACM")
        by_name = securities_payload(s, query="acme corp")
        miss = securities_payload(s, query="zzz")
    assert by_ticker["returned"] == 1
    assert by_name["returned"] == 1
    assert miss["returned"] == 0


def test_an_unknown_sort_falls_back_rather_than_reaching_the_query(
    corpus: sessionmaker[Session],
) -> None:
    """The sort key arrives from a query parameter, so it is looked up in an
    allow-list rather than interpolated."""
    _ticker(corpus, 1, "ACME")
    _bar(corpus, 1, "2020-01-02", 10.0, 100)
    with corpus() as s:
        build_summary(s)
        body = securities_payload(s, sort="; drop table security_summary --")
    assert body["sort"] == "priced_rows"
    assert body["returned"] == 1


def test_a_rebuild_replaces_rather_than_accumulates(
    corpus: sessionmaker[Session],
) -> None:
    """Rebuilt whole, deliberately. An incremental refresh would need to know
    which securities changed, which means a scan — the thing being avoided."""
    _ticker(corpus, 1, "ACME")
    with corpus() as s:
        first = build_summary(s)
        second = build_summary(s)
        total = s.execute(text(f"select count(*) from {TABLE}")).scalar_one()
    assert total == first.securities == second.securities
    assert second.built_at >= first.built_at
