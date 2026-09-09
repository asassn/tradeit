"""Tests for the daily risk snapshot.

A snapshot is taken whether or not anything traded, so the tests are built
around portfolios that never proposed a trade -- including one that is over its
heat limit purely because prices moved.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tradeit.portfolio.base import PortfolioState, PositionState, PositionStatus, Side
from tradeit.risk.contextual import UNCLASSIFIED, MatrixCorrelationSource
from tradeit.risk.snapshot import build_snapshot, sector_exposures
from tradeit.strategy.config import RiskConfig

AS_OF = dt.datetime(2024, 6, 3, 21, 0, tzinfo=dt.UTC)
SESSION = dt.date(2024, 6, 3)


def _position(
    instrument_id: int,
    *,
    quantity: str = "100",
    entry: str = "100",
    stop: str = "95",
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
        initial_stop_price=Decimal(stop),
    )


def _portfolio(
    *positions: PositionState,
    equity: str = "100000",
    cash: str = "50000",
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


class TestSectorExposures:
    def test_each_sector_gets_its_share_of_equity(self) -> None:
        portfolio = _portfolio(_position(1), _position(2), _position(3))
        exposures = sector_exposures(portfolio, {1: "Energy", 2: "Energy", 3: "Financials"})
        assert exposures == {"Energy": 0.20, "Financials": 0.10}

    def test_unclassified_holdings_are_pooled_under_their_own_name(self) -> None:
        """Visible as a coverage gap rather than mistaken for a sector."""
        portfolio = _portfolio(_position(1), _position(2))
        exposures = sector_exposures(portfolio, {1: "Energy"})
        assert exposures == {"Energy": 0.10, UNCLASSIFIED: 0.10}

    def test_an_empty_portfolio_has_no_exposures(self) -> None:
        assert sector_exposures(_portfolio(), {}) == {}


class TestSnapshot:
    def test_a_quiet_portfolio_records_its_metrics_and_no_breaches(self) -> None:
        portfolio = _portfolio(_position(1), _position(2))
        snapshot = build_snapshot(portfolio, RiskConfig(), session_date=SESSION)
        assert snapshot.session_date == SESSION
        assert snapshot.as_of == AS_OF
        assert snapshot.position_count == 2
        assert snapshot.open_risk == Decimal(1000)
        assert snapshot.heat == Decimal("0.01")
        assert snapshot.gross_exposure_pct == Decimal("0.2")
        assert snapshot.largest_position_pct == Decimal("0.1")
        assert snapshot.limit_breaches == ()

    def test_a_breach_is_recorded_without_anybody_trading(self) -> None:
        """Prices moved; the book is outside its heat limit this morning."""
        positions = tuple(_position(i, quantity="1000", stop="88") for i in range(1, 6))
        portfolio = _portfolio(*positions, equity="1000000")
        snapshot = build_snapshot(portfolio, RiskConfig(), session_date=SESSION)
        assert snapshot.heat == Decimal("0.06")
        moved = _portfolio(*positions, equity="900000")
        breached = build_snapshot(moved, RiskConfig(), session_date=SESSION)
        assert "portfolio_heat" in breached.limit_breaches

    def test_a_concentrated_sector_is_a_breach(self) -> None:
        positions = tuple(_position(i, quantity="1000") for i in range(1, 5))
        portfolio = _portfolio(*positions, equity="1000000")
        snapshot = build_snapshot(
            portfolio,
            RiskConfig(),
            session_date=SESSION,
            sectors=dict.fromkeys(range(1, 5), "Energy"),
        )
        assert snapshot.sector_exposures == {"Energy": 0.4}
        assert "sector_exposure" in snapshot.limit_breaches

    def test_a_drawdown_at_the_halt_is_a_breach(self) -> None:
        snapshot = build_snapshot(
            _portfolio(equity="85000"),
            RiskConfig(),
            session_date=SESSION,
            peak_equity=Decimal(100000),
        )
        assert snapshot.drawdown_from_peak == Decimal("0.15")
        assert "max_drawdown" in snapshot.limit_breaches

    def test_no_peak_means_no_drawdown_claim(self) -> None:
        snapshot = build_snapshot(_portfolio(equity="10"), RiskConfig(), session_date=SESSION)
        assert snapshot.drawdown_from_peak == Decimal(0)
        assert "max_drawdown" not in snapshot.limit_breaches


class TestWorstCorrelation:
    def test_the_worst_held_pair_is_reported(self) -> None:
        portfolio = _portfolio(_position(1), _position(2), _position(3))
        snapshot = build_snapshot(
            portfolio,
            RiskConfig(),
            session_date=SESSION,
            correlation=MatrixCorrelationSource(values={(1, 2): 0.2, (1, 3): 0.81, (2, 3): 0.4}),
        )
        assert snapshot.max_pairwise_correlation == 0.81
        assert "correlation_cluster" in snapshot.limit_breaches

    def test_one_unmeasured_pair_makes_the_whole_figure_unknown(self) -> None:
        """A maximum over the known pairs is systematically too low."""
        portfolio = _portfolio(_position(1), _position(2), _position(3))
        snapshot = build_snapshot(
            portfolio,
            RiskConfig(),
            session_date=SESSION,
            correlation=MatrixCorrelationSource(values={(1, 2): 0.2, (1, 3): 0.81}),
        )
        assert snapshot.max_pairwise_correlation is None
        assert "correlation_cluster" not in snapshot.limit_breaches

    def test_a_single_position_has_no_pair(self) -> None:
        snapshot = build_snapshot(
            _portfolio(_position(1)),
            RiskConfig(),
            session_date=SESSION,
            correlation=MatrixCorrelationSource(values={}),
        )
        assert snapshot.max_pairwise_correlation is None

    def test_no_correlation_source_reports_unknown_not_zero(self) -> None:
        portfolio = _portfolio(_position(1), _position(2))
        snapshot = build_snapshot(portfolio, RiskConfig(), session_date=SESSION)
        assert snapshot.max_pairwise_correlation is None


def test_the_configuration_digest_is_carried_onto_the_snapshot() -> None:
    snapshot = build_snapshot(
        _portfolio(),
        RiskConfig(),
        session_date=SESSION,
        strategy_config_digest="abc123",
    )
    assert snapshot.strategy_config_digest == "abc123"
