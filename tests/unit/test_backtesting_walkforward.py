"""Tests for the walk-forward harness.

The window arithmetic gets most of the attention here because it is where an
off-by-one silently leaks: a test window starting one session too early
overlaps its training window, and the resulting out-of-sample number is not
out-of-sample at all.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from tradeit.backtesting.base import (
    BacktestResult,
    BacktestSpec,
    BacktestStatus,
    PerformanceMetrics,
)
from tradeit.backtesting.walkforward import (
    ANCHORED,
    ROLLING,
    WalkForward,
    WalkForwardReport,
    WindowResult,
)
from tradeit.core.enums import ArtifactKind
from tradeit.core.models import OhlcvBar
from tradeit.portfolio.cycle import EntryCandidate
from tradeit.reproducibility.versioning import ArtifactVersion, RunManifest

UTC = dt.UTC
START = dt.date(2024, 1, 1)


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="wf",
        as_of=dt.datetime(2024, 12, 31, tzinfo=UTC),
        strategy_config=ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "b", {"r": 1}),
        data_snapshot=ArtifactVersion.of(ArtifactKind.DATA_SNAPSHOT, "d", {"n": 1}),
        feature_set=None,
        model=None,
        code_version="test",
        created_at=dt.datetime(2024, 12, 31, tzinfo=UTC),
    )


@dataclass
class _Sessions:
    """A calendar of consecutive days; no bars are needed for window arithmetic."""

    count: int

    def sessions(self, start: dt.date, end: dt.date) -> Sequence[dt.date]:
        days = [START + dt.timedelta(days=i) for i in range(self.count)]
        return [d for d in days if start <= d <= end]

    def bars(self, session_date: dt.date) -> Mapping[int, OhlcvBar]:
        return {}

    def candidates(self, session_date: dt.date) -> Sequence[EntryCandidate]:
        return []


@dataclass
class _RecordingEngine:
    """Returns a scripted return per span, and records what it was asked to run."""

    returns_by_label: dict[str, float] = field(default_factory=dict)
    calls: list[BacktestSpec] = field(default_factory=list)
    name: str = "recording"

    def run(self, spec: BacktestSpec) -> BacktestResult:
        self.calls.append(spec)
        label = "train" if "/train/" in spec.name else "test"
        total = self.returns_by_label.get(label, 0.0)
        return BacktestResult(
            spec=spec,
            manifest=_manifest(),
            status=BacktestStatus.COMPLETED,
            metrics=PerformanceMetrics(
                total_return_pct=total,
                cagr=total,
                max_drawdown_pct=0.1,
                max_drawdown_duration_sessions=1,
                sharpe=1.0,
                sortino=1.0,
                calmar=1.0,
                win_rate=0.5,
                profit_factor=1.5,
                expectancy_r=0.2,
                average_win_r=1.0,
                average_loss_r=-0.5,
                trade_count=40,
                exposure_pct=0.5,
                turnover=1.0,
            ),
            trades=(),
            equity_curve=((spec.start, Decimal(100000)), (spec.end, Decimal(110000))),
        )


def _spec(days: int = 100) -> BacktestSpec:
    return BacktestSpec(
        name="wf",
        start=START,
        end=START + dt.timedelta(days=days - 1),
        universe="u",
        initial_capital=Decimal(100000),
        strategy_config_digest="sc",
        cost_model="c",
        fill_model="f",
    )


def _harness(
    *,
    mode: str = ANCHORED,
    train: int = 40,
    test: int = 10,
    gap: int = 0,
    sessions: int = 100,
    engine: _RecordingEngine | None = None,
) -> WalkForward:
    return WalkForward(
        engine=engine or _RecordingEngine(),
        data=_Sessions(sessions),
        mode=mode,
        train_sessions=train,
        test_sessions=test,
        gap_sessions=gap,
    )


class TestConstruction:
    def test_an_unknown_mode_is_refused(self) -> None:
        with pytest.raises(ValueError, match="mode must be"):
            _harness(mode="sideways")

    def test_a_one_session_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            _harness(test=1)

    def test_a_negative_gap_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            _harness(gap=-1)


class TestWindowArithmetic:
    def test_the_test_window_never_overlaps_the_training_window(self) -> None:
        """WalkForwardWindow would refuse it, so this asserts we never build one."""
        for gap in (0, 1, 5):
            for window in _harness(gap=gap).windows(_spec()):
                assert window.test_start > window.train_end

    def test_the_gap_pushes_the_test_window_out(self) -> None:
        without = _harness(gap=0).windows(_spec())[0]
        with_gap = _harness(gap=5).windows(_spec())[0]
        assert (with_gap.test_start - without.test_start).days == 5
        assert with_gap.train_end == without.train_end

    def test_anchored_training_grows_from_a_fixed_origin(self) -> None:
        windows = _harness(mode=ANCHORED).windows(_spec())
        assert len({w.train_start for w in windows}) == 1
        assert windows[0].train_start == START
        assert windows[-1].train_end > windows[0].train_end

    def test_rolling_training_keeps_a_fixed_width(self) -> None:
        windows = _harness(mode=ROLLING).windows(_spec())
        widths = {(w.train_end - w.train_start).days for w in windows}
        assert len(widths) == 1
        assert len({w.train_start for w in windows}) == len(windows)

    def test_test_windows_tile_without_overlapping_each_other(self) -> None:
        windows = _harness().windows(_spec())
        for earlier, later in itertools.pairwise(windows):
            assert later.test_start > earlier.test_end

    def test_a_history_too_short_for_one_split_yields_nothing(self) -> None:
        """Not one truncated window measured on different terms."""
        assert _harness(train=40, test=10, sessions=30).windows(_spec(30)) == []

    def test_no_partial_window_is_emitted_at_the_end(self) -> None:
        windows = _harness(train=40, test=10, sessions=100).windows(_spec(100))
        widths = {(w.test_end - w.test_start).days for w in windows}
        assert len(widths) == 1


class TestReporting:
    def _report(self, *, in_sample: float, out_of_sample: float) -> WalkForwardReport:
        engine = _RecordingEngine(returns_by_label={"train": in_sample, "test": out_of_sample})
        return _harness(engine=engine).evaluate(_spec())

    def test_run_returns_only_the_out_of_sample_results(self) -> None:
        engine = _RecordingEngine(returns_by_label={"train": 0.30, "test": 0.04})
        harness = _harness(engine=engine)
        results = harness.run(_spec())
        assert len(results) == len(harness.windows(_spec()))
        assert all(r.metrics is not None and r.metrics.total_return_pct == 0.04 for r in results)

    def test_both_sides_of_every_window_are_run(self) -> None:
        engine = _RecordingEngine()
        harness = _harness(engine=engine)
        count = len(harness.windows(_spec()))
        harness.evaluate(_spec())
        assert len(engine.calls) == count * 2

    def test_each_side_is_named_so_the_two_cannot_be_confused(self) -> None:
        engine = _RecordingEngine()
        _harness(engine=engine).evaluate(_spec())
        assert any("/train/" in spec.name for spec in engine.calls)
        assert any("/test/" in spec.name for spec in engine.calls)

    def test_degradation_is_the_in_sample_advantage(self) -> None:
        report = self._report(in_sample=0.30, out_of_sample=0.04)
        assert report.mean_degradation == pytest.approx(0.26)

    def test_a_strategy_that_travels_shows_little_degradation(self) -> None:
        assert self._report(in_sample=0.06, out_of_sample=0.04).mean_degradation == pytest.approx(
            0.02
        )

    def test_consistency_counts_winning_windows_not_average_return(self) -> None:
        """A high average with low consistency is one lucky window."""
        report = self._report(in_sample=0.1, out_of_sample=0.05)
        assert report.consistency() == 1.0
        losing = self._report(in_sample=0.1, out_of_sample=-0.05)
        assert losing.consistency() == 0.0

    def test_degradation_is_none_when_a_side_produced_no_metrics(self) -> None:
        spec = _spec()
        engine = _RecordingEngine()
        result = engine.run(spec)
        bare = BacktestResult(
            spec=spec,
            manifest=_manifest(),
            status=BacktestStatus.COMPLETED,
            metrics=None,
            trades=(),
            equity_curve=(),
        )
        window = _harness().windows(spec)[0]
        assert WindowResult(window=window, in_sample=result, out_of_sample=bare).degradation is None

    def test_an_empty_report_reports_nothing_rather_than_zero(self) -> None:
        report = _harness(sessions=10).evaluate(_spec(10))
        assert report.results == ()
        assert report.mean_degradation is None
        assert report.consistency() is None
