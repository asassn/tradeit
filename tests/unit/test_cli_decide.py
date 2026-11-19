"""``tradeit plan``: resolving a universe and declaring a portfolio.

The command's own wiring was exercised against the real 73 GB corpus, which
these tests deliberately do not touch. On 2015-11-27, over twenty tickers from
2014-01-02:

* with no holdings it proposed ``TXN 108 @ 57.55 stop 52.95``, risking 0.50% of
  equity and sized by ``risk_per_trade``;
* with ``IBM`` and ``TXN`` declared held it refused ``TXN`` for
  ``sector_limit_reached, heat_limit_exceeded`` and reported that it would have
  ranked first.

That second line is the one the project existed to produce and had never
produced. What is tested here is everything the command decides *before*
handing off to :func:`tradeit.decide.plan_session`, because those are the
places it could silently produce a plan about a portfolio nobody described.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.cli_decide import _holding, _universe
from tradeit.core.enums import PositionStatus, Side
from tradeit.storage.tables import Issuer, Security, SymbolAlias

UTC = dt.UTC
DAY = dt.date(2015, 1, 2)


def _security(session: Session, name: str) -> int:
    issuer = Issuer(display_name=name, source="test")
    session.add(issuer)
    session.flush()
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    return int(security.security_id)


def _alias(
    session: Session,
    security_id: int,
    ticker: str,
    *,
    valid_from: dt.date = DAY,
    valid_to: dt.date | None = None,
) -> None:
    session.add(
        SymbolAlias(
            security_id=security_id,
            alias_kind="ticker",
            alias_value=ticker,
            valid_from=valid_from,
            valid_to=valid_to,
            knowledge_source="test",
            knowledge_time=dt.datetime.combine(valid_from, dt.time(21), tzinfo=UTC),
            source="test",
        )
    )
    session.flush()


# -- resolving the universe -------------------------------------------------


def test_a_resolved_ticker_maps_to_its_security(db_session: Session) -> None:
    sid = _security(db_session, "Acme")
    _alias(db_session, sid, "ACME")

    assert _universe(db_session, ["ACME"]) == {"ACME": sid}


def test_an_unresolved_ticker_is_absent_so_the_caller_can_report_it(
    db_session: Session,
) -> None:
    """**Absent, never guessed.**

    A universe quietly smaller than the one asked for produces a plan about a
    portfolio nobody described. The command prints the difference; this pins
    that the resolver gives it something to print rather than inventing an id.
    Measured against the real corpus, five of twenty tickers did not resolve.
    """
    sid = _security(db_session, "Acme")
    _alias(db_session, sid, "ACME")

    resolved = _universe(db_session, ["ACME", "NOSUCH"])

    assert resolved == {"ACME": sid}
    assert "NOSUCH" not in resolved


def test_a_ticker_held_by_two_issuers_resolves_to_the_earlier_interval(
    db_session: Session,
) -> None:
    """The splice this corpus exists to prevent, arriving as a universe entry.

    One ticker, two unrelated issuers across time. The resolver takes the
    earliest interval deterministically rather than whichever row the database
    happened to return — an arbitrary choice here would silently plan trades
    for a different company than the one being priced.
    """
    first = _security(db_session, "First Holder")
    second = _security(db_session, "Second Holder")
    _alias(
        db_session, first, "SPLICE", valid_from=dt.date(1998, 1, 2), valid_to=dt.date(2005, 1, 1)
    )
    _alias(db_session, second, "SPLICE", valid_from=dt.date(2005, 1, 1))

    resolved = _universe(db_session, ["SPLICE"])

    assert resolved == {"SPLICE": first}
    assert _universe(db_session, ["SPLICE"]) == resolved, "and it is stable across calls"


def test_an_empty_ticker_list_resolves_to_nothing_without_raising(
    db_session: Session,
) -> None:
    assert _universe(db_session, []) == {}


# -- declaring a holding ----------------------------------------------------


def test_a_declared_holding_carries_its_stop() -> None:
    position = _holding("IBM:400:138.46:110.00", {"IBM": 7})

    assert position.instrument_id == 7
    assert position.quantity == Decimal(400)
    assert position.average_entry_price == Decimal("138.46")
    assert position.stop_price == Decimal("110.00")
    assert position.initial_stop_price == Decimal("110.00")
    assert position.side is Side.LONG
    assert position.status is PositionStatus.OPEN
    # 400 shares losing $28.46 each if the stop is hit.
    assert position.open_risk(Decimal("138.46")) == Decimal(400) * Decimal("28.46")


@pytest.mark.parametrize(
    "spec",
    [
        "IBM:400:138.46",  # no stop
        "IBM:400",  # no price either
        "IBM",  # a name alone
        "IBM:400:138.46:110.00:extra",  # one part too many
        "",
    ],
)
def test_a_holding_without_all_four_parts_is_refused(spec: str) -> None:
    """**A holding with no stop has undefined risk.**

    It cannot be sized, cannot be summed into portfolio heat, and is therefore
    not a position this system will hold — ``PositionState`` says exactly that.
    Defaulting the stop would be inventing the position's size, since
    risk-based sizing derives shares from the entry-to-stop distance.
    """
    with pytest.raises(SystemExit) as raised:
        _holding(spec, {"IBM": 7})

    assert "TICKER:QUANTITY:ENTRY:STOP" in str(raised.value)


def test_a_holding_outside_the_universe_is_refused() -> None:
    """It would have no prices to mark against, so its stop could not be managed."""
    with pytest.raises(SystemExit) as raised:
        _holding("GOOG:100:50:45", {"IBM": 7})

    assert "GOOG" in str(raised.value)
    assert "--tickers" in str(raised.value)
