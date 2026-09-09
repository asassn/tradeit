"""The views must agree with the library, or they are a seventh trap.

`v_prices` exists so a tool that never reads the data dictionary still gets a
correct answer. That only holds if it applies the same exclusions
`price_series` applies — the exchange calendar, the adjudicated interval, and
the latest revision. These tests build the views over a synthetic corpus whose
every row is one of the traps, and require the view to drop exactly what the
library drops.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tradeit.core.enums import KnowledgeTimeSource
from tradeit.research01.pit import KnowledgeTimeBasis
from tradeit.research01.series import price_series
from tradeit.research01.views import VIEWS, create_views
from tradeit.storage.tables import Issuer, Security, SecurityPriceFact, SymbolAlias

UTC = dt.UTC
KNOWN = dt.datetime(2024, 1, 1, tzinfo=UTC)
LATER = dt.datetime(2025, 1, 1, tzinfo=UTC)


def _security(session: Session) -> Security:
    issuer = Issuer(display_name="ACME", source="test")
    session.add(issuer)
    session.flush()
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    return security


def _bar(
    session: Session, sid: int, day: dt.date, close: str, *, known: dt.datetime = KNOWN
) -> None:
    for basis in ("raw", "total"):
        session.add(
            SecurityPriceFact(
                security_id=sid,
                session_date=day,
                adjustment_basis=basis,
                event_time=known,
                knowledge_time=known,
                knowledge_time_basis=str(KnowledgeTimeBasis.SESSION_CLOSE),
                knowledge_source="test",
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal(1000),
                volume_adjusted=False,
                source="test",
            )
        )


@pytest.fixture
def built(db_session: Session) -> Session:
    create_views(db_session, start=dt.date(2020, 1, 1), end=dt.date(2021, 12, 31))
    return db_session


def _days(session: Session, view: str, sid: int) -> list[dt.date]:
    rows = session.execute(
        text(f"select session_date from {view} where security_id = :s order by 1"), {"s": sid}
    ).all()
    return [dt.date.fromisoformat(str(d)) if isinstance(d, str) else d for (d,) in rows]


def test_every_view_is_created(built: Session) -> None:
    names = {
        n for (n,) in built.execute(text("select name from sqlite_master where type='view'")).all()
    }
    assert set(VIEWS) <= names


def test_a_market_holiday_is_excluded(built: Session) -> None:
    """Trap 0.3: ~3,900 holiday bars are in the tables by design."""
    security = _security(built)
    _bar(built, security.security_id, dt.date(2021, 7, 5), "10")  # observed July 4th
    _bar(built, security.security_id, dt.date(2021, 7, 6), "11")
    built.flush()
    assert _days(built, "v_prices", security.security_id) == [dt.date(2021, 7, 6)]


def test_a_bar_outside_the_adjudicated_interval_is_excluded(built: Session) -> None:
    """Trap 0.3 again: ~91,000 spliced bars are retained, not deleted."""
    security = _security(built)
    built.add(
        SymbolAlias(
            security_id=security.security_id,
            alias_kind="ticker",
            alias_value="ACME",
            valid_from=dt.date(2021, 7, 6),
            valid_to=dt.date(2021, 7, 8),
            knowledge_time=KNOWN,
            knowledge_source="test",
            source="test",
        )
    )
    for day in (dt.date(2021, 7, 2), dt.date(2021, 7, 6), dt.date(2021, 7, 7), dt.date(2021, 7, 9)):
        _bar(built, security.security_id, day, "10")
    built.flush()
    # valid_to is exclusive, so 7-08 onward is out and 7-02 predates the interval.
    assert _days(built, "v_prices", security.security_id) == [
        dt.date(2021, 7, 6),
        dt.date(2021, 7, 7),
    ]


def test_only_the_latest_revision_is_returned(built: Session) -> None:
    """327,924 keys carry more than one knowledge_time."""
    security = _security(built)
    day = dt.date(2021, 7, 6)
    _bar(built, security.security_id, day, "10", known=KNOWN)
    _bar(built, security.security_id, day, "99", known=LATER)
    built.flush()
    rows = built.execute(
        text("select close from v_prices where security_id = :s"), {"s": security.security_id}
    ).all()
    assert len(rows) == 1
    assert Decimal(str(rows[0][0])) == Decimal(99)


def test_the_two_price_views_carry_different_bases(built: Session) -> None:
    """Trap 0.1: every session is stored twice. Each view picks one, so
    neither double-counts and a caller never has to know the column exists."""
    security = _security(built)
    _bar(built, security.security_id, dt.date(2021, 7, 6), "10")
    built.flush()
    for view in ("v_prices", "v_prices_raw"):
        assert len(_days(built, view, security.security_id)) == 1


def test_v_security_tickers_makes_the_last_day_inclusive(built: Session) -> None:
    """Trap 0.2: the stored valid_to is exclusive and off by one day."""
    security = _security(built)
    built.add(
        SymbolAlias(
            security_id=security.security_id,
            alias_kind="ticker",
            alias_value="ACME",
            valid_from=dt.date(2021, 1, 1),
            valid_to=dt.date(2021, 7, 8),
            knowledge_time=KNOWN,
            knowledge_source="test",
            source="test",
        )
    )
    built.flush()
    row = built.execute(text("select ticker, last_day, is_current from v_security_tickers")).one()
    assert row[0] == "ACME"
    assert str(row[1]).startswith("2021-07-07"), (
        "valid_to is exclusive; last owned is the day before"
    )
    assert not row[2]


def test_an_open_ended_ticker_is_marked_current(built: Session) -> None:
    security = _security(built)
    built.add(
        SymbolAlias(
            security_id=security.security_id,
            alias_kind="ticker",
            alias_value="ACME",
            valid_from=dt.date(2021, 1, 1),
            valid_to=None,
            knowledge_time=KNOWN,
            knowledge_source="test",
            source="test",
        )
    )
    built.flush()
    row = built.execute(text("select last_day, is_current from v_security_tickers")).one()
    assert row[0] is None
    assert row[1]


def test_rebuilding_is_idempotent(built: Session) -> None:
    """It must be safe to run after every load, or it will not be run at all."""
    before = built.execute(text("select count(*) from trading_sessions")).scalar()
    create_views(built, start=dt.date(2020, 1, 1), end=dt.date(2021, 12, 31))
    after = built.execute(text("select count(*) from trading_sessions")).scalar()
    assert before == after


class TestZeroPricedBars:
    """A bar priced at zero is a vendor placeholder, not a price.

    The corpus holds 11,580 of them across 248 securities, and the two
    supported read paths disagreed about them until this was fixed --
    ``CorpusSessionData`` excluded them and the views served them.
    """

    def _zero_bar(self, session: Session, sid: int, day: dt.date) -> None:
        for basis in ("raw", "total"):
            session.add(
                SecurityPriceFact(
                    security_id=sid,
                    session_date=day,
                    adjustment_basis=basis,
                    open=Decimal(0),
                    high=Decimal(0),
                    low=Decimal(0),
                    close=Decimal(0),
                    volume=Decimal(110),
                    event_time=KNOWN,
                    knowledge_time=KNOWN,
                    knowledge_time_basis=KnowledgeTimeBasis.SESSION_CLOSE,
                    knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                    source="test",
                )
            )

    def test_neither_price_view_serves_a_zero_priced_bar(self, built: Session) -> None:
        security = _security(built)
        _bar(built, security.security_id, dt.date(2021, 7, 6), "10")
        self._zero_bar(built, security.security_id, dt.date(2021, 7, 7))
        built.flush()
        for view in ("v_prices", "v_prices_raw"):
            rows = built.execute(
                text(f"select session_date from {view} where security_id = :s"),
                {"s": security.security_id},
            ).all()
            assert [r[0] for r in rows] == ["2021-07-06"], view

    def test_price_series_excludes_it_too(self, built: Session) -> None:
        """The two read paths must agree, which is the point of the fix."""
        security = _security(built)
        _bar(built, security.security_id, dt.date(2021, 7, 6), "10")
        self._zero_bar(built, security.security_id, dt.date(2021, 7, 7))
        built.flush()
        bars = price_series(built, security.security_id, as_of=dt.datetime(2026, 1, 1, tzinfo=UTC))
        assert [b.session_date for b in bars] == [dt.date(2021, 7, 6)]

    def test_an_auditor_can_still_see_it(self, built: Session) -> None:
        """Bounded on read, not destroyed -- like every other exclusion here."""
        security = _security(built)
        self._zero_bar(built, security.security_id, dt.date(2021, 7, 7))
        built.flush()
        bars = price_series(
            built,
            security.security_id,
            as_of=dt.datetime(2026, 1, 1, tzinfo=UTC),
            include_disputed=True,
        )
        assert [b.session_date for b in bars] == [dt.date(2021, 7, 7)]
