"""Planning a declared portfolio against a corpus.

Built on a synthetic database rather than the real 73 GB file, so these run
everywhere — but each rule they assert was chosen because of what the real
corpus or the real engine does.

The property that matters most here is the one this module got wrong first:
**a holding that did not print today is carried, not refused.**
``EventDrivenEngine._context`` has carried one at its last known close from the
beginning, and says why — that price "is not a guess, it is the last thing
anybody knew, and being unchanged from yesterday it cannot trigger a stop that
yesterday did not". ``plan_declared`` instead raised and refused the entire
horizon, which stalls every *other* position's stop over one quiet name.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tradeit.core.enums import KnowledgeTimeSource
from tradeit.decide import DeclarationError, DeclaredHolding, DeclaredPortfolio, plan_declared
from tradeit.portfolio.mandate import Mandate
from tradeit.research01.pit import KnowledgeTimeBasis
from tradeit.storage.tables import Issuer, Sector, Security, SecurityPriceFact, SymbolAlias

UTC = dt.UTC
WARM = dt.date(2024, 1, 1)
AS_OF = dt.date(2024, 3, 1)


def _close(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(21), tzinfo=UTC)


def _listed(session: Session, ticker: str, sector: str = "Manufacturing") -> int:
    issuer = Issuer(display_name=f"{ticker} Inc", source="test")
    session.add(issuer)
    session.flush()
    security = Security(
        issuer_id=issuer.issuer_id, security_type="common_stock", currency="USD", source="test"
    )
    session.add(security)
    session.flush()
    sid = int(security.security_id)
    session.add(
        SymbolAlias(
            security_id=sid,
            alias_kind="ticker",
            alias_value=ticker,
            valid_from=dt.date(2000, 1, 1),
            valid_to=None,
            knowledge_source="test",
            knowledge_time=_close(dt.date(2000, 1, 1)),
            source="test",
        )
    )
    session.add(
        Sector(
            instrument_id=sid,
            scheme="SIC",
            sector=sector,
            valid_from=dt.date(2000, 1, 1),
            valid_to=None,
            ingested_at=_close(dt.date(2000, 1, 1)),
            source="test",
        )
    )
    session.flush()
    return sid


def _bars(session: Session, sid: int, days: list[dt.date], close: str = "100") -> None:
    for day in days:
        session.add(
            SecurityPriceFact(
                security_id=sid,
                session_date=day,
                adjustment_basis="raw",
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal(1_000_000),
                event_time=_close(day),
                knowledge_time=_close(day),
                knowledge_time_basis=KnowledgeTimeBasis.SESSION_CLOSE,
                knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
                source="test",
            )
        )
    session.flush()


def _weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _declared(**overrides: object) -> DeclaredPortfolio:
    payload: dict[str, object] = {
        "mandate": Mandate.SWING,
        "equity": Decimal(100_000),
        "cash": Decimal(50_000),
        "universe": ("QUIET", "LOUD"),
        "warmup_from": WARM,
        "as_of": AS_OF,
        "holdings": (),
        "score_lower": 0.0,
        "score_upper": 0.5,
        # Short windows: the point is the carry, not a crossover.
        "fast": 2,
        "slow": 5,
        "stop_pct": Decimal("0.08"),
    }
    payload.update(overrides)
    return DeclaredPortfolio(**payload)  # type: ignore[arg-type]


def _holding(ticker: str, **overrides: object) -> DeclaredHolding:
    payload: dict[str, object] = {
        "ticker": ticker,
        "quantity": Decimal(100),
        "entry_price": Decimal(100),
        "stop_price": Decimal(90),
        "sessions_held": 5,
        "opened_on": dt.date(2024, 1, 2),
    }
    payload.update(overrides)
    return DeclaredHolding(**payload)  # type: ignore[arg-type]


@pytest.fixture
def corpus(db_session: Session) -> dict[str, int]:
    """Two names. ``LOUD`` prints every weekday; ``QUIET`` stops two days early."""
    days = _weekdays(WARM, AS_OF)
    loud = _listed(db_session, "LOUD")
    quiet = _listed(db_session, "QUIET")
    _bars(db_session, loud, days, close="100")
    _bars(db_session, quiet, days[:-2], close="110")
    return {"LOUD": loud, "QUIET": quiet}


def test_a_quiet_holding_is_carried_at_its_last_close(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """**The engine's rule, now this module's too.**

    The whole horizon used to be refused over one name with no print. Every
    other position's stop went unmanaged as a result, which is precisely the
    failure the refusal was meant to avoid.
    """
    result = plan_declared(
        db_session, _declared(holdings=(_holding("QUIET", entry_price=Decimal(110)),))
    )

    assert len(result.carried) == 1
    ticker, session_date = result.carried[0]
    assert ticker == "QUIET"
    assert session_date < AS_OF, "marked at an earlier session, and it says which"

    (held,) = result.plan.holdings
    assert held.last_price == Decimal(110), "the last price anybody quoted"
    assert held.open_risk == Decimal(100) * (Decimal(110) - Decimal(90))


def test_a_holding_that_printed_today_is_not_reported_as_carried(
    db_session: Session, corpus: dict[str, int]
) -> None:
    result = plan_declared(db_session, _declared(holdings=(_holding("LOUD"),)))

    assert result.carried == ()
    assert result.plan.holdings[0].last_price == Decimal(100)


def test_a_quiet_name_does_not_stall_the_other_positions(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """The reason the refusal was wrong: one quiet name blocked everything."""
    result = plan_declared(
        db_session,
        _declared(
            holdings=(_holding("QUIET", entry_price=Decimal(110)), _holding("LOUD")),
        ),
    )

    assert {h.reference for h in result.plan.holdings} == {"QUIET", "LOUD"}
    assert [t for t, _ in result.carried] == ["QUIET"]


def test_a_holding_that_never_printed_is_refused(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """**No last known price exists, so there is nothing honest to carry.**

    That is an identity problem — the ticker resolves to a security with no
    prices in this window — not a quiet session, and the two want different
    answers.
    """
    ghost = _listed(db_session, "GHOST")
    assert ghost  # listed, with an alias and a sector, and no bars at all

    with pytest.raises(DeclarationError, match="no print anywhere"):
        plan_declared(
            db_session,
            _declared(universe=("LOUD", "GHOST"), holdings=(_holding("GHOST"),)),
        )


def test_an_unresolvable_as_of_names_the_last_session(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """A date the corpus has no session for is refused, naming the nearest."""
    with pytest.raises(DeclarationError, match="not a trading session"):
        plan_declared(db_session, _declared(as_of=dt.date(2024, 3, 2)))  # a Saturday


def test_the_plan_reports_what_it_loaded(db_session: Session, corpus: dict[str, int]) -> None:
    """A plan whose inputs are invisible cannot be checked."""
    result = plan_declared(db_session, _declared())

    assert result.sessions_loaded > 0
    assert result.bars_loaded > 0
    assert result.sectors_classified == 2
    assert result.seconds >= 0


def test_a_mandate_whose_corpus_does_not_exist_declines(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """Day's primary corpus is intraday-01, and a Daily slate would mislabel."""
    with pytest.raises(DeclarationError, match="declines to be scored"):
        plan_declared(db_session, _declared(mandate=Mandate.DAY))


