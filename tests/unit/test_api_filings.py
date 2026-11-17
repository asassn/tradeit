"""The filing record, and whether it is complete.

**Completeness is what this route exists to answer**, not the list. A filing
record that ends years before the security stopped trading reads as a company
that stopped filing; it is a record that stops, and only the second is
established.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.api.filings_route import filings_payload
from tradeit.storage.tables import Base


@pytest.fixture
def corpus(tmp_path: object) -> sessionmaker[Session]:
    dsn = f"sqlite:///{tmp_path / 'corpus.sqlite'}"  # type: ignore[operator]
    engine = create_engine(dsn, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(
            text(
                "create table security_summary (security_id integer primary key, last_session text)"
            )
        )
    return sessionmaker(bind=engine, future=True)


def _issuer(factory: sessionmaker[Session], source: str) -> None:
    with factory() as s:
        s.execute(
            text(
                "insert into issuers (issuer_id, display_name, source) "
                "values (1, 'ACME CORP', :src)"
            ),
            {"src": source},
        )
        s.execute(
            text(
                "insert into securities (security_id, issuer_id, security_type, "
                "currency, source) values (1, 1, 'common', 'USD', :src)"
            ),
            {"src": source},
        )
        s.commit()


def _filing(factory: sessionmaker[Session], form: str, filed: str) -> None:
    with factory() as s:
        s.execute(
            text(
                "insert into filings (issuer_id, accession, form_type, filed_at, "
                "source) values (1, :a, :f, :d, 'edgar')"
            ),
            {"a": f"{form}-{filed}", "f": form, "d": filed},
        )
        s.commit()


def _traded_until(factory: sessionmaker[Session], day: str) -> None:
    with factory() as s:
        s.execute(
            text("insert into security_summary (security_id, last_session) values (1, :d)"),
            {"d": day},
        )
        s.commit()


def _read(factory: sessionmaker[Session]) -> dict[str, object]:
    with factory() as s:
        return filings_payload(s, 1)


def test_a_record_that_stops_before_trading_did_is_flagged(
    corpus: sessionmaker[Session],
) -> None:
    """**The reason this route exists.**

    IMC Global is the live example: the filing record stops 2004-11-19 while
    the security traded until 2018-01-30, 4,820 days later. As a plain list
    that reads as a company that stopped filing in 2004.
    """
    _issuer(corpus, "edgar_filing_text")
    _filing(corpus, "10-K", "2004-11-19")
    _traded_until(corpus, "2018-01-30")
    body = _read(corpus)
    assert body["stops_early"] is not None
    assert "4820 days later" in body["stops_early"]  # type: ignore[operator]
    assert "only the first is established" in body["stops_early"]  # type: ignore[operator]


def test_an_annual_filer_is_not_flagged_for_filing_annually(
    corpus: sessionmaker[Session],
) -> None:
    """The threshold is a year because an annual filer legitimately goes
    eleven months between filings; a tighter bound flags every one of them,
    and a warning that always fires stops being read."""
    _issuer(corpus, "edgar_full_index")
    _filing(corpus, "10-K", "2020-01-02")
    _traded_until(corpus, "2020-11-01")
    assert _read(corpus)["stops_early"] is None


def test_the_seeding_route_is_reported_because_it_decides_what_gaps_mean(
    corpus: sessionmaker[Session],
) -> None:
    """`filings` is seeded from more than one place and they are not equally
    complete. Measured 2026-11-17, records stopping more than a year early:
    93 of 858 for `edgar_filing_text` against 24 of 11,635 for
    `sec_fsds_sub`."""
    _issuer(corpus, "edgar_filing_text")
    _filing(corpus, "10-K", "2020-01-02")
    body = _read(corpus)
    assert body["issuer"]["source"] == "edgar_filing_text"  # type: ignore[index]
    assert "93 of 858" in body["source_note"]  # type: ignore[operator]


def test_an_unrecognised_seeding_route_says_so_rather_than_claiming_complete(
    corpus: sessionmaker[Session],
) -> None:
    """Silence about provenance reads as assurance about it."""
    _issuer(corpus, "something_new")
    _filing(corpus, "10-K", "2020-01-02")
    assert "not recorded" in _read(corpus)["source_note"]  # type: ignore[operator]


def test_form_types_are_counted_verbatim(corpus: sessionmaker[Session]) -> None:
    """`10-K` is not `10-K405`. Form strings are recorded exactly as filed and
    never normalised, and collapsing them would invent a filing history the
    issuer does not have."""
    _issuer(corpus, "edgar_full_index")
    _filing(corpus, "10-K", "2020-01-02")
    _filing(corpus, "10-K405", "1999-01-02")
    forms = {f["form_type"] for f in _read(corpus)["forms"]}  # type: ignore[union-attr]
    assert forms == {"10-K", "10-K405"}


def test_an_issuer_with_no_filings_says_why(corpus: sessionmaker[Session]) -> None:
    """An issuer can be known without its index having been ingested, which
    is a different fact from an issuer that never filed."""
    _issuer(corpus, "sec_fsds_sub")
    body = _read(corpus)
    assert body["available"] is False
    assert "seeded from more than one place" in body["note"]  # type: ignore[operator]


def test_a_security_with_no_issuer_says_so(corpus: sessionmaker[Session]) -> None:
    body = _read(corpus)
    assert body["available"] is False
    assert "no issuer" in body["note"]  # type: ignore[operator]
