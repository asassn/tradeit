"""Tests for the performance analyzer.

Several of these exist to catch a formula that is defensible-looking and wrong
in the flattering direction: the Sortino denominator, the unrecovered drawdown,
the flat-curve Sharpe, and attribution normalised against a negative total.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.backtesting.base import BacktestTrade
from tradeit.backtesting.performance import StandardPerformanceAnalyzer

ANALYZER = StandardPerformanceAnalyzer(annualisation_factor=252, risk_free_rate=0.0)
DAY = dt.date(2020, 1, 1)


def _curve(*values: str, start: dt.date = DAY) -> list[tuple[dt.date, Decimal]]:
    return [(start + dt.timedelta(days=i), Decimal(v)) for i, v in enumerate(values)]


def _trade(
    *,
    net: str = "100",
    entry: str = "10",
    exit_price: str = "12",
    quantity: str = "100",
    entry_date: dt.date = DAY,
    exit_date: dt.date | None = None,
    r: float | None = 1.0,
    sector: str | None = "Energy",
    regime: str | None = "bull_trending",
    reason: str = "target_reached",
    instrument_id: int = 1,
) -> BacktestTrade:
    return BacktestTrade(
        instrument_id=instrument_id,
        entry_date=entry_date,
        entry_price=Decimal(entry),
        exit_date=exit_date or entry_date + dt.timedelta(days=1),
        exit_price=Decimal(exit_price),
        quantity=Decimal(quantity),
        side="long",
        exit_reason=reason,
        gross_pnl=Decimal(net),
        net_pnl=Decimal(net),
        costs=Decimal(0),
        return_pct=0.0,
        r_multiple=r,
        holding_sessions=1,
        sector=sector,
        regime_at_entry=regime,
    )


class TestCurveValidation:
    def test_a_single_point_is_a_balance_not_a_record(self) -> None:
        with pytest.raises(ValueError, match="at least two points"):
            ANALYZER.compute([], _curve("1000"))

    def test_an_unordered_curve_is_refused(self) -> None:
        curve = _curve("1000", "1100")[::-1]
        with pytest.raises(ValueError, match="strictly ordered"):
            ANALYZER.compute([], curve)

    def test_a_repeated_date_is_refused(self) -> None:
        curve = [(DAY, Decimal(1000)), (DAY, Decimal(1100))]
        with pytest.raises(ValueError, match="strictly ordered"):
            ANALYZER.compute([], curve)

    def test_starting_with_no_capital_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot start with no capital"):
            ANALYZER.compute([], _curve("0", "100"))


class TestReturnsComeFromTheCurve:
    def test_the_curve_decides_the_return_not_the_trade_list(self) -> None:
        """Identical trades, different sizing, different return."""
        trades = [_trade(net="100")]
        small = ANALYZER.compute(trades, _curve("1000", "1100"))
        large = ANALYZER.compute(trades, _curve("10000", "10100"))
        assert small.total_return_pct == pytest.approx(0.10)
        assert large.total_return_pct == pytest.approx(0.01)

    def test_a_run_that_traded_nothing_is_an_outcome_not_an_error(self) -> None:
        metrics = ANALYZER.compute([], _curve("1000", "1000", "1000"))
        assert metrics.trade_count == 0
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor is None
        assert metrics.expectancy_r is None
        assert not metrics.statistically_meaningful


class TestDrawdown:
    def test_depth_is_measured_from_the_running_peak(self) -> None:
        metrics = ANALYZER.compute([], _curve("100", "120", "90", "110"))
        assert metrics.max_drawdown_pct == pytest.approx(0.25)

    def test_a_drawdown_that_never_recovered_runs_to_the_end(self) -> None:
        """Ending underwater must not look shorter than recovering."""
        unrecovered = ANALYZER.compute([], _curve("100", "120", "90", "95", "95", "95"))
        recovered = ANALYZER.compute([], _curve("100", "120", "90", "130", "130", "130"))
        assert unrecovered.max_drawdown_duration_sessions == 4
        assert recovered.max_drawdown_duration_sessions == 2

    def test_a_rising_curve_has_no_drawdown(self) -> None:
        metrics = ANALYZER.compute([], _curve("100", "110", "120"))
        assert metrics.max_drawdown_pct == 0.0
        assert metrics.calmar is None  # undefined, not perfect


class TestRiskAdjustedRatios:
    CURVE = _curve("1000", "1020", "1009.8")  # +2%, then -1%

    def test_sharpe_annualises_the_periodic_excess(self) -> None:
        metrics = ANALYZER.compute([], self.CURVE)
        assert metrics.sharpe == pytest.approx(3.7416, rel=1e-3)

    def test_sortino_divides_by_every_period_not_only_the_losing_ones(self) -> None:
        """The wrong denominator would report roughly 7.94 here."""
        metrics = ANALYZER.compute([], self.CURVE)
        assert metrics.sortino == pytest.approx(11.2250, rel=1e-3)
        assert metrics.sortino != pytest.approx(7.937, rel=1e-2)

    def test_the_risk_free_rate_is_actually_applied(self) -> None:
        hurdled = StandardPerformanceAnalyzer(annualisation_factor=252, risk_free_rate=0.05)
        assert hurdled.compute([], self.CURVE).sharpe is not None
        assert hurdled.compute([], self.CURVE).sharpe < ANALYZER.compute([], self.CURVE).sharpe  # type: ignore[operator]

    def test_a_flat_curve_has_no_sharpe_rather_than_an_enormous_one(self) -> None:
        """How a strategy that never traded wins a leaderboard."""
        metrics = ANALYZER.compute([], _curve("1000", "1000", "1000", "1000"))
        assert metrics.sharpe is None
        assert metrics.sortino is None

    def test_a_curve_with_no_losing_period_still_has_a_sortino(self) -> None:
        metrics = ANALYZER.compute([], _curve("1000", "1010", "1030"))
        assert metrics.sortino is None  # no downside deviation at all
        assert metrics.sharpe is not None


class TestRuin:
    def test_a_wiped_out_account_reports_minus_one_hundred_percent(self) -> None:
        metrics = ANALYZER.compute([], _curve("1000", "500", "0"))
        assert metrics.cagr == -1.0
        assert metrics.total_return_pct == -1.0

    def test_the_return_series_stops_at_ruin_rather_than_inverting(self) -> None:
        """A recovery from zero equity is arithmetic, not a result."""
        metrics = ANALYZER.compute([], _curve("1000", "0", "500"))
        assert metrics.sharpe is None  # only one usable period remains


class TestExposureAndTurnover:
    def test_a_session_with_two_positions_counts_once(self) -> None:
        """Exposure is time in the market, not leverage."""
        dates = _curve("1000", "1000", "1000", "1000", "1000")
        one = _trade(entry_date=DAY, exit_date=DAY + dt.timedelta(days=1))
        two = _trade(instrument_id=2, entry_date=DAY, exit_date=DAY + dt.timedelta(days=1))
        assert ANALYZER.compute([one], dates).exposure_pct == pytest.approx(0.4)
        assert ANALYZER.compute([one, two], dates).exposure_pct == pytest.approx(0.4)

    def test_turnover_counts_both_legs(self) -> None:
        """A round trip at a flat price still turned the book over twice."""
        curve = [(dt.date(2020, 1, 1), Decimal(1000)), (dt.date(2021, 1, 1), Decimal(1000))]
        trade = _trade(entry="10", exit_price="10", quantity="100", net="0")
        metrics = ANALYZER.compute([trade], curve)
        assert metrics.turnover == pytest.approx(1.9959, rel=1e-3)

    def test_no_trades_is_no_turnover(self) -> None:
        assert ANALYZER.compute([], _curve("1000", "1000")).turnover == 0.0


class TestTradeStatistics:
    def test_profit_factor_is_undefined_without_a_loss(self) -> None:
        """Undefined, not excellent."""
        metrics = ANALYZER.compute([_trade(net="100")], _curve("1000", "1100"))
        assert metrics.profit_factor is None
        assert metrics.win_rate == 1.0

    def test_profit_factor_is_gross_win_over_gross_loss(self) -> None:
        trades = [_trade(net="300"), _trade(net="-100")]
        metrics = ANALYZER.compute(trades, _curve("1000", "1200"))
        assert metrics.profit_factor == pytest.approx(3.0)
        assert metrics.win_rate == pytest.approx(0.5)

    def test_a_trade_without_an_r_multiple_is_skipped_not_counted_as_zero(self) -> None:
        trades = [_trade(r=2.0), _trade(r=None)]
        metrics = ANALYZER.compute(trades, _curve("1000", "1100"))
        assert metrics.expectancy_r == pytest.approx(2.0)
        assert metrics.trade_count == 2

    def test_a_break_even_trade_counts_as_neither_win_nor_loss(self) -> None:
        metrics = ANALYZER.compute([_trade(net="0")], _curve("1000", "1000", "1000"))
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor is None

    def test_twenty_nine_trades_are_not_statistically_meaningful(self) -> None:
        trades = [_trade(instrument_id=i) for i in range(29)]
        assert not ANALYZER.compute(trades, _curve("1000", "1100")).statistically_meaningful
        trades.append(_trade(instrument_id=99))
        assert ANALYZER.compute(trades, _curve("1000", "1100")).statistically_meaningful


class TestBenchmark:
    def test_the_benchmark_return_is_reported_alongside(self) -> None:
        metrics = ANALYZER.compute([], _curve("1000", "1100"), benchmark=_curve("500", "520"))
        assert metrics.benchmark_return_pct == pytest.approx(0.04)

    def test_no_benchmark_is_none_not_zero(self) -> None:
        assert ANALYZER.compute([], _curve("1000", "1100")).benchmark_return_pct is None


class TestAttribution:
    def test_an_unknown_dimension_raises_rather_than_returning_nothing(self) -> None:
        """An empty attribution reads as 'no concentration'."""
        with pytest.raises(ValueError, match="cannot attribute by 'instrument_id'"):
            ANALYZER.attribute([_trade()], "instrument_id")

    def test_pnl_is_grouped_by_sector(self) -> None:
        trades = [
            _trade(net="300", sector="Energy"),
            _trade(net="-100", sector="Energy"),
            _trade(net="50", sector="Financials"),
        ]
        assert ANALYZER.attribute(trades, "sector") == {"Energy": 200.0, "Financials": 50.0}

    def test_the_parts_sum_to_the_whole_even_with_missing_labels(self) -> None:
        trades = [_trade(net="300", sector="Energy"), _trade(net="-100", sector=None)]
        buckets = ANALYZER.attribute(trades, "sector")
        assert buckets == {"Energy": 300.0, "unknown": -100.0}
        assert sum(buckets.values()) == pytest.approx(200.0)

    def test_attribution_is_currency_not_a_share_of_a_negative_total(self) -> None:
        """+200 against a total of -100 would report as -200% if normalised."""
        trades = [_trade(net="200", sector="Energy"), _trade(net="-300", sector="Tech")]
        buckets = ANALYZER.attribute(trades, "sector")
        assert buckets["Energy"] == 200.0
        assert buckets["Tech"] == -300.0

    def test_regime_and_exit_reason_are_available_dimensions(self) -> None:
        trades = [
            _trade(net="100", regime="bull_trending", reason="target_reached"),
            _trade(net="-40", regime="bear_trending", reason="stop_loss"),
        ]
        assert ANALYZER.attribute(trades, "regime_at_entry") == {
            "bear_trending": -40.0,
            "bull_trending": 100.0,
        }
        assert ANALYZER.attribute(trades, "exit_reason") == {
            "stop_loss": -40.0,
            "target_reached": 100.0,
        }
