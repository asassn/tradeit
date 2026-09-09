"""Tests for the corpus-backed session data adapter.

Built on a synthetic database rather than the real 67 GB file, so they run
everywhere — but every rule they assert was chosen because of what the real
corpus does: two bases per session, revisions learned years later, and tickers
that changed hands.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.enums import KnowledgeTimeSource
from tradeit.research01.pit import KnowledgeTimeBasis
from tradeit.storage.tables import (
    Issuer,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

UTC = dt.UTC
DAY = dt.date(2020, 6, 1)


def _close(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(21), tzinfo=UTC)


def _security(session: Session) -> int:
    issuer = Issuer(display_name="ACME", source="test")
    session.add(issuer)
    session.flush()
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    return security.security_id


def _bar(
    session: Session,
    security_id: int,
    day: dt.date,
    *,
    basis: str = "raw",
    close: str = "100",
    known: dt.datetime | None = None,
    event: dt.datetime | None = None,
) -> None:
    session.add(
        SecurityPriceFact(
            security_id=security_id,
            session_date=day,
            adjustment_basis=basis,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal(1000000),
            event_time=event or _close(day),
            knowledge_time=known or _close(day),
            knowledge_time_basis=KnowledgeTimeBasis.SESSION_CLOSE,
            knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
            source="test",
        )
    )


def _ticker(
    session: Session,
    security_id: int,
    *,
    valid_from: dt.date,
    valid_to: dt.date | None = None,
) -> None:
    session.add(
        SymbolAlias(
            security_id=security_id,
            alias_kind="ticker",
            alias_value="ACME",
            valid_from=valid_from,
            valid_to=valid_to,
            knowledge_source="test",
            knowledge_time=_close(valid_from),
            source="test",
        )
    )


def _data(session: Session, security_id: int, **overrides: object) -> CorpusSessionData:
    payload: dict[str, object] = {
        "session": session,
        "universe": (security_id,),
        "start": dt.date(2020, 1, 1),
        "end": dt.date(2020, 12, 31),
        "candidate_source": lambda d, b: [],
    }
    payload.update(overrides)
    return CorpusSessionData(**payload)  # type: ignore[arg-type]


class TestConstruction:
    def test_an_empty_universe_is_refused(self, db_session: Session) -> None:
        """Loading everything would scan 71 million rows."""
        with pytest.raises(ValueError, match="declared universe"):
            _data(db_session, 1, universe=())

    def test_a_backwards_range_is_refused(self, db_session: Session) -> None:
        sid = _security(db_session)
        with pytest.raises(ValueError, match="end must be after start"):
            _data(db_session, sid, start=dt.date(2020, 6, 1), end=dt.date(2020, 1, 1))


class TestRawPricesOnly:
    def test_the_adjusted_basis_is_never_served(self, db_session: Session) -> None:
        """An adjusted series carries future splits in every earlier bar."""
        sid = _security(db_session)
        _bar(db_session, sid, DAY, basis="raw", close="100")
        _bar(db_session, sid, DAY, basis="total", close="50")
        db_session.flush()
        bars = _data(db_session, sid).bars(DAY)
        assert bars[sid].close == Decimal(100)


class TestPointInTime:
    def test_a_revision_learned_later_is_not_visible(self, db_session: Session) -> None:
        """A 2024 correction to a 2020 bar did not exist in 2020."""
        sid = _security(db_session)
        _bar(db_session, sid, DAY, close="100")
        _bar(db_session, sid, DAY, close="999", known=_close(dt.date(2024, 1, 1)))
        db_session.flush()
        assert _data(db_session, sid).bars(DAY)[sid].close == Decimal(100)

    def test_a_same_day_revision_is_visible(self, db_session: Session) -> None:
        """Knowable by the close, so the later knowledge_time wins.

        Both rows carry an intraday ``event_time``, because the schema enforces
        knowledge_time >= event_time -- a fact cannot be known before it
        happens, and a revision "learned" earlier the same day is not a thing.
        """
        sid = _security(db_session)
        event = dt.datetime.combine(DAY, dt.time(14), tzinfo=UTC)
        _bar(
            db_session,
            sid,
            DAY,
            close="100",
            event=event,
            known=dt.datetime.combine(DAY, dt.time(15), tzinfo=UTC),
        )
        _bar(
            db_session,
            sid,
            DAY,
            close="101",
            event=event,
            known=dt.datetime.combine(DAY, dt.time(20), tzinfo=UTC),
        )
        db_session.flush()
        assert _data(db_session, sid).bars(DAY)[sid].close == Decimal(101)


class TestTheSpliceGuard:
    def test_bars_before_the_ticker_was_owned_are_excluded(self, db_session: Session) -> None:
        sid = _security(db_session)
        _ticker(db_session, sid, valid_from=dt.date(2020, 6, 1))
        _bar(db_session, sid, dt.date(2020, 3, 2), close="50")
        _bar(db_session, sid, DAY, close="100")
        db_session.flush()
        data = _data(db_session, sid)
        assert data.sessions(dt.date(2020, 1, 1), dt.date(2020, 12, 31)) == [DAY]
        assert data.excluded_out_of_window == 1

    def test_the_exclusive_valid_to_excludes_its_own_day(self, db_session: Session) -> None:
        """valid_to is exclusive; the last owned day is the one before it."""
        sid = _security(db_session)
        _ticker(db_session, sid, valid_from=dt.date(2020, 1, 1), valid_to=DAY)
        _bar(db_session, sid, dt.date(2020, 5, 29), close="100")
        _bar(db_session, sid, DAY, close="100")
        db_session.flush()
        data = _data(db_session, sid)
        assert data.sessions(dt.date(2020, 1, 1), dt.date(2020, 12, 31)) == [dt.date(2020, 5, 29)]


class TestSplits:
    def test_a_split_on_its_ex_date_is_reported(self, db_session: Session) -> None:
        sid = _security(db_session)
        _bar(db_session, sid, DAY, close="50")
        db_session.add(
            SecurityCorporateActionFact(
                security_id=sid,
                action_type="split",
                ex_date=DAY,
                ratio=Decimal(2),
                event_time=_close(DAY),
                knowledge_time=_close(DAY),
                knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
                source="test",
            )
        )
        db_session.flush()
        data = _data(db_session, sid)
        assert data.splits_on(DAY) == {sid: Decimal(2)}
        assert data.split_count == 1

    def test_a_session_with_no_split_reports_nothing(self, db_session: Session) -> None:
        sid = _security(db_session)
        _bar(db_session, sid, DAY, close="100")
        db_session.flush()
        assert _data(db_session, sid).splits_on(DAY) == {}


def test_load_statistics_are_reported(db_session: Session) -> None:
    sid = _security(db_session)
    for offset in range(5):
        _bar(db_session, sid, DAY + dt.timedelta(days=offset), close="100")
    db_session.flush()
    data = _data(db_session, sid)
    assert data.bar_count == 5
    assert data.session_count == 5


class TestNonPositiveBars:
    """A bar priced at zero is a vendor placeholder, not a price."""

    def test_a_zero_priced_bar_is_excluded_and_counted(self, db_session: Session) -> None:
        sid = _security(db_session)
        _bar(db_session, sid, DAY, close="100")
        db_session.execute(
            SecurityPriceFact.__table__.insert(),
            {
                "security_id": sid,
                "session_date": DAY + dt.timedelta(days=1),
                "adjustment_basis": "raw",
                "open": Decimal(0),
                "high": Decimal(0),
                "low": Decimal(0),
                "close": Decimal(0),
                "volume": Decimal(110),
                "event_time": _close(DAY + dt.timedelta(days=1)),
                "knowledge_time": _close(DAY + dt.timedelta(days=1)),
                "knowledge_time_basis": KnowledgeTimeBasis.SESSION_CLOSE,
                "knowledge_source": KnowledgeTimeSource.VENDOR_INGEST,
                "source": "test",
            },
        )
        db_session.flush()
        data = _data(db_session, sid)
        assert data.excluded_non_positive == 1
        assert data.bar_count == 1
        assert data.sessions(dt.date(2020, 1, 1), dt.date(2020, 12, 31)) == [DAY]

    def test_the_two_exclusion_counts_are_reported_separately(self, db_session: Session) -> None:
        """A single total would hide which problem the corpus actually has."""
        sid = _security(db_session)
        _ticker(db_session, sid, valid_from=DAY)
        _bar(db_session, sid, dt.date(2020, 3, 2), close="50")  # before the ticker
        _bar(db_session, sid, DAY, close="100")
        db_session.flush()
        data = _data(db_session, sid)
        assert data.excluded_out_of_window == 1
        assert data.excluded_non_positive == 0
        assert data.excluded == 1
