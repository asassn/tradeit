"""Tests for trial counting and the multiple-testing hurdle.

``test_the_formula_agrees_with_simulation`` is the one that makes the rest
meaningful: the hurdle is an asymptotic approximation, and an approximation
nobody checked is a constant somebody guessed.
"""

from __future__ import annotations

import datetime as dt
import random
import statistics
from decimal import Decimal

import pytest

from tradeit.backtesting.base import (
    BacktestResult,
    BacktestSpec,
    BacktestStatus,
    PerformanceMetrics,
)
from tradeit.backtesting.overfitting import (
    TrialLedger,
    TrialVerdict,
    expected_max_of_normals,
    sharpe_hurdle,
    trial_key,
)
from tradeit.core.enums import ArtifactKind
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest

UTC = dt.UTC
NOW = dt.datetime(2024, 6, 1, tzinfo=UTC)


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="of",
        as_of=NOW,
        strategy_config=ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "b", {"r": 1}),
        data_snapshot=ArtifactVersion.of(ArtifactKind.DATA_SNAPSHOT, "d", {"n": 1}),
        feature_set=None,
        model=None,
        code_version="test",
        created_at=NOW,
    )


def _spec(**overrides: object) -> BacktestSpec:
    payload: dict[str, object] = {
        "name": "run",
        "start": dt.date(2014, 1, 1),
        "end": dt.date(2024, 1, 1),  # ten years
        "universe": "sp500",
        "initial_capital": Decimal(100000),
        "strategy_config_digest": "cfg-a",
        "cost_model": "participation",
        "fill_model": "bar",
    }
    payload.update(overrides)
    return BacktestSpec(**payload)  # type: ignore[arg-type]


def _result(spec: BacktestSpec, sharpe: float | None) -> BacktestResult:
    metrics = (
        None
        if sharpe is None
        else PerformanceMetrics(
            total_return_pct=1.0,
            cagr=0.1,
            max_drawdown_pct=0.2,
            max_drawdown_duration_sessions=30,
            sharpe=sharpe,
            sortino=sharpe,
            calmar=0.5,
            win_rate=0.5,
            profit_factor=1.4,
            expectancy_r=0.2,
            average_win_r=1.0,
            average_loss_r=-0.6,
            trade_count=120,
            exposure_pct=0.6,
            turnover=2.0,
        )
    )
    return BacktestResult(
        spec=spec,
        manifest=_manifest(),
        status=BacktestStatus.COMPLETED,
        metrics=metrics,
        trades=(),
        equity_curve=((spec.start, Decimal(100000)), (spec.end, Decimal(200000))),
    )


class TestTheHurdleMaths:
    @pytest.mark.parametrize("trials", [5, 20, 200])
    def test_the_formula_agrees_with_simulation(self, trials: int) -> None:
        """An approximation nobody checked is a constant somebody guessed."""
        rng = random.Random(20240101)
        simulated = statistics.fmean(
            max(rng.gauss(0, 1) for _ in range(trials)) for _ in range(4000)
        )
        assert expected_max_of_normals(trials) == pytest.approx(simulated, abs=0.06)

    def test_the_hurdle_rises_with_the_number_of_trials(self) -> None:
        hurdles = [sharpe_hurdle(n, 10.0) for n in (2, 10, 100, 1000)]
        assert all(h is not None for h in hurdles)
        assert hurdles == sorted(hurdles)  # type: ignore[type-var]

    def test_the_hurdle_falls_with_more_data(self) -> None:
        """More years is a better-estimated Sharpe, so luck clears less easily."""
        short = sharpe_hurdle(100, 2.0)
        long = sharpe_hurdle(100, 20.0)
        assert short is not None and long is not None
        assert short > long

    def test_a_single_trial_carries_no_multiple_testing_penalty(self) -> None:
        assert sharpe_hurdle(1, 10.0) == 0.0
        assert expected_max_of_normals(1) == 0.0

    def test_too_short_a_period_is_none_not_zero(self) -> None:
        """Zero would say 'any positive Sharpe clears', the opposite of the truth."""
        assert sharpe_hurdle(100, 0.0) is None

    def test_two_hundred_trials_over_ten_years_is_a_real_hurdle(self) -> None:
        hurdle = sharpe_hurdle(200, 10.0)
        assert hurdle is not None
        assert hurdle == pytest.approx(0.875, abs=0.01)


