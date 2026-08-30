"""Interfaces for backtesting, walk-forward validation, and Monte Carlo.

The architectural commitment that matters more than anything in this module:
**the backtester does not reimplement the strategy.** It advances an
``AsOfClock`` through history and calls the same screening, scoring, sizing,
risk and execution components that run live, with a simulated broker
substituted at the edge. A backtester containing its own copy of the entry logic
is testing a program that resembles the live system rather than the live system
itself, and the two drift apart silently.

What the backtester adds is the loop, the simulated venue, and the measurement.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradeit.core.enums import BacktestStatus
from tradeit.reproducibility.versioning import RunManifest


@dataclass(frozen=True, slots=True)
class BacktestSpec:
    """The complete definition of a backtest run.

    Hashable via the manifest, so an identical specification is recognisable as
    a re-run rather than a new experiment. That matters for the discipline this
    module exists to enforce: counting how many variations were tried is the
    only defence against reporting the best of two hundred as if it were the
    first of one.
    """

    name: str
    start: dt.date
    end: dt.date
    universe: str
    initial_capital: Decimal
    strategy_config_digest: str
    cost_model: str
    fill_model: str
    benchmark_instrument_id: int | None = None
    warmup_sessions: int = 250

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("backtest end must be after start")
        if self.initial_capital <= 0:
            raise ValueError("initial capital must be positive")

    @property
    def effective_start(self) -> dt.date:
        """Trading starts only after indicators are warm.

        The warm-up window is part of the specification rather than an
        implementation detail, because a backtest that begins trading on day one
        with cold indicators is measuring something other than the strategy.
        """
        return self.start


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    """One completed round trip, with enough context to analyse it.

    ``mae`` and ``mfe`` — maximum adverse and favourable excursion — are stored
    because they answer questions the P&L cannot: whether stops were too tight,
    whether winners were exited too early, and whether a profitable strategy was
    profitable by luck of exit timing.
    """

    instrument_id: int
    entry_date: dt.date
    entry_price: Decimal
    exit_date: dt.date
    exit_price: Decimal
    quantity: Decimal
    side: str
    exit_reason: str
    gross_pnl: Decimal
    net_pnl: Decimal
    costs: Decimal
    return_pct: float
    r_multiple: float | None
    holding_sessions: int
    mae: Decimal | None = None
    mfe: Decimal | None = None
    score_at_entry: float | None = None
    sector: str | None = None
    regime_at_entry: str | None = None


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Summary statistics for a backtest or a live period.

    Percentage and risk-adjusted measures lead, per the brief's requirement that
    the system never prioritise raw dollar profit over risk-adjusted percentage
    return. Total dollars are recorded but are not the headline.

    ``trade_count`` sits alongside the ratios deliberately: a Sharpe ratio
    computed from nineteen trades is a number, not evidence, and presenting it
    without its sample size invites exactly that mistake.
    """

    total_return_pct: float
    cagr: float
    max_drawdown_pct: float
    max_drawdown_duration_sessions: int
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    win_rate: float
    profit_factor: float | None
    expectancy_r: float | None
    average_win_r: float | None
    average_loss_r: float | None
    trade_count: int
    exposure_pct: float
    turnover: float
    benchmark_return_pct: float | None = None

    @property
    def statistically_meaningful(self) -> bool:
        """Whether there are enough trades to interpret these ratios at all.

        Thirty is a low bar, deliberately: below it the numbers should not be
        quoted, above it they should still be treated cautiously.
        """
        return self.trade_count >= 30


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Everything one backtest produced."""

    spec: BacktestSpec
    manifest: RunManifest
    status: BacktestStatus
    metrics: PerformanceMetrics | None
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[tuple[dt.date, Decimal], ...]
    warnings: tuple[str, ...] = ()
    data_caveats: tuple[str, ...] = ()

    @property
    def trustworthy(self) -> bool:
        """Whether this result may be quoted without qualification.

        False when the underlying data was not backtest-grade or the trade
        count is too small. The property exists so that reports cannot present
        a caveated result as a clean one by omission.
        """
        return (
            self.status is BacktestStatus.COMPLETED
            and not self.data_caveats
            and self.metrics is not None
            and self.metrics.statistically_meaningful
        )


@runtime_checkable
class BacktestEngine(Protocol):
    """Runs a specification and produces a result.

    Implementations advance a clock session by session, and at each step do
    exactly what the live daily job does. The simulated broker is injected;
    nothing else differs.
    """

    name: str

    def run(self, spec: BacktestSpec) -> BacktestResult: ...


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """One in-sample/out-of-sample split.

    Anchored (expanding in-sample) or rolling (fixed-width) is a parameter of
    the harness. Both are supported because they answer different questions:
    anchored asks whether the strategy still works given everything known;
    rolling asks whether it works given only the recent past.
    """

    train_start: dt.date
    train_end: dt.date
    test_start: dt.date
    test_end: dt.date

    def __post_init__(self) -> None:
        if self.test_start <= self.train_end:
            raise ValueError(
                "out-of-sample window overlaps in-sample; the gap between them is "
                "what makes the out-of-sample result mean anything"
            )


@runtime_checkable
class WalkForwardHarness(Protocol):
    """Fits on in-sample windows and measures on out-of-sample ones.

    The reported metric is always out-of-sample. In-sample results are retained
    for diagnosis — a large in-sample/out-of-sample gap is the signature of
    overfitting and is more informative than either number alone.
    """

    def windows(self, spec: BacktestSpec) -> list[WalkForwardWindow]: ...

    def run(self, spec: BacktestSpec) -> list[BacktestResult]: ...


@dataclass(frozen=True, slots=True)
class MonteCarloSpec:
    """Configuration for a Monte Carlo study.

    Two distinct methods, answering two distinct questions:

    ``trade_sequence``
        Resample the observed trades. Asks: given this edge, how bad could the
        path have been? Reveals that a smooth equity curve was partly luck of
        ordering.
    ``parameter_perturbation``
        Jitter the strategy parameters. Asks: is this a real effect or a peak in
        a noisy surface? A strategy whose performance collapses under a 10%
        parameter change is fitted to noise.
    """

    method: str
    iterations: int = 1000
    seed: int = 20240101
    block_size: int | None = None
    perturbation_pct: float = 0.10

    def __post_init__(self) -> None:
        if self.method not in ("trade_sequence", "parameter_perturbation", "bootstrap_returns"):
            raise ValueError(f"unknown Monte Carlo method {self.method!r}")
        if self.iterations < 100:
            raise ValueError("fewer than 100 iterations produces percentiles nobody should quote")


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """Distributional outcomes rather than a point estimate.

    Percentiles are the deliverable. The 5th percentile of maximum drawdown is
    the number that should size a live position, not the drawdown that happened
    to occur in one historical ordering.
    """

    spec: MonteCarloSpec
    return_percentiles: dict[int, float] = field(default_factory=dict)
    drawdown_percentiles: dict[int, float] = field(default_factory=dict)
    ruin_probability: float = 0.0
    iterations_completed: int = 0

    def worst_case_drawdown(self, percentile: int = 5) -> float | None:
        return self.drawdown_percentiles.get(percentile)


@runtime_checkable
class MonteCarloRunner(Protocol):
    def run(self, result: BacktestResult, spec: MonteCarloSpec) -> MonteCarloResult: ...


@runtime_checkable
class PerformanceAnalyzer(Protocol):
    """Computes metrics and attributes them to sources.

    Attribution by sector, factor and regime is what distinguishes "this worked"
    from "this worked because of three positions in one sector during one
    six-month period".
    """

    def compute(
        self,
        trades: Sequence[BacktestTrade],
        equity_curve: Sequence[tuple[dt.date, Decimal]],
        benchmark: Sequence[tuple[dt.date, Decimal]] | None = None,
    ) -> PerformanceMetrics: ...

    def attribute(self, trades: Sequence[BacktestTrade], by: str) -> dict[str, float]: ...
