"""The decision path, composed and run for one horizon and one session.

What these guard is the step the platform had never taken. The machinery for
"is this a great trade for *this portfolio right now*" was built, tested and
measured *beside* the decision: the one run that produced a stored decision
record ranked with the portfolio awareness switched off and never imported
``tradeit.opportunity``. So the properties under test are about composition,
and three of them are about **honesty of absence** rather than correctness of
arithmetic:

* A refusal names the limit that caused it, and every limit that did.
* A control that cannot fire is reported, and the report is **measured from
  the wiring** rather than listed — a hardcoded list would keep asserting an
  absence after it had been fixed.
* A mandate's timeframe is **checked**, not defaulted. The stored record's
  ``"swing"`` label came from a default argument, so it was right by accident.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import Bartimeframe, PositionStatus, Side, SignalDirection
from tradeit.decide import SessionPlan, absent_controls, plan_session, with_references
from tradeit.opportunity import OpportunityFitRanker, VetoReason
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.mandate import Mandate, TimeframeNotEligible
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopContext, StopLadder
from tradeit.risk.base import RiskRule
from tradeit.risk.contextual import SectorExposureRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
    PyramidRule,
)
from tradeit.strategy.base import OpportunityScore, ScoreComponent
from tradeit.strategy.config import ExitConfig, RiskConfig, SizingConfig

SESSION = dt.date(2015, 6, 1)
AS_OF = dt.datetime.combine(SESSION, dt.time(21), tzinfo=dt.UTC)


def _cycle(
    sectors: dict[int, str] | None = None,
    *,
    fit: bool = True,
    extra: tuple[RiskRule, ...] = (),
    costs: object | None = None,
) -> PortfolioCycle:
    sizing = SizingConfig()
    risk = RiskConfig()
    sectors = sectors if sectors is not None else {}
    rules: tuple[RiskRule, ...] = (
        PortfolioHeatRule(config=risk),
        MaxPositionsRule(config=risk),
        GrossExposureRule(config=risk),
        SectorExposureRule(config=risk, sectors=sectors),
        *extra,
    )
    ranker = (
        OpportunityFitRanker(risk=risk, sizing=sizing, sectors=sectors)
        if fit
        else DiversityAwareRanker(
            correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
        )
    )
    return PortfolioCycle(
        sizer=RiskBasedSizer(
            sizing=sizing,
            risk=risk,
            max_participation=Decimal("0.05"),
            costs=costs,  # type: ignore[arg-type]
        ),
        engine=MostRestrictiveEngine(rule_set=rules, sizing=sizing),
        ranker=ranker,
        ladder=StopLadder(config=ExitConfig()),
    )


def _candidate(instrument_id: int, total: float = 0.8) -> EntryCandidate:
    return EntryCandidate(
        score=OpportunityScore(
            instrument_id=instrument_id,
            session_date=SESSION,
            direction=SignalDirection.LONG,
            total=total,
            components=(
                ScoreComponent(name="setup", raw_value=total, normalised=total, weight=1.0),
            ),
            feature_set_digest="fs",
            strategy_config_digest="sc",
        ),
        entry_price=Decimal(100),
        stop_price=Decimal(95),
    )


def _position(instrument_id: int, quantity: str = "400", stop: str = "95") -> PositionState:
    return PositionState(
        position_id=instrument_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(quantity),
        average_entry_price=Decimal(100),
        stop_price=Decimal(stop),
        opened_on=dt.date(2015, 1, 2),
        initial_stop_price=Decimal(stop),
    )


def _portfolio(
    *positions: PositionState, cash: str = "40000", equity: str = "100000"
) -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=AS_OF,
        cash=Decimal(cash),
        equity=Decimal(equity),
        positions=positions,
        last_prices={p.instrument_id: Decimal(100) for p in positions},
    )


def _context(sessions: int = 5, atr: Decimal | None = None) -> StopContext:
    return StopContext(
        last_price=Decimal(100),
        high_since_entry=Decimal(105),
        sessions_held=sessions,
        partial_profit_taken=False,
        atr=atr,
    )


def _plan(**overrides: object) -> SessionPlan:
    kwargs: dict[str, object] = {
        "mandate": Mandate.SWING,
        "timeframe": Bartimeframe.D1,
        "session_date": SESSION,
        "cycle": _cycle(),
        "portfolio": _portfolio(),
        "candidates": [],
        "contexts": {},
    }
    kwargs.update(overrides)
    return plan_session(**kwargs)  # type: ignore[arg-type]


# -- the mandate is checked, not defaulted ----------------------------------


def test_a_timeframe_outside_the_mandate_is_refused() -> None:
    """The stored record's "swing" label came from a default argument.

    So it was correct by accident rather than by verification. Constructing the
    unit of study checks the timeframe against the mandate's eligible set, and a
    monthly bar is not a swing timeframe.
    """
    with pytest.raises(TimeframeNotEligible):
        _plan(timeframe=Bartimeframe.MN1)

    # And with candidates present, so the check cannot be passing only on the
    # empty-slate path that skips `require_population`.
    with pytest.raises(TimeframeNotEligible):
        _plan(timeframe=Bartimeframe.MN1, candidates=[_candidate(1)])


def test_a_day_with_no_candidates_is_an_outcome_not_an_error() -> None:
    """`require_population` refuses an empty population; a quiet session is not one.

    Refusing here would make "nothing to do today" raise, which is the most
    common honest answer a swing portfolio can give.
    """
    plan = _plan(candidates=[])

    assert plan.candidates_considered == 0
    assert plan.would_buy == ()
    assert plan.refusals == ()


def test_each_mandate_accepts_a_daily_slate_but_reports_its_own_label() -> None:
    """Daily is eligible for all three horizons; the label must not be shared."""
    for mandate in (Mandate.SWING, Mandate.RETIREMENT, Mandate.DAY):
        plan = _plan(mandate=mandate)
        assert plan.mandate == str(mandate)
        assert plan.timeframe == "1d"


# -- the refusal reaches the plan, naming its limits ------------------------


def test_a_refused_candidate_names_every_limit_that_bound_it() -> None:
    """The property the whole composition exists for.

    Reproduces, on scripted values, what a real corpus run produces: a
    candidate refused by two limits at once, both named, with the standing it
    would have had. Before this path ran, the stored record's only vocabulary
    for a refusal was the string ``"sizer"``.
    """
    sectors = {1: "Crowded", 9: "Crowded"}
    # Two limits at once, by arithmetic rather than by hope: the holding is 40%
    # of equity against a 30% sector cap, and risks 400 x $20 = $8,000 = 8% of
    # equity against a 6% heat limit.
    portfolio = _portfolio(_position(9, stop="80"), cash="40000")
    plan = _plan(
        cycle=_cycle(sectors),
        portfolio=portfolio,
        candidates=[_candidate(1)],
        contexts={9: _context()},
    )

    assert plan.would_buy == ()
    assert len(plan.refusals) == 1
    refusal = plan.refusals[0]
    assert str(VetoReason.SECTOR_LIMIT_REACHED) in refusal.reasons
    assert len(refusal.reasons) >= 2, f"two limits bound, both must be named: {refusal.reasons}"
    assert refusal.standing == 1
    assert refusal.ranker == "opportunity_fit"
    assert "fit " in refusal.detail and "sector Crowded" in refusal.detail


def test_a_ranker_refusal_is_not_reported_as_a_risk_decline() -> None:
    """Two mechanisms, two fields, so the acting control stays identifiable."""
    sectors = {1: "Crowded", 9: "Crowded"}
    plan = _plan(
        cycle=_cycle(sectors),
        portfolio=_portfolio(_position(9)),
        candidates=[_candidate(1)],
        contexts={9: _context()},
    )

    assert len(plan.refusals) == 1
    assert plan.declined == (), "it never reached the risk engine"
    assert plan.entries == ()


def test_an_admissible_candidate_is_sized_and_reports_its_binding_constraint() -> None:
    """ "We bought less than we wanted" and "we bought what we wanted" differ."""
    plan = _plan(candidates=[_candidate(1)], portfolio=_portfolio(cash="40000"))

    assert len(plan.would_buy) == 1
    entry = plan.would_buy[0]
    assert entry.quantity > 0
    assert entry.binding_constraint, "the cap that decided the size must be named"
    assert entry.notional == entry.quantity * entry.entry_price
    assert entry.weight == pytest.approx(float(entry.notional / Decimal(100_000)))
    assert entry.standalone == pytest.approx(0.8)


def test_holdings_are_priced_and_weighted_against_equity() -> None:
    plan = _plan(portfolio=_portfolio(_position(9)), contexts={9: _context()})

    assert len(plan.holdings) == 1
    holding = plan.holdings[0]
    assert holding.market_value == Decimal(400) * Decimal(100)
    assert holding.weight == pytest.approx(0.40)
    assert holding.open_risk == Decimal(400) * Decimal(5)


def test_an_exit_appears_as_a_would_sell_with_its_reason() -> None:
    """A time stop at the limit must be reported, not silently carried."""
    plan = _plan(
        portfolio=_portfolio(_position(9)),
        contexts={9: _context(sessions=10_000)},
    )

    assert len(plan.exits) == 1
    assert plan.exits[0].instrument_id == 9
    assert plan.exits[0].reason
    assert plan.exits[0].fraction > 0


def test_a_missing_label_renders_the_id_rather_than_dropping_the_row() -> None:
    """A holding with no ticker is still a holding."""
    plan = _plan(
        portfolio=_portfolio(_position(9)),
        contexts={9: _context()},
        references={},
    )

    assert plan.holdings[0].reference == "9"

    relabelled = with_references(plan, {9: "IBM"})
    assert relabelled.holdings[0].reference == "IBM"
    assert plan.holdings[0].reference == "9", "the original is unchanged"


def test_every_plan_carries_its_evidence_status() -> None:
    """A number on a screen is acted on; a document is not."""
    plan = _plan()

    assert len(plan.caveats) >= 3
    joined = " ".join(plan.caveats)
    assert "not a recommendation" in joined
    assert "intraday-01" in joined, "the missing trigger layer travels with the plan"
    assert "interlock" in joined


# -- absence is measured, not listed ---------------------------------------


def test_an_absent_control_stops_being_reported_once_it_is_installed() -> None:
    """The reason this is measured from the wiring rather than hardcoded.

    A fixed list would keep asserting an absence after somebody fixed it, which
    is worse than not reporting absence at all: it would be a false claim about
    a risk control, which is the one place a false claim costs money.
    """
    without = {control.name for control in absent_controls(_cycle(), {})}
    assert "PyramidRule" in without

    with_rule = _cycle(extra=(PyramidRule(config=SizingConfig()),))
    assert "PyramidRule" not in {control.name for control in absent_controls(with_rule, {})}


def test_the_position_size_rule_absence_tracks_the_engine_not_the_sizer() -> None:
    """The sizer caps position weight too, but the engine is the authority."""
    named = {c.name for c in absent_controls(_cycle(), {})}
    assert "PositionSizeRule" in named

    installed = _cycle(extra=(PositionSizeRule(max_position_pct_of_equity=0.2),))
    assert "PositionSizeRule" not in {c.name for c in absent_controls(installed, {})}


def test_the_trailing_stop_is_reported_absent_only_when_no_context_has_an_atr() -> None:
    """It engages solely on ATR, and both production call sites pass None."""
    no_atr = {c.name for c in absent_controls(_cycle(), {9: _context(atr=None)})}
    assert "trailing_stop" in no_atr

    with_atr = {c.name for c in absent_controls(_cycle(), {9: _context(atr=Decimal(2))})}
    assert "trailing_stop" not in with_atr


def test_the_cash_veto_is_always_reported_unreachable() -> None:
    """Structural, not configuration: it holds for every wiring of this sizer."""
    named = {c.name for c in absent_controls(_cycle(), {})}

    assert "insufficient_cash_veto" in named
    reason = next(
        c.reason for c in absent_controls(_cycle(), {}) if c.name == "insufficient_cash_veto"
    )
    assert "cash / entry_price" in reason


def test_the_plan_reports_the_absences_it_measured() -> None:
    plan = _plan(contexts={})

    assert plan.absent, "a plan that reported no absences would be claiming all controls are armed"
    assert "insufficient_cash_veto" in {control.name for control in plan.absent}


# -- a plain ranker still works --------------------------------------------


def test_a_ranker_without_the_explaining_protocol_still_produces_a_plan() -> None:
    """``ExplainingRanker`` is optional; the old path must not break."""
    plan = _plan(cycle=_cycle(fit=False), candidates=[_candidate(1)])

    assert len(plan.would_buy) == 1
    assert plan.refusals == (), "not reported, which is not the same as nothing refused"


def test_describe_names_the_refusal_so_a_person_can_read_the_plan() -> None:
    sectors = {1: "Crowded", 9: "Crowded"}
    plan = _plan(
        cycle=_cycle(sectors),
        portfolio=_portfolio(_position(9)),
        candidates=[_candidate(1)],
        contexts={9: _context()},
        references={1: "TXN", 9: "IBM"},
    )

    text = plan.describe()
    assert "SWING" in text
    assert "REFUSED" in text
    assert "TXN" in text
    assert "sector_limit_reached" in text
    assert "NOT ARMED" in text
    assert "would have ranked 1" in text


def test_candidates_considered_is_reported_so_an_empty_plan_is_explicable() -> None:
    """Zero buys from zero candidates and zero buys from forty are different facts."""
    assert _plan(candidates=[]).candidates_considered == 0
    assert _plan(candidates=[_candidate(1), _candidate(2)]).candidates_considered == 2