class TestTrialIdentity:
    def test_the_same_specification_is_one_trial_however_often_it_runs(self) -> None:
        ledger = TrialLedger(hypothesis="h")
        for _ in range(5):
            ledger.record(_spec(), at=NOW)
        assert ledger.count == 1

    def test_changing_a_parameter_is_a_new_trial(self) -> None:
        """Which is what searching is."""
        ledger = TrialLedger(hypothesis="h")
        ledger.record(_spec(strategy_config_digest="cfg-a"), at=NOW)
        ledger.record(_spec(strategy_config_digest="cfg-b"), at=NOW)
        assert ledger.count == 2

    def test_renaming_a_run_is_not_a_new_trial(self) -> None:
        """Otherwise a search could be hidden behind labels."""
        assert trial_key(_spec(name="attempt-1")) == trial_key(_spec(name="final-version"))

    def test_changing_the_period_is_a_new_trial(self) -> None:
        ledger = TrialLedger(hypothesis="h")
        ledger.record(_spec(), at=NOW)
        ledger.record(_spec(end=dt.date(2023, 1, 1)), at=NOW)
        assert ledger.count == 2

    def test_a_rerun_keeps_the_first_timestamp_and_updates_the_outcome(self) -> None:
        ledger = TrialLedger(hypothesis="h")
        first = ledger.record(_spec(), at=NOW, sharpe=1.0)
        later = ledger.record(_spec(), at=NOW + dt.timedelta(days=1), sharpe=1.2)
        assert later.recorded_at == first.recorded_at
        assert later.sharpe == 1.2

    def test_a_recorded_sharpe_of_zero_is_not_discarded(self) -> None:
        """`or` would treat it as absent, which is how a falsy zero disappears."""
        ledger = TrialLedger(hypothesis="h")
        ledger.record(_spec(), at=NOW, sharpe=0.0)
        assert ledger.record(_spec(), at=NOW).sharpe == 0.0


class TestAssessment:
    def test_an_unrecorded_run_is_unresolved_not_a_single_trial(self) -> None:
        """A ledger nobody wrote to would otherwise clear everything."""
        ledger = TrialLedger(hypothesis="h")
        verdict, reason = ledger.assess(_result(_spec(), 2.0))
        assert verdict is TrialVerdict.UNRESOLVED
        assert "unrecorded search cannot be judged" in reason

    def test_a_strong_result_from_one_trial_clears(self) -> None:
        ledger = TrialLedger(hypothesis="h")
        spec = _spec()
        ledger.record(spec, at=NOW)
        verdict, reason = ledger.assess(_result(spec, 1.4))
        assert verdict is TrialVerdict.CLEARS_HURDLE
        assert "1 trial" in reason

    def test_the_same_result_fails_after_two_hundred_trials(self) -> None:
        """The number the search produced, not the number it reported."""
        ledger = TrialLedger(hypothesis="h")
        spec = _spec()
        ledger.record(spec, at=NOW)
        for i in range(199):
            ledger.record(_spec(strategy_config_digest=f"cfg-{i}"), at=NOW)
        assert ledger.count == 200
        verdict, reason = ledger.assess(_result(spec, 0.6))
        assert verdict is TrialVerdict.BELOW_HURDLE
        assert "no-skill search of this size" in reason

    def test_a_genuinely_strong_result_survives_a_large_search(self) -> None:
        ledger = TrialLedger(hypothesis="h")
        spec = _spec()
        ledger.record(spec, at=NOW)
        for i in range(199):
            ledger.record(_spec(strategy_config_digest=f"cfg-{i}"), at=NOW)
        verdict, _ = ledger.assess(_result(spec, 1.8))
        assert verdict is TrialVerdict.CLEARS_HURDLE

    def test_the_reason_always_names_the_trial_count(self) -> None:
        """A Sharpe quoted without it is the omission this module prevents."""
        ledger = TrialLedger(hypothesis="h")
        spec = _spec()
        ledger.record(spec, at=NOW)
        ledger.record(_spec(strategy_config_digest="other"), at=NOW)
        for sharpe in (0.1, 3.0):
            _, reason = ledger.assess(_result(spec, sharpe))
            assert "2 trials" in reason

    def test_a_result_without_a_sharpe_is_not_applicable(self) -> None:
        ledger = TrialLedger(hypothesis="h")
        spec = _spec()
        ledger.record(spec, at=NOW)
        verdict, reason = ledger.assess(_result(spec, None))
        assert verdict is TrialVerdict.NOT_APPLICABLE
        assert "cannot be applied" in reason

    def test_record_all_logs_a_whole_search_at_once(self) -> None:
        ledger = TrialLedger(hypothesis="h")
        ledger.record_all((_spec(strategy_config_digest=f"cfg-{i}") for i in range(25)), at=NOW)
        assert ledger.count == 25
        assert ledger.hurdle(10.0) == pytest.approx(sharpe_hurdle(25, 10.0))
