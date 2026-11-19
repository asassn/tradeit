"""``/plan``: the route the dashboard exists for.

Everything built before this answered *"is this a great stock"* — prices,
fundamentals, filings, provenance. This one answers *"is this a great trade for
this portfolio right now"*, which CLAUDE.md says is the only question that may
authorise a trade.

The route was exercised against the real 73 GB corpus, which these tests do not
touch: on ``docs/examples/portfolios.example.json`` it returned 200 with Swing
refusing ``TXN`` for ``['sector_limit_reached', 'heat_limit_exceeded']`` at
standing 1, Retirement planning flat, and both carrying their measured
``not_armed`` lists. What is tested here is what the route decides on its own —
and most of it is about **saying which input is missing** instead of answering
empty, because an empty plan and a portfolio with nothing to do look identical.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradeit.api import create_app
from tradeit.api.plan_route import plan_payload, serialise_plan
from tradeit.decide import (
    AbsentControl,
    Holding,
    ProposedEntry,
    ProposedExit,
    Refusal,
    SessionPlan,
)
from tradeit.storage.tables import Base

SESSION = dt.date(2015, 11, 27)


@pytest.fixture
def record(tmp_path: Path) -> str:
    dsn = f"sqlite:///{tmp_path / 'record.sqlite'}"
    Base.metadata.create_all(create_engine(dsn, future=True))
    return dsn


@pytest.fixture
def corpus(tmp_path: Path) -> str:
    """Schema-valid and empty. Enough for the decisions tested here."""
    dsn = f"sqlite:///{tmp_path / 'corpus.sqlite'}"
    Base.metadata.create_all(create_engine(dsn, future=True))
    return dsn


def _declaration(tmp_path: Path, **overrides: object) -> Path:
    entry: dict[str, object] = {
        "mandate": "swing",
        "equity": "100000",
        "cash": "40000",
        "universe": ["IBM"],
        "warmup_from": "2014-01-02",
        "as_of": "2015-11-27",
        "score_range": {"lower": 0.0, "upper": 0.5},
        "holdings": [],
    }
    entry.update(overrides)
    path = tmp_path / "declaration.json"
    path.write_text(json.dumps({"portfolios": [entry]}))
    return path


# -- which input is missing is named ---------------------------------------


def test_without_a_declaration_the_route_says_so(record: str, corpus: str) -> None:
    """**Not an empty plan.**

    A plan with no holdings and no buys is a legitimate answer — a quiet
    session on a flat portfolio. So a missing declaration cannot be allowed to
    produce one, or the two become indistinguishable.
    """
    response = TestClient(create_app(record, corpus)).get("/plan")

    assert response.status_code == 501
    detail = response.json()["detail"]
    assert "declaration" in detail
    assert "portfolios or positions row" in detail, "it should say why a file is needed at all"


def test_without_a_corpus_the_route_says_so(record: str, tmp_path: Path) -> None:
    """A plan is made against prices, and the record holds none."""
    app = create_app(record, declaration=_declaration(tmp_path))

    response = TestClient(app).get("/plan")

    assert response.status_code == 501
    assert "corpus" in response.json()["detail"]


def test_a_refused_declaration_is_reported_with_its_reason(
    record: str, corpus: str, tmp_path: Path
) -> None:
    """The file is read per request, so a bad edit is visible without a restart."""
    bad = tmp_path / "declaration.json"
    bad.write_text(json.dumps({"portfolios": [{"mandate": "swing"}]}))
    app = create_app(record, corpus, declaration=bad)

    body = TestClient(app).get("/plan").json()

    assert body["available"] is False
    assert "declaration refused" in body["note"]
    assert body["portfolios"] == []


def test_a_horizon_that_declines_stays_in_the_response(
    record: str, corpus: str, tmp_path: Path
) -> None:
    """**It keeps its reason rather than vanishing.**

    Day's primary corpus is ``intraday-01``, which does not exist. A client
    that simply received fewer horizons could not tell a declining Day from a
    Day nobody declared.
    """
    app = create_app(record, corpus, declaration=_declaration(tmp_path, mandate="day"))

    body = TestClient(app).get("/plan").json()

    assert body["available"] is True
    (entry,) = body["portfolios"]
    assert entry["mandate"] == "day"
    assert entry["available"] is False
    assert "intraday-01" in entry["note"]
    assert "declines to be scored" in entry["note"]
    # The declaration is echoed even for a horizon that cannot be planned, so
    # the reader can see what was asked for.
    assert entry["declared"]["equity"] == 100_000.0


def test_the_declaration_travels_with_the_plan(record: str, corpus: str, tmp_path: Path) -> None:
    """A plan whose inputs are invisible cannot be checked.

    "Why is equity 100,000?" has no answer on screen unless the declaration is
    beside the result.
    """
    app = create_app(record, corpus, declaration=_declaration(tmp_path, mandate="day"))

    (entry,) = TestClient(app).get("/plan").json()["portfolios"]

    declared = entry["declared"]
    assert declared["equity"] == 100_000.0
    assert declared["cash"] == 40_000.0
    assert declared["as_of"] == "2015-11-27"
    assert declared["score_range"] == {"lower": 0.0, "upper": 0.5}


def test_an_empty_corpus_reports_the_horizon_unplannable_rather_than_flat(
    record: str, corpus: str, tmp_path: Path
) -> None:
    """No ticker resolving is a reason, not a portfolio with nothing to do."""
    app = create_app(record, corpus, declaration=_declaration(tmp_path))

    (entry,) = TestClient(app).get("/plan").json()["portfolios"]

    assert entry["available"] is False
    assert "resolved" in entry["note"]


def test_the_payload_names_the_declaration_it_read(
    record: str, corpus: str, tmp_path: Path
) -> None:
    path = _declaration(tmp_path, mandate="day")
    session = sessionmaker(bind=create_engine(corpus, future=True))()

    payload = plan_payload(session, path)

    assert payload["declaration"] == str(path)
    assert payload["generated_at"], "an age-less plan is a number of unknown vintage"


# -- the shape of a serialised plan ----------------------------------------


def _plan() -> SessionPlan:
    return SessionPlan(
        mandate="swing",
        timeframe="1d",
        session_date=SESSION,
        equity=Decimal(100_000),
        cash=Decimal(40_000),
        heat=0.1545,
        position_count=1,
        holdings=(
            Holding(
                instrument_id=7,
                reference="IBM",
                quantity=Decimal(400),
                average_entry_price=Decimal("138.46"),
                last_price=Decimal("138.46"),
                stop_price=Decimal("110.00"),
                market_value=Decimal("55384.00"),
                open_risk=Decimal("11384.00"),
                weight=0.5538,
            ),
        ),
        entries=(
            ProposedEntry(
                instrument_id=9,
                reference="TXN",
                quantity=Decimal(108),
                entry_price=Decimal("57.55"),
                stop_price=Decimal("52.95"),
                notional=Decimal("6215.40"),
                risk_amount=Decimal("497.23"),
                risk_fraction=Decimal("0.005"),
                binding_constraint="risk_per_trade",
                weight=0.0622,
                standalone=0.0068,
                accepted=True,
            ),
            ProposedEntry(
                instrument_id=11,
                reference="AMD",
                quantity=Decimal(0),
                entry_price=Decimal(10),
                stop_price=Decimal(9),
                notional=Decimal(0),
                risk_amount=Decimal(0),
                risk_fraction=Decimal(0),
                binding_constraint="portfolio_heat_budget",
                weight=0.0,
                standalone=0.4,
                accepted=False,
                declined_by="portfolio_heat",
                detail="heat budget exhausted",
            ),
        ),
        exits=(
            ProposedExit(
                instrument_id=7,
                reference="IBM",
                reason="stop_hit",
                fraction=Decimal(1),
                detail="stop hit at 110.00",
            ),
        ),
        refusals=(
            Refusal(
                instrument_id=9,
                reference="TXN",
                reasons=("sector_limit_reached", "heat_limit_exceeded"),
                ranker="opportunity_fit",
                standing=1,
                detail="fit 0.3455, total 0.1423, sector Manufacturing",
            ),
        ),
        absent=(AbsentControl(name="trailing_stop", reason="no ATR in any stop context"),),
        caveats=("not a recommendation",),
        candidates_considered=3,
    )


def test_a_refusal_keeps_every_limit_that_bound_it() -> None:
    """One veto per candidate would make a second binding limit look slack."""
    body = serialise_plan(_plan())

    (refused,) = body["refused"]
    assert refused["reasons"] == ["sector_limit_reached", "heat_limit_exceeded"]
    assert refused["standing"] == 1
    assert refused["ranker"] == "opportunity_fit"


def test_a_ranker_refusal_and_a_risk_decline_are_separate_keys() -> None:
    """Two mechanisms, two keys, so the acting control stays identifiable."""
    body = serialise_plan(_plan())

    assert [r["reference"] for r in body["refused"]] == ["TXN"]
    assert [d["reference"] for d in body["declined"]] == ["AMD"]
    assert body["declined"][0]["declined_by"] == "portfolio_heat"
    assert [b["reference"] for b in body["would_buy"]] == ["TXN"]


def test_an_entry_carries_the_constraint_that_sized_it() -> None:
    body = serialise_plan(_plan())

    (buy,) = body["would_buy"]
    assert buy["binding_constraint"] == "risk_per_trade"
    assert buy["quantity"] == 108.0
    assert buy["risk_fraction"] == pytest.approx(0.005)


def test_the_absent_controls_and_caveats_survive_serialisation() -> None:
    """A plan that listed only what fired would look identical to one fully armed."""
    body = serialise_plan(_plan())

    assert body["not_armed"] == [{"name": "trailing_stop", "reason": "no ATR in any stop context"}]
    assert body["caveats"] == ["not a recommendation"]


def test_every_figure_is_a_number_the_client_can_format() -> None:
    """No Decimals, which do not survive JSON, and no pre-formatted strings.

    The web client formats every figure in one place so that one fact cannot be
    rendered two ways; a server that pre-formatted would route around that.
    """
    body = serialise_plan(_plan())

    assert isinstance(body["equity"], float)
    assert isinstance(body["holdings"][0]["market_value"], float)
    assert isinstance(body["would_buy"][0]["notional"], float)
    assert isinstance(body["would_sell"][0]["fraction"], float)
    json.dumps(body)


def test_a_holding_with_no_stop_serialises_as_null_not_zero() -> None:
    """Zero is a stop at zero. Null is no stop, and the difference is total risk."""
    import dataclasses

    plan = _plan()
    stopless = dataclasses.replace(plan.holdings[0], stop_price=None)
    body = serialise_plan(dataclasses.replace(plan, holdings=(stopless,)))

    assert body["holdings"][0]["stop_price"] is None
