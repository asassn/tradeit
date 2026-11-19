"""``tradeit plan``: resolving a universe and declaring a holding.

The command's own wiring was exercised against the real 73 GB corpus, which
these tests deliberately do not touch. On 2015-11-27, over twenty tickers from
2014-01-02:

* with no holdings it proposed ``TXN 108 @ 57.55 stop 52.95``, risking 0.50% of
  equity and sized by ``risk_per_trade``;
* with ``IBM`` and ``TXN`` declared held it refused ``TXN`` for
  ``sector_limit_reached, heat_limit_exceeded`` and reported that it would have
  ranked first.

That second line is the one the project existed to produce and had never
produced. What is tested here is everything decided *before* the corpus is
touched, because those are the places a plan could be produced about a
portfolio nobody described.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.cli_decide import _holding
from tradeit.decide import resolve_universe
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


AS_OF = dt.date(2015, 11, 27)


def test_a_resolved_ticker_maps_to_its_security(db_session: Session) -> None:
    sid = _security(db_session, "Acme")
    _alias(db_session, sid, "ACME")

    resolved, unresolved = resolve_universe(db_session, ("ACME",), AS_OF)

    assert resolved == {"ACME": sid}
    assert unresolved == {}


def test_an_unresolved_ticker_is_absent_so_the_caller_can_report_it(
    db_session: Session,
) -> None:
    """**Absent, never guessed.**

    A universe quietly smaller than the one declared produces a plan about a
    portfolio nobody described. Five of twenty tickers did not resolve in the
    first real run, and the command prints the difference; this pins that the
    resolver gives it something to print rather than inventing an id.
    """
    sid = _security(db_session, "Acme")
    _alias(db_session, sid, "ACME")

    resolved, unresolved = resolve_universe(db_session, ("ACME", "NOSUCH"), AS_OF)

    assert resolved == {"ACME": sid}
    assert "NOSUCH" in unresolved
    assert "no security held this ticker" in unresolved["NOSUCH"]


def test_a_ticker_resolves_to_whoever_held_it_on_the_date(db_session: Session) -> None:
    """**Resolution is per date, never per ticker** — DATA_DICTIONARY §0.2.

    One ticker, two unrelated issuers across time. An earlier version of this
    resolver took the earliest interval, which is deterministic but not
    correct: measured over 2003-2009 it resolved ``DIS`` to the Disney security
    whose prices begin in 2018, so the name printed on 0 of 1,763 sessions.
    """
    first = _security(db_session, "First Holder")
    second = _security(db_session, "Second Holder")
    _alias(
        db_session, first, "SPLICE", valid_from=dt.date(1998, 1, 2), valid_to=dt.date(2005, 1, 1)
    )
    _alias(db_session, second, "SPLICE", valid_from=dt.date(2005, 1, 1))

    early, _ = resolve_universe(db_session, ("SPLICE",), dt.date(2000, 6, 1))
    late, _ = resolve_universe(db_session, ("SPLICE",), dt.date(2015, 6, 1))

    assert early == {"SPLICE": first}
    assert late == {"SPLICE": second}, "the later holder, not the earliest interval"


def test_valid_to_is_exclusive_so_the_handover_day_belongs_to_the_new_holder(
    db_session: Session,
) -> None:
    """DATA_DICTIONARY §0.2: the last day a security owns a ticker is valid_to - 1."""
    first = _security(db_session, "First Holder")
    second = _security(db_session, "Second Holder")
    handover = dt.date(2005, 1, 1)
    _alias(db_session, first, "SPLICE", valid_from=dt.date(1998, 1, 2), valid_to=handover)
    _alias(db_session, second, "SPLICE", valid_from=handover)

    on_handover, _ = resolve_universe(db_session, ("SPLICE",), handover)
    day_before, _ = resolve_universe(db_session, ("SPLICE",), handover - dt.timedelta(days=1))

    assert on_handover == {"SPLICE": second}
    assert day_before == {"SPLICE": first}


def test_a_ticker_two_securities_claim_at_once_is_refused(db_session: Session) -> None:
    """**Ambiguity beats fabricated certainty.**

    Real case: ``DIS`` on 2015-11-27 is claimed by two securities whose
    intervals overlap — one open-ended from 1990, one running 1995 to 2019.
    Picking either would invent an identity, and the plan would price one
    company while naming the other's ticker.
    """
    first = _security(db_session, "Open Ended")
    second = _security(db_session, "Bounded")
    _alias(db_session, first, "DIS", valid_from=dt.date(1990, 1, 1))
    _alias(
        db_session, second, "DIS", valid_from=dt.date(1995, 11, 13), valid_to=dt.date(2019, 10, 7)
    )

    resolved, unresolved = resolve_universe(db_session, ("DIS",), AS_OF)

    assert resolved == {}, "neither may be chosen"
    assert "2 securities claim this ticker" in unresolved["DIS"]
    assert str(first) in unresolved["DIS"] and str(second) in unresolved["DIS"]


def test_an_empty_universe_resolves_to_nothing_without_querying(
    db_session: Session,
) -> None:
    """An empty ``in ()`` is a SQL error in some dialects, so it short-circuits."""
    assert resolve_universe(db_session, (), AS_OF) == ({}, {})


# -- declaring a holding on the command line -------------------------------


def test_a_declared_holding_carries_its_stop_and_its_age() -> None:
    holding = _holding("IBM:400:138.46:110.00:20")

    assert holding.ticker == "IBM"
    assert holding.quantity == Decimal(400)
    assert holding.entry_price == Decimal("138.46")
    assert holding.stop_price == Decimal("110.00")
    assert holding.sessions_held == 20


@pytest.mark.parametrize(
    "spec",
    [
        "IBM:400:138.46:110.00",  # no session count
        "IBM:400:138.46",  # no stop either
        "IBM:400",
        "IBM",
        "IBM:400:138.46:110.00:20:extra",
        "",
    ],
)
def test_a_holding_without_all_five_parts_is_refused(spec: str) -> None:
    """**A holding with no stop has undefined risk.**

    It cannot be sized, cannot be summed into portfolio heat, and is therefore
    not a position this system will hold — ``PositionState`` says exactly that.
    Defaulting the stop would invent the position's size, since risk-based
    sizing derives shares from the entry-to-stop distance; defaulting the
    session count would make the holding look freshly opened and suppress the
    time stop that should close it.
    """
    with pytest.raises(SystemExit) as raised:
        _holding(spec)

    assert "TICKER:QUANTITY:ENTRY:STOP:SESSIONS_HELD" in str(raised.value)


def test_a_holding_with_a_non_numeric_part_is_refused() -> None:
    with pytest.raises(SystemExit) as raised:
        _holding("IBM:lots:138.46:110.00:20")

    assert "not a number" in str(raised.value)
