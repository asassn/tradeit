"""Tests for the portfolio cycle.

Two of these carry the design. ``test_limits_are_cumulative_across_one_slate``
fails if each candidate is measured against the opening snapshot instead of the
provisional state, which is the bug that lets an entire slate through a heat
limit. ``test_an_exit_frees_capital_for_an_entry_in_the_same_cycle`` fails if
entries are planned before exits, which is the bug that makes the portfolio
decline a trade for want of a budget it was about to release.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import ExitReason, PositionStatus, Side
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopContext, StopLadder
from tradeit.risk.base import RiskRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PyramidRule,
)
from tradeit.strategy.base import OpportunityScore, ScoreComponent, SignalDirection
from tradeit.strategy.config import ExitConfig, RiskConfig, SizingConfig

AS_OF = dt.datetime(2024, 6, 3, 21, 0, tzinfo=dt.UTC)


def _cycle(
    *,
    sizing: SizingConfig | None = None,
    risk: RiskConfig | None = None,
    pyramid: bool = False,
) -> PortfolioCycle:
    sizing = sizing or SizingConfig()
    risk = risk or RiskConfig()
    rules: list[RiskRule] = [
        PortfolioHeatRule(config=risk),
        MaxPositionsRule(config=risk),
        GrossExposureRule(config=risk),
    ]
    if pyramid:
        rules.append(PyramidRule(config=sizing))
    return PortfolioCycle(
        sizer=RiskBasedSizer(sizing=sizing, risk=risk, max_participation=Decimal("0.05")),
        engine=MostRestrictiveEngine(rule_set=tuple(rules), sizing=sizing),
        ranker=DiversityAwareRanker(
            correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
        ),
        ladder=StopLadder(config=ExitConfig()),
    )


def _candidate(
    instrument_id: int,
    *,
    total: float = 1.0,
    entry: str = "100",
    stop: str = "95",
) -> EntryCandidate:
    score = OpportunityScore(
        instrument_id=instrument_id,
        session_date=dt.date(2024, 6, 3),
        direction=SignalDirection.LONG,
        total=total,
        components=(ScoreComponent(name="trend", raw_value=total, normalised=total, weight=1.0),),
        feature_set_digest="fs",
        strategy_config_digest="sc",
    )
    return EntryCandidate(score=score, entry_price=Decimal(entry), stop_price=Decimal(stop))


def _position(
    instrument_id: int,
    *,
    quantity: str = "100",
    entry: str = "100",
    stop: str = "95",
    initial_stop: str | None = None,
    entries: int = 1,
) -> PositionState:
    return PositionState(
        position_id=instrument_id,
        portfolio_id=1,
        instrument_id=instrument_id,
        side=Side.LONG,
        status=PositionStatus.OPEN,
        quantity=Decimal(quantity),
        average_entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        opened_on=dt.date(2024, 5, 1),
        initial_stop_price=Decimal(initial_stop if initial_stop is not None else stop),
        pyramid_entries=entries,
    )


def _portfolio(
    *positions: PositionState,
    cash: str = "100000",
    equity: str = "100000",
    last_prices: dict[int, Decimal] | None = None,
) -> PortfolioState:
    return PortfolioState(
        portfolio_id=1,
        as_of=AS_OF,
        cash=Decimal(cash),
        equity=Decimal(equity),
        positions=positions,
        last_prices=last_prices or {p.instrument_id: Decimal(100) for p in positions},
    )


def _context(last: str, *, sessions: int = 5, high: str | None = None) -> StopContext:
    return StopContext(
        last_price=Decimal(last),
        high_since_entry=Decimal(high or last),
        sessions_held=sessions,
        partial_profit_taken=False,
        atr=None,
    )


class TestCumulativeAdmission:
    """Each admitted entry must be visible to the next candidate.

    Against the opening snapshot every one of thirty identical candidates
    passes every limit, because none of them can see the others. Which limit
    stops the run depends on the configuration, and all three are exercised
    here -- a single case would pass just as happily if the wrong limit were
    doing the work.
    """

    def _slate(self, count: int = 30, stop: str = "95") -> list[EntryCandidate]:
        return [_candidate(i, total=1.0 - i / 100, stop=stop) for i in range(1, count + 1)]

    def test_cash_stops_the_run_under_the_default_configuration(self) -> None:
        plan = _cycle().plan(_portfolio(), self._slate(), {})
        assert len(plan.entries) == 30
        assert len(plan.accepted) == 10  # $10,000 a position against $100,000
        assert plan.projected.cash == Decimal(0)
        assert "available_cash" in plan.declined[0].explain()

    def test_heat_stops_the_run_when_risk_per_trade_is_larger(self) -> None:
        cycle = _cycle(sizing=SizingConfig(risk_per_trade_pct=0.01))
        plan = cycle.plan(_portfolio(), self._slate(stop="80"), {})
        assert len(plan.accepted) == 6  # 6% heat / 1% per trade
        assert plan.projected.heat() == Decimal("0.06")
        assert "portfolio_heat_budget" in plan.declined[0].explain()

    def test_the_position_count_stops_the_run_when_neither_binds_first(self) -> None:
        cycle = _cycle(sizing=SizingConfig(risk_per_trade_pct=0.0025))
        plan = cycle.plan(_portfolio(), self._slate(stop="80"), {})
        assert len(plan.accepted) == 12
        assert plan.projected.heat() < Decimal("0.06")
        assert plan.projected.cash > Decimal(0)
        assert "max_positions" in plan.declined[0].explain()

    def test_declined_candidates_are_kept_with_their_reason(self) -> None:
        plan = _cycle().plan(_portfolio(), self._slate(), {})
        assert len(plan.declined) == 20
        assert all(entry.explain() for entry in plan.declined)
        assert all(entry.quantity == Decimal(0) for entry in plan.declined)


class TestCapitalRecycling:
    def test_an_exit_frees_capital_for_an_entry_in_the_same_cycle(self) -> None:
        """Entries planned before exits would decline this trade."""
        held = _position(9, quantity="950", entry="100", stop="95")
        portfolio = _portfolio(held, cash="5000", equity="100000")
        cycle = _cycle()
        # The held name has broken its stop, so it leaves and returns $90,250.
        plan = cycle.plan(portfolio, [_candidate(1)], {9: _context("95")})
        assert [signal.reason for signal in plan.exits] == [ExitReason.STOP_LOSS]
        assert plan.accepted
        assert plan.accepted[0].sizing.binding_constraint == "risk_per_trade"

    def test_without_the_exit_the_same_entry_is_capped_by_cash(self) -> None:
        held = _position(9, quantity="950", entry="100", stop="95")
        portfolio = _portfolio(held, cash="5000", equity="100000")
        cycle = _cycle()
        # 1R above entry: the stop moves to breakeven, but nothing exits.
        plan = cycle.plan(portfolio, [_candidate(1)], {9: _context("105", high="105")})
        assert not plan.exits
        assert plan.accepted[0].sizing.binding_constraint == "available_cash"

    def test_a_partial_exit_returns_only_its_share(self) -> None:
        held = _position(9, quantity="300", entry="100", stop="95")
        portfolio = _portfolio(held, cash="0", equity="100000")
        cycle = _cycle()
        # 5R above entry triggers the partial-profit exit of a third.
        plan = cycle.plan(portfolio, [], {9: _context("125", high="125")})
        assert [signal.reason for signal in plan.exits] == [ExitReason.PARTIAL_PROFIT]
        assert plan.projected.cash == Decimal("300") * Decimal("0.33") * Decimal(125)
        remaining = plan.projected.open_positions[0]
        assert remaining.quantity == Decimal("300") * Decimal("0.67")


class TestStopManagement:
    def test_stops_are_advanced_for_every_open_position(self) -> None:
        positions = (_position(8), _position(9))
        plan = _cycle().plan(
            _portfolio(*positions),
            [],
            {8: _context("110"), 9: _context("104")},
        )
        assert len(plan.stop_updates) == 2
        moved = {update.instrument_id: update for update in plan.moved_stops}
        assert set(moved) == {8}  # only the one at 1R or better
        assert moved[8].stop_price == Decimal(100)

    def test_a_held_position_with_no_market_context_is_an_error(self) -> None:
        """Skipping it would leave a position unprotected and say nothing."""
        with pytest.raises(ValueError, match=r"no market context for held instruments \[9\]"):
            _cycle().plan(_portfolio(_position(9)), [], {})

    def test_a_duplicate_candidate_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"more than one candidate for \[1\]"):
            _cycle().plan(_portfolio(), [_candidate(1), _candidate(1, entry="200")], {})


class TestPyramiding:
    def test_an_add_merges_into_the_held_position(self) -> None:
        held = _position(7, quantity="100", entry="100", stop="98", initial_stop="95")
        portfolio = _portfolio(held, cash="100000", last_prices={7: Decimal(110)})
        cycle = _cycle(pyramid=True)
        plan = cycle.plan(
            portfolio,
            [_candidate(7, entry="110", stop="104")],
            {7: _context("110", high="110")},
        )
        assert plan.accepted
        assert len(plan.projected.open_positions) == 1
        merged = plan.projected.open_positions[0]
        assert merged.pyramid_entries == 2
        assert merged.quantity > Decimal(100)
        assert Decimal(100) < merged.average_entry_price < Decimal(110)

    def test_an_add_to_a_loser_is_declined(self) -> None:
        held = _position(7, quantity="100", entry="100", stop="98", initial_stop="95")
        portfolio = _portfolio(held, cash="100000", last_prices={7: Decimal(99)})
        cycle = _cycle(pyramid=True)
        plan = cycle.plan(
            portfolio,
            [_candidate(7, entry="99", stop="94")],
            {7: _context("99", high="101")},
        )
        assert not plan.accepted
        assert "averaging down" in plan.declined[0].explain()


def test_an_empty_slate_still_manages_what_is_held() -> None:
    plan = _cycle().plan(_portfolio(_position(9)), [], {9: _context("110")})
    assert plan.entries == ()
    assert plan.capital_deployed == Decimal(0)
    assert len(plan.moved_stops) == 1


def test_capital_deployed_matches_the_cash_the_projection_spent() -> None:
    cycle = _cycle()
    candidates = [_candidate(i, total=1.0 - i / 100) for i in range(1, 6)]
    plan = cycle.plan(_portfolio(), candidates, {})
    assert plan.capital_deployed == Decimal(100000) - plan.projected.cash
