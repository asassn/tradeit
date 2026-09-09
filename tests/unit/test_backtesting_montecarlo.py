"""Tests for the resampling Monte Carlo runner.

The one that carries the design is
``test_resampling_finds_a_worse_drawdown_than_the_observed_path``: if it fails,
the runner is reporting the history it was given rather than the distribution
around it, which is the entire reason to run one.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.backtesting.base import (
    BacktestResult,
    BacktestSpec,
    BacktestTrade,
    MonteCarloSpec,
)
from tradeit.backtesting.montecarlo import PERCENTILES, ResamplingMonteCarlo
from tradeit.core.enums import ArtifactKind, BacktestStatus
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest

UTC = dt.UTC
START = dt.date(2024, 1, 1)
RUNNER = ResamplingMonteCarlo(ruin_threshold=0.5)


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="mc",
        as_of=dt.datetime(2024, 12, 31, tzinfo=UTC),
        strategy_config=ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "b", {"r": 1}),
        data_snapshot=ArtifactVersion.of(ArtifactKind.DATA_SNAPSHOT, "d", {"n": 1}),
        feature_set=None,
        model=None,
        code_version="test",
        created_at=dt.datetime(2024, 12, 31, tzinfo=UTC),
    )


def _spec() -> BacktestSpec:
    return BacktestSpec(
        name="t",
        start=START,
        end=START + dt.timedelta(days=400),
        universe="u",
        initial_capital=Decimal(100000),
        strategy_config_digest="sc",
        cost_model="c",
        fill_model="f",
    )


def _trade(day_offset: int, net: str) -> BacktestTrade:
    entry = START + dt.timedelta(days=day_offset)
    return BacktestTrade(
        instrument_id=1,
        entry_date=entry,
        entry_price=Decimal(100),
        exit_date=entry + dt.timedelta(days=1),
        exit_price=Decimal(101),
        quantity=Decimal(10),
        side="long",
        exit_reason="target_reached",
        gross_pnl=Decimal(net),
        net_pnl=Decimal(net),
        costs=Decimal(0),
        return_pct=0.0,
        r_multiple=1.0,
        holding_sessions=1,
    )


def _result(
    trades: list[BacktestTrade],
    curve: list[tuple[dt.date, Decimal]],
) -> BacktestResult:
    return BacktestResult(
        spec=_spec(),
        manifest=_manifest(),
        status=BacktestStatus.COMPLETED,
        metrics=None,
        trades=tuple(trades),
        equity_curve=tuple(curve),
    )


def _flat_curve(days: int, equity: str = "100000") -> list[tuple[dt.date, Decimal]]:
    return [(START + dt.timedelta(days=i), Decimal(equity)) for i in range(days)]


def _mc(method: str = "trade_sequence", **overrides: object) -> MonteCarloSpec:
    payload: dict[str, object] = {"method": method, "iterations": 500, "seed": 7}
    payload.update(overrides)
    return MonteCarloSpec(**payload)  # type: ignore[arg-type]


class TestTheDistributionIsNotTheHistory:
    def test_resampling_finds_a_worse_drawdown_than_the_observed_path(self) -> None:
        """The observed ordering was luck; a worse one was available."""
        # Alternating wins and losses: the realised path never draws down much,
        # but a run of the losses does.
        trades = [_trade(i, "5000" if i % 2 == 0 else "-4000") for i in range(40)]
        result = _result(trades, _flat_curve(40))
        outcome = RUNNER.run(result, _mc())
        observed_worst = 0.04  # roughly one 4% loss at a time
        assert outcome.drawdown_percentiles[95] > observed_worst * 2

    def test_the_tails_bracket_the_median(self) -> None:
        trades = [_trade(i, "5000" if i % 2 == 0 else "-4000") for i in range(40)]
        outcome = RUNNER.run(_result(trades, _flat_curve(40)), _mc())
        assert (
            outcome.return_percentiles[5]
            <= outcome.return_percentiles[50]
            <= outcome.return_percentiles[95]
        )
        assert set(outcome.return_percentiles) == set(PERCENTILES)

    def test_a_strategy_that_only_wins_never_draws_down(self) -> None:
        trades = [_trade(i, "1000") for i in range(40)]
        outcome = RUNNER.run(_result(trades, _flat_curve(40)), _mc())
        assert outcome.drawdown_percentiles[95] == 0.0
        assert outcome.ruin_probability == 0.0


class TestFractionsNotDollars:
    def test_pnl_is_scaled_by_the_equity_it_was_risked_against(self) -> None:
        """Replaying raw dollars turns a percentage strategy into a fixed-dollar one."""
        trades = [_trade(i, "1000") for i in range(10)]
        small = RUNNER.run(_result(trades, _flat_curve(10, "10000")), _mc())
        large = RUNNER.run(_result(trades, _flat_curve(10, "1000000")), _mc())
        assert small.return_percentiles[50] > large.return_percentiles[50]

    def test_a_trade_with_no_equity_mark_is_skipped_not_defaulted(self) -> None:
        """The fallback would make later trades look enormous."""
        trades = [_trade(0, "1000"), _trade(999, "1000")]
        outcome = RUNNER.run(_result(trades, _flat_curve(10)), _mc())
        # Only one usable fraction remains, which is too few to resample.
        assert outcome.iterations_completed == 0


class TestMethods:
    def test_bootstrap_returns_uses_the_curve_not_the_trades(self) -> None:
        curve = [(START + dt.timedelta(days=i), Decimal(100000 + i * 1000)) for i in range(30)]
        outcome = RUNNER.run(_result([], curve), _mc("bootstrap_returns"))
        assert outcome.iterations_completed == 500
        assert outcome.return_percentiles[50] > 0

    def test_trade_sequence_on_a_run_with_no_trades_yields_nothing(self) -> None:
        outcome = RUNNER.run(_result([], _flat_curve(30)), _mc())
        assert outcome.iterations_completed == 0
        assert outcome.return_percentiles == {}

    def test_parameter_perturbation_is_refused_rather_than_faked(self) -> None:
        """Returning a trade-resampled distribution would answer another question."""
        trades = [_trade(i, "1000") for i in range(10)]
        with pytest.raises(ValueError, match="cannot be run from a finished result"):
            RUNNER.run(_result(trades, _flat_curve(10)), _mc("parameter_perturbation"))


class TestDeterminismAndShape:
    def test_the_same_seed_gives_the_same_distribution(self) -> None:
        trades = [_trade(i, "5000" if i % 3 else "-4000") for i in range(30)]
        result = _result(trades, _flat_curve(30))
        first = RUNNER.run(result, _mc(seed=42))
        second = RUNNER.run(result, _mc(seed=42))
        assert first.return_percentiles == second.return_percentiles

    def test_a_different_seed_gives_a_different_one(self) -> None:
        trades = [_trade(i, "5000" if i % 3 else "-4000") for i in range(30)]
        result = _result(trades, _flat_curve(30))
        assert (
            RUNNER.run(result, _mc(seed=1)).return_percentiles
            != RUNNER.run(result, _mc(seed=2)).return_percentiles
        )

    def test_resampling_is_with_replacement_not_a_permutation(self) -> None:
        """A permutation has the same total return every time."""
        trades = [_trade(i, "5000" if i % 2 == 0 else "-4000") for i in range(40)]
        outcome = RUNNER.run(_result(trades, _flat_curve(40)), _mc())
        assert outcome.return_percentiles[5] != outcome.return_percentiles[95]

    def test_block_resampling_preserves_streaks(self) -> None:
        """Independent draws break up clustered losses and flatter the tail."""
        trades = [_trade(i, "5000" if i % 2 == 0 else "-4000") for i in range(40)]
        result = _result(trades, _flat_curve(40))
        independent = RUNNER.run(result, _mc(block_size=None))
        blocked = RUNNER.run(result, _mc(block_size=8))
        assert blocked.drawdown_percentiles[95] != independent.drawdown_percentiles[95]

    def test_a_path_is_the_same_length_as_the_original(self) -> None:
        trades = [_trade(i, "1000") for i in range(7)]
        outcome = RUNNER.run(_result(trades, _flat_curve(7)), _mc(block_size=5))
        # Seven 0.01 gains compounded, whatever the ordering.
        assert outcome.return_percentiles[50] == pytest.approx(1.01**7 - 1, rel=1e-9)


class TestRuin:
    def test_ruin_is_counted_against_the_configured_threshold(self) -> None:
        trades = [_trade(i, "-30000") for i in range(10)]
        outcome = RUNNER.run(_result(trades, _flat_curve(10)), _mc())
        assert outcome.ruin_probability == 1.0

    def test_a_gentler_threshold_reports_less_ruin(self) -> None:
        trades = [_trade(i, "5000" if i % 2 == 0 else "-9000") for i in range(30)]
        result = _result(trades, _flat_curve(30))
        strict = ResamplingMonteCarlo(ruin_threshold=0.9).run(result, _mc())
        lenient = ResamplingMonteCarlo(ruin_threshold=0.1).run(result, _mc())
        assert strict.ruin_probability >= lenient.ruin_probability

    def test_worst_case_drawdown_reads_the_percentile_it_names(self) -> None:
        trades = [_trade(i, "5000" if i % 2 == 0 else "-4000") for i in range(40)]
        outcome = RUNNER.run(_result(trades, _flat_curve(40)), _mc())
        assert outcome.worst_case_drawdown(5) == outcome.drawdown_percentiles[5]
