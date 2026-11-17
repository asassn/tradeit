"""Where a security's facts came from — the corpus's own argument.

Every test here protects a distinction that an ordinary data view would
collapse, and each collapse would read as reassurance rather than as a gap.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tradeit.api import create_app
from tradeit.api.provenance_route import provenance_payload
from tradeit.storage.tables import Base

CITATION = (
    "symbol bound to CIK 1498046 by 0001498046-11-000012 filed 2011-11-14: "
    '"Our common shares are quoted on the OTC Bulletin Board under the symbol '
    'AAAB". Interval start NOT evidenced by the filing.'
)


@pytest.fixture
def corpus(tmp_path: object) -> str:
    """A corpus-shaped database with the one view the route reads."""
    path = tmp_path / "corpus.sqlite"  # type: ignore[operator]
    dsn = f"sqlite:///{path}"
    engine = create_engine(dsn, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(
            text(
                "insert into issuers (issuer_id, display_name, source) "
                "values (1, 'ACME CORP', 'edgar')"
            )
        )
        c.execute(
            text(
                "insert into securities (security_id, issuer_id, security_type, "
                "currency, identity_evidence, source) "
                "values (1, 1, 'common', 'USD', 'filing', 'edgar')"
            )
        )
        # The view the route reads, with the interval arithmetic done: the
        # stored `valid_to` is exclusive, and reading it as inclusive is trap
        # 0.2 in the data dictionary.
        c.execute(
            text(
                "create view v_security_tickers as select "
                "a.security_id, s.issuer_id, a.alias_value as ticker, "
                "a.valid_from as first_day, "
                "case when a.valid_to is null then null "
                "     else date(a.valid_to, '-1 day') end as last_day, "
                "a.valid_to is null as is_current, "
                "a.knowledge_source as evidence, a.citation "
                "from symbol_aliases a join securities s "
                "on s.security_id = a.security_id where a.alias_kind = 'ticker'"
            )
        )
    return dsn


def _alias(dsn: str, **kw: object) -> None:
    with create_engine(dsn, future=True).begin() as c:
        c.execute(
            text(
                "insert into symbol_aliases (security_id, alias_kind, alias_value, "
                "valid_from, valid_to, knowledge_time, knowledge_source, citation, "
                "source) values (1, 'ticker', :v, :f, :t, :kt, :ks, :cite, 'edgar')"
            ),
            kw,
        )


def _read(dsn: str) -> dict[str, object]:
    with sessionmaker(bind=create_engine(dsn, future=True), future=True)() as s:
        return provenance_payload(s, 1)


def test_an_interval_travels_with_the_filing_that_evidenced_it(corpus: str) -> None:
    """An interval shown without its citation is an assertion.

    The evidence is frequently the most interesting thing on the page — a
    ticker bound to a CIK by a sentence in a 10-K — and truncating it would
    remove the reason to trust the row.
    """
    _alias(
        corpus,
        v="AAAB",
        f=dt.date(2011, 8, 9),
        t=dt.date(2018, 9, 12),
        kt=dt.datetime(2011, 11, 14),
        ks="edgar_filing_text",
        cite=CITATION,
    )
    body = _read(corpus)
    row = body["tickers"][0]  # type: ignore[index]
    assert row["citation"] == CITATION, "the citation was altered or truncated"
    assert row["evidence"] == "edgar_filing_text"


def test_the_exclusive_end_date_is_corrected_before_display(corpus: str) -> None:
    """`valid_to` is exclusive and `last_day` is inclusive.

    Trap 0.2 in the data dictionary. Shown uncorrected, every interval would
    claim one day it did not hold — small, systematic and invisible.
    """
    _alias(
        corpus,
        v="AAAB",
        f=dt.date(2011, 8, 9),
        t=dt.date(2018, 9, 12),
        kt=dt.datetime(2011, 11, 14),
        ks="edgar_filing_text",
        cite=CITATION,
    )
    row = _read(corpus)["tickers"][0]  # type: ignore[index]
    assert row["last_day"] == "2018-09-11"
    assert row["is_current"] is False


def test_a_current_interval_has_no_end_and_says_so(corpus: str) -> None:
    _alias(
        corpus,
        v="ACME",
        f=dt.date(1990, 1, 1),
        t=None,
        kt=dt.datetime(2026, 8, 14),
        ks="sec_company_tickers",
        cite="SEC company_tickers.json snapshot 2026-08-14",
    )
    row = _read(corpus)["tickers"][0]  # type: ignore[index]
    assert row["last_day"] is None
    assert row["is_current"] is True


def test_the_company_tickers_read_rule_travels_with_the_payload(corpus: str) -> None:
    """A 1990 start from `sec_company_tickers` is a floor, not a finding.

    That source evidences the CURRENT holding only; the interval's start is
    deliberately left open. Without the note, the oldest date on the page
    reads as the day the ticker was first held.
    """
    body = _read(corpus)
    assert "not evidenced" in body["ticker_note"]  # type: ignore[index]


def test_no_disputed_split_is_distinguished_from_no_split_at_all(
    corpus: str,
) -> None:
    """Two different facts behind the same empty list.

    "Nothing was disputed" is reassurance; "there was nothing to dispute" is
    not. An unqualified blank lets a reader take the second for the first.
    """
    no_splits = _read(corpus)
    assert no_splits["split_verdicts"] == []
    assert "no split at all" in no_splits["split_verdict_note"]  # type: ignore[index]

    with create_engine(corpus, future=True).begin() as c:
        c.execute(
            text(
                "insert into security_corporate_action_facts "
                "(security_id, action_type, ex_date, ratio, event_time, "
                " knowledge_time, knowledge_source, currency, source) "
                "values (1, 'split', '2020-08-31', 4.0, '2020-08-31', "
                "        '2020-08-31', 'vendor', 'USD', 'vendor')"
            )
        )
    with_split = _read(corpus)
    assert with_split["split_verdicts"] == []
    assert "no split was disputed" in with_split["split_verdict_note"]  # type: ignore[index]


def test_without_a_corpus_the_route_says_which_database_is_missing(
    tmp_path: object,
) -> None:
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    response = TestClient(create_app(record)).get("/securities/1/provenance")
    assert response.status_code == 501
    assert "different database" in response.json()["detail"]


def test_a_database_without_the_corpus_views_names_that_mistake(
    tmp_path: object,
) -> None:
    """Passing the record twice is the likely operator error, and the view is
    what distinguishes a prepared corpus from a database with the tables."""
    record = f"sqlite:///{tmp_path / 'record.sqlite'}"  # type: ignore[operator]
    Base.metadata.create_all(create_engine(record, future=True))
    body = TestClient(create_app(record, record)).get("/securities/1/provenance").json()
    assert body["available"] is False
    assert "not a prepared research-01 corpus" in body["note"]
