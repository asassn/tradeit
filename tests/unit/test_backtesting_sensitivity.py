"""Tests for parameter-perturbation sensitivity.

The pair that carries the design is ``test_a_fitted_strategy_is_exposed`` and
``test_a_robust_strategy_survives``: the module must distinguish a result that
depended on the exact parameters from one that did not, and a measure that
cannot separate those two is decoration.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.backtesting.base import (
    BacktestResult,
    BacktestSpec,
    BacktestStatus,
    MonteCarloSpec,
    PerformanceMetrics,
)
from tradeit.backtesting.sensitivity import ParameterPerturbation, read_path
from tradeit.core.enums import ArtifactKind
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest
from tradeit.strategy.config import StrategyConfig

UTC = dt.UTC
NOW = dt.datetime(2024, 6, 1, tzinfo=UTC)
RISK = "sizing.risk_per_trade_pct"
TRAIL = "exits.trailing_stop_atr_multiple"


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="s",
        as_of=NOW,
        strategy_config=ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "b", {"r": 1}),
        data_snapshot=ArtifactVersion.of(ArtifactKind.DATA_SNAPSHOT, "d", {"n": 1}),
        feature_set=None,
        model=None,
        code_version="test",
        created_at=NOW,
    )


def _spec() -> BacktestSpec:
    return BacktestSpec(
        name="run",
        start=dt.date(2014, 1, 1),
        end=dt.date(2024, 1, 1),
        universe="u",
        initial_capital=Decimal(100000),
        strategy_config_digest="cfg",
        cost_model="c",
        fill_model="f",
    )


def _result(total_return: float, drawdown: float = 0.2) -> BacktestResult:
    return BacktestResult(
        spec=_spec(),
        manifest=_manifest(),
        status=BacktestStatus.COMPLETED,
        metrics=PerformanceMetrics(
            total_return_pct=total_return,
            cagr=0.1,
            max_drawdown_pct=drawdown,
            max_drawdown_duration_sessions=20,
            sharpe=1.0,
            sortino=1.2,
            calmar=0.5,
            win_rate=0.5,
            profit_factor=1.5,
            expectancy_r=0.2,
            average_win_r=1.0,
            average_loss_r=-0.5,
            trade_count=100,
            exposure_pct=0.5,
            turnover=2.0,
        ),
        trades=(),
        equity_curve=((_spec().start, Decimal(100000)), (_spec().end, Decimal(150000))),
    )


BASE = StrategyConfig(name="baseline")
BASELINE_RETURN = 0.50


def _mc(**overrides: object) -> MonteCarloSpec:
    payload: dict[str, object] = {
        "method": "parameter_perturbation",
        "iterations": 200,
        "seed": 11,
        "perturbation_pct": 0.10,
    }
    payload.update(overrides)
    return MonteCarloSpec(**payload)  # type: ignore[arg-type]


class TestPathHandling:
    def test_a_numeric_path_reads(self) -> None:
        assert read_path(BASE, RISK) == pytest.approx(0.005)

    def test_a_non_numeric_path_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be perturbed"):
            read_path(BASE, "name")

    def test_a_boolean_is_not_a_number_for_this_purpose(self) -> None:
        with pytest.raises(ValueError, match="cannot be perturbed"):
            read_path(BASE, "sizing.allow_fractional_shares")

    def test_a_bad_path_fails_at_construction_not_mid_run(self) -> None:
        with pytest.raises(AttributeError):
            ParameterPerturbation(
                base=BASE, parameters=("sizing.no_such_field",), rerun=lambda c: _result(0.1)
            )

    def test_perturbing_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one dotted path"):
            ParameterPerturbation(base=BASE, parameters=(), rerun=lambda c: _result(0.1))


class TestFragility:
    def test_a_robust_strategy_survives(self) -> None:
        """Every jittered configuration does about as well as the tuned one."""
        runner = ParameterPerturbation(
            base=BASE, parameters=(RISK, TRAIL), rerun=lambda c: _result(0.48)
        )
        report = runner.report(_result(BASELINE_RETURN), _mc())
        assert report.fragility == pytest.approx(0.04)
        assert report.survives(max_fragility=0.30)

    def test_a_fitted_strategy_is_exposed(self) -> None:
        """The edge existed only at the exact parameters chosen."""

        def collapse(config: StrategyConfig) -> BacktestResult:
            exact = config.sizing.risk_per_trade_pct == BASE.sizing.risk_per_trade_pct
            return _result(BASELINE_RETURN if exact else 0.01)

        runner = ParameterPerturbation(base=BASE, parameters=(RISK, TRAIL), rerun=collapse)
        report = runner.report(_result(BASELINE_RETURN), _mc())
        assert report.fragility is not None
        assert report.fragility > 0.9
        assert not report.survives(max_fragility=0.30)

    def test_a_median_that_loses_money_reports_above_one(self) -> None:
        """The loudest signal available: the tuned run made money, the jitter did not."""
        runner = ParameterPerturbation(
            base=BASE, parameters=(RISK,), rerun=lambda c: _result(-0.20)
        )
        report = runner.report(_result(BASELINE_RETURN), _mc())
        assert report.fragility is not None
        assert report.fragility > 1.0

    def test_a_baseline_with_no_edge_has_undefined_fragility(self) -> None:
        """A share of zero is not zero, and 0.0 would read as perfectly robust."""
        runner = ParameterPerturbation(base=BASE, parameters=(RISK,), rerun=lambda c: _result(0.1))
        report = runner.report(_result(0.0), _mc())
        assert report.fragility is None
        assert report.survives(max_fragility=0.3) is None
        assert "no edge to test" in report.explain()


class TestTheJitter:
    def test_every_named_parameter_moves_in_the_same_draw(self) -> None:
        """One-at-a-time analysis understates fragility; this is not that."""
        seen: list[StrategyConfig] = []

        def capture(config: StrategyConfig) -> BacktestResult:
            seen.append(config)
            return _result(0.4)

        runner = ParameterPerturbation(base=BASE, parameters=(RISK, TRAIL), rerun=capture)
        runner.report(_result(BASELINE_RETURN), _mc(iterations=100))
        both_moved = [
            c
            for c in seen
            if c.sizing.risk_per_trade_pct != BASE.sizing.risk_per_trade_pct
            and c.exits.trailing_stop_atr_multiple != BASE.exits.trailing_stop_atr_multiple
        ]
        assert len(both_moved) == len(seen)

    def test_the_jitter_stays_inside_the_configured_band(self) -> None:
        seen: list[float] = []

        def capture(config: StrategyConfig) -> BacktestResult:
            seen.append(config.sizing.risk_per_trade_pct)
            return _result(0.4)

        ParameterPerturbation(base=BASE, parameters=(RISK,), rerun=capture).report(
            _result(BASELINE_RETURN), _mc(perturbation_pct=0.10)
        )
        base_value = BASE.sizing.risk_per_trade_pct
        assert all(abs(v - base_value) <= base_value * 0.1 + 1e-12 for v in seen)

    def test_jitter_is_relative_so_small_parameters_move_by_small_amounts(self) -> None:
        seen: list[float] = []

        def capture(config: StrategyConfig) -> BacktestResult:
            seen.append(config.sizing.risk_per_trade_pct)
            return _result(0.4)

        ParameterPerturbation(base=BASE, parameters=(RISK,), rerun=capture).report(
            _result(BASELINE_RETURN), _mc(iterations=100)
        )
        # An absolute 10% jitter would push a 0.005 parameter wildly out of range.
        assert all(0.004 <= v <= 0.006 for v in seen)

    def test_an_integer_parameter_stays_an_integer(self) -> None:
        seen: list[int] = []

        def capture(config: StrategyConfig) -> BacktestResult:
            seen.append(config.risk.max_positions)
            return _result(0.4)

        ParameterPerturbation(base=BASE, parameters=("risk.max_positions",), rerun=capture).report(
            _result(BASELINE_RETURN), _mc(iterations=100, perturbation_pct=0.30)
        )
        assert seen
        assert all(isinstance(v, int) for v in seen)
        assert min(seen) >= 1

    def test_the_same_seed_reproduces_the_study(self) -> None:
        runner = ParameterPerturbation(base=BASE, parameters=(RISK,), rerun=lambda c: _result(0.4))
        first = runner.report(_result(BASELINE_RETURN), _mc(seed=5))
        second = runner.report(_result(BASELINE_RETURN), _mc(seed=5))
        assert [d.digest for d in first.draws] == [d.digest for d in second.draws]


class TestInvalidDraws:
    def test_a_draw_the_schema_refuses_is_counted_not_resampled(self) -> None:
        """A baseline that cannot be nudged is sitting on a constraint boundary."""
        # macd_fast is 12 and macd_slow 26; a large jitter on the fast period
        # can push it past the slow one, which the schema forbids.
        runner = ParameterPerturbation(
            base=BASE, parameters=("indicators.macd_fast",), rerun=lambda c: _result(0.4)
        )
        report = runner.report(_result(BASELINE_RETURN), _mc(iterations=100, perturbation_pct=1.5))
        assert report.rejected_draws > 0
        assert len(report.draws) + report.rejected_draws == 100
        assert "invalid" in report.explain()

    def test_a_run_producing_no_metrics_is_rejected_not_scored_as_zero(self) -> None:
        bare = BacktestResult(
            spec=_spec(),
            manifest=_manifest(),
            status=BacktestStatus.COMPLETED,
            metrics=None,
            trades=(),
            equity_curve=(),
        )
        runner = ParameterPerturbation(base=BASE, parameters=(RISK,), rerun=lambda c: bare)
        report = runner.report(_result(BASELINE_RETURN), _mc(iterations=100))
        assert report.draws == ()
        assert report.rejected_draws == 100


class TestProtocolShape:
    def test_run_returns_a_monte_carlo_result(self) -> None:
        runner = ParameterPerturbation(base=BASE, parameters=(RISK,), rerun=lambda c: _result(0.3))
        outcome = runner.run(_result(BASELINE_RETURN), _mc())
        assert outcome.iterations_completed == 200
        assert outcome.return_percentiles[50] == pytest.approx(0.3)
        assert outcome.worst_case_drawdown(5) == pytest.approx(0.2)

    def test_a_resampling_method_is_refused(self) -> None:
        """Those belong to the other runner, and the names must not blur."""
        runner = ParameterPerturbation(base=BASE, parameters=(RISK,), rerun=lambda c: _result(0.3))
        with pytest.raises(ValueError, match="belong to ResamplingMonteCarlo"):
            runner.run(_result(BASELINE_RETURN), _mc(method="trade_sequence"))

    def test_no_usable_draws_yields_no_percentiles(self) -> None:
        bare = BacktestResult(
            spec=_spec(),
            manifest=_manifest(),
            status=BacktestStatus.COMPLETED,
            metrics=None,
            trades=(),
            equity_curve=(),
        )
        runner = ParameterPerturbation(base=BASE, parameters=(RISK,), rerun=lambda c: bare)
        outcome = runner.run(_result(BASELINE_RETURN), _mc())
        assert outcome.iterations_completed == 0
        assert outcome.return_percentiles == {}
