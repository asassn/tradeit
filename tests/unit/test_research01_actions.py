"""Corporate actions: the dependency adjusted prices rest on.

Two properties carry the weight here. An action must attach to whichever
security held the ticker **on its ex-date**, so a reused ticker never lends one
company's split to another's price history. And a row missing the value its own
action type requires is **rejected, not defaulted** -- a split silently ratioed
1.0 is a split that does nothing and passes every check downstream.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.research01 import (
    Delivery,
    KnowledgeTimeBasis,
    RejectReason,
    VendorAction,
    action_knowledge_time_for,
    import_corporate_actions,
)
from tradeit.storage.tables import (
    Issuer,
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SymbolAlias,
)

UTC = dt.UTC
SESSION = dt.date(2011, 4, 4)
DELIVERED = dt.datetime(2026, 8, 31, 12, tzinfo=UTC)
KT = dt.datetime(2026, 1, 1, tzinfo=UTC)


def _security(session: Session, name: str, cik: str) -> Security:
    issuer = Issuer(display_name=name, source="test")
    session.add(issuer)
    session.flush()
    session.add(
        IssuerIdentifier(
            issuer_id=issuer.issuer_id,
            namespace="sec_cik",
            value=cik,
            value_normalized=str(int(cik)),
            role="primary",
            citation="test",
            source="test",
        )
    )
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    return security


def _alias(
    session: Session, security: Security, ticker: str, frm: dt.date, to: dt.date | None
) -> None:
    session.add(
        SymbolAlias(
            security_id=security.security_id,
            alias_kind="ticker",
            alias_value=ticker,
            valid_from=frm,
            valid_to=to,
            knowledge_time=KT,
            knowledge_source="test",
            source="test",
        )
    )
    session.flush()


class TestActionKnowledgeTime:
    def test_an_action_is_knowable_at_its_ex_date_close(self) -> None:
        kt, basis = action_knowledge_time_for(ex_date=SESSION, delivered_at=DELIVERED)
        assert basis is KnowledgeTimeBasis.SESSION_CLOSE
        assert kt.date() == SESSION
        assert kt < DELIVERED

    def test_a_non_session_ex_date_falls_back_to_delivery(self) -> None:
        kt, basis = action_knowledge_time_for(ex_date=dt.date(2010, 12, 26), delivered_at=DELIVERED)
        assert basis is KnowledgeTimeBasis.DELIVERY_UNESTABLISHED
        assert kt == DELIVERED

    def test_knowledge_time_is_never_earlier_than_the_event(self) -> None:
        """The conservative direction, deliberately.

        A split is announced before its ex-date, so a true announcement instant
        would sit *earlier* than the event it describes. Using the ex-date may
        credit us with knowing later than we could have, and never earlier.
        Under-informed is safe; look-ahead is not.
        """
        kt, _ = action_knowledge_time_for(ex_date=SESSION, delivered_at=DELIVERED)
        assert kt >= dt.datetime.combine(SESSION, dt.time.min, tzinfo=UTC)


class TestRoundTrip:
    def test_a_split_round_trips(self, db_session: Session) -> None:
        security = _security(db_session, "ACME", "1")
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)

        result = import_corporate_actions(
            db_session,
            [VendorAction("ACME", "split", SESSION, ratio=Decimal("2"))],
            Delivery("eodhd", DELIVERED, "acme_actions.csv"),
        )
        assert result.landed == 1

        stored = db_session.scalars(select(SecurityCorporateActionFact)).one()
        assert stored.security_id == security.security_id
        assert stored.action_type == "split"
        assert stored.ex_date == SESSION
        assert stored.ratio == Decimal("2")
        assert stored.cash_amount is None
        assert stored.knowledge_time >= stored.event_time

    def test_a_dividend_round_trips(self, db_session: Session) -> None:
        security = _security(db_session, "ACME", "1")
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)
        import_corporate_actions(
            db_session,
            [VendorAction("ACME", "cash_dividend", SESSION, cash_amount=Decimal("0.25"))],
            Delivery("eodhd", DELIVERED),
        )
        stored = db_session.scalars(select(SecurityCorporateActionFact)).one()
        assert stored.cash_amount == Decimal("0.25")
        assert stored.ratio is None

    def test_re_importing_changes_nothing(self, db_session: Session) -> None:
        security = _security(db_session, "ACME", "1")
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)
        actions = [VendorAction("ACME", "split", SESSION, ratio=Decimal("2"))]
        delivery = Delivery("eodhd", DELIVERED)

        first = import_corporate_actions(db_session, actions, delivery)
        second = import_corporate_actions(db_session, actions, delivery)
        assert (first.landed, second.landed) == (1, 0)
        assert second.rejected[0].reason is RejectReason.DUPLICATE
        assert len(db_session.scalars(select(SecurityCorporateActionFact)).all()) == 1


class TestIncompleteRowsAreRejectedNotDefaulted:
    @pytest.mark.parametrize(
        ("action_type", "missing"),
        [("split", "ratio"), ("reverse_split", "ratio"), ("cash_dividend", "cash_amount")],
    )
    def test_a_row_missing_its_required_value_is_refused(
        self, db_session: Session, action_type: str, missing: str
    ) -> None:
        """A split defaulted to ratio 1.0 does nothing and looks entirely fine."""
        security = _security(db_session, "ACME", "1")
        _alias(db_session, security, "ACME", dt.date(2000, 1, 1), None)

        result = import_corporate_actions(
            db_session,
            [VendorAction("ACME", action_type, SESSION)],
            Delivery("eodhd", DELIVERED),
        )
        assert result.landed == 0
        assert result.rejected[0].reason is RejectReason.INCOMPLETE
        assert missing in result.rejected[0].detail
        assert db_session.scalars(select(SecurityCorporateActionFact)).all() == []


class TestNoActionCrossesAnIdentityBreak:
    """The splice property, in the form that would corrupt an adjusted series.

    A split attached to the wrong side of a reused ticker does not merely
    mis-attribute an event: it would rescale another company's entire price
    history when the adjusted series is derived.
    """

    BREAK_OUT = dt.date(2009, 6, 1)
    BREAK_IN = dt.date(2009, 9, 1)

    def test_actions_land_under_the_security_that_held_the_ticker_that_day(
        self, db_session: Session
    ) -> None:
        old = _security(db_session, "OLD CO", "40730")
        new = _security(db_session, "NEW CO", "1467858")
        _alias(db_session, old, "TWO", dt.date(2000, 1, 3), self.BREAK_OUT)
        _alias(db_session, new, "TWO", self.BREAK_IN, None)

        result = import_corporate_actions(
            db_session,
            [
                VendorAction("TWO", "split", dt.date(2009, 5, 1), ratio=Decimal("2")),
                VendorAction("TWO", "split", dt.date(2009, 10, 1), ratio=Decimal("3")),
                VendorAction("TWO", "split", dt.date(2009, 7, 1), ratio=Decimal("5")),
            ],
            Delivery("eodhd", DELIVERED),
        )
        assert result.landed == 2

        rows = db_session.scalars(select(SecurityCorporateActionFact)).all()
        by_security = {r.security_id: r for r in rows}
        assert set(by_security) == {old.security_id, new.security_id}
        assert by_security[old.security_id].ratio == Decimal("2")
        assert by_security[new.security_id].ratio == Decimal("3")

        # The gap action belongs to neither and is not nearest-neighboured.
        assert [r.reason for r in result.unresolved] == [RejectReason.NO_ALIAS]
        assert result.unresolved[0].payload.ex_date == dt.date(2009, 7, 1)

    def test_neither_security_receives_the_other_s_split(self, db_session: Session) -> None:
        old = _security(db_session, "OLD CO", "40730")
        new = _security(db_session, "NEW CO", "1467858")
        _alias(db_session, old, "TWO", dt.date(2000, 1, 3), self.BREAK_OUT)
        _alias(db_session, new, "TWO", self.BREAK_IN, None)
        import_corporate_actions(
            db_session,
            [
                VendorAction("TWO", "split", dt.date(2009, 5, 1), ratio=Decimal("2")),
                VendorAction("TWO", "split", dt.date(2009, 10, 1), ratio=Decimal("3")),
            ],
            Delivery("eodhd", DELIVERED),
        )
        for security_id, expected in (
            (old.security_id, Decimal("2")),
            (new.security_id, Decimal("3")),
        ):
            ratios = db_session.scalars(
                select(SecurityCorporateActionFact.ratio).where(
                    SecurityCorporateActionFact.security_id == security_id
                )
            ).all()
            assert ratios == [expected]