# ---------------------------------------------------------------------------
# The plan and the backtest must install the same rules
#
# Measured 2026-11-19, they did not, and they differed in BOTH directions: the
# backtest installed `PositionSizeRule` and the plan did not, while the plan
# installed `SectorExposureRule` and the backtest did not. A plan showing a
# decision the backtest never validated, and a backtest validating a decision
# the plan would not make, is two systems wearing one name.
# ---------------------------------------------------------------------------


def test_the_plan_installs_exactly_the_rules_the_backtest_does() -> None:
    """One source of truth, asserted rather than assumed.

    The archive's rule set is frozen by what it ran — `build_engine`'s own
    docstring requires every scoreboard result to stay reproducible — so this
    pins the plan to it rather than the other way round.
    """
    from tradeit.backtesting.baselines import base_rules
    from tradeit.decide.runner import build_cycle
    from tradeit.strategy.config import StrategyConfig

    config = StrategyConfig(name="ma_cross_baseline")
    planned = sorted(type(rule).__name__ for rule in build_cycle(config, {}).engine.rules)
    archived = sorted(type(rule).__name__ for rule in base_rules(config))

    assert planned == archived, (
        "the dashboard would show a decision no backtest validated, and the "
        f"backtest would validate one the dashboard would not make: {planned} vs {archived}"
    )


def test_sector_is_still_enforced_even_though_its_engine_rule_is_absent(
    db_session: Session, corpus: dict[str, int]
) -> None:
    """**Absent from the engine is not absent from the decision.**

    `score_fit` vetoes `sector_limit_reached` at the ranker, before any sizing.
    What the engine-stage rule would add is a re-check of a holding that grew
    into the cap by a price move rather than by a new entry, and the reported
    absence has to say that rather than implying sector is unconstrained.
    """
    result = plan_declared(db_session, _declared())
    absent = {control.name: control.reason for control in result.plan.absent}

    assert "SectorExposureRule" in absent
    assert "sector_limit_reached" in absent["SectorExposureRule"]
    assert "grew into the cap" in absent["SectorExposureRule"]
    assert "PositionSizeRule" not in absent, "it is installed now, so it must not be listed"
