"""Is this a real effect, or a peak in a noisy surface?

``MonteCarloSpec`` describes ``parameter_perturbation`` as jittering the
strategy's parameters to ask exactly that, and states the answer it is looking
for: *a strategy whose performance collapses under a 10% parameter change is
fitted to noise.* :mod:`tradeit.backtesting.montecarlo` refuses the method
because it is handed a finished result and cannot re-run anything. This module
is the re-runner.

Every parameter moves at once
-----------------------------

The obvious implementation jitters one parameter at a time and reports a
sensitivity per parameter. It is easier to read and it **systematically
understates fragility**, because a strategy can be robust to every parameter
alone and fall apart when two move together — and in a real deployment they are
all slightly wrong at once. One-at-a-time analysis answers "which knob matters
most", which is a useful question and a different one.

Relative, not absolute
----------------------

``perturbation_pct`` is a fraction of each parameter's own value. A ±10%
absolute jitter would be meaningless applied to ``risk_per_trade_pct`` of 0.005
and catastrophic applied to a 14-period ATR. Integer parameters round to the
nearest whole number, which means a small jitter on a small integer can produce
no change at all — that is honest rather than a bug, and the reported draw
count is of *distinct configurations actually run*.

An invalid draw is a finding, not a nuisance
---------------------------------------------

Jitter can produce a configuration the strategy schema rejects — a slow MACD
period that no longer exceeds the fast one, a position cap above the gross
exposure limit. Those draws are counted and reported rather than silently
resampled. A parameter set that cannot be nudged without becoming invalid is
sitting on a constraint boundary, and that is worth knowing before it is worth
hiding.

What "survives" means
---------------------

:attr:`SensitivityReport.fragility` is the share of the reported edge that
disappears at the median perturbed draw. Zero means the jittered strategies did
as well as the tuned one; 1.0 means the entire return was an artefact of the
exact parameters chosen. **Above 1.0 is possible and is the loudest signal
here**: the median jittered strategy lost money while the tuned one made it.

The threshold that separates surviving from fitted is a judgement, so it has no
default and must be supplied.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tradeit.backtesting.base import (
    BacktestResult,
    MonteCarloResult,
    MonteCarloSpec,
)
from tradeit.backtesting.montecarlo import PERCENTILES, percentile_of
from tradeit.errors import ConfigError
from tradeit.strategy.config import StrategyConfig

__all__ = ["Draw", "ParameterPerturbation", "SensitivityReport"]


@dataclass(frozen=True, slots=True)
class Draw:
    """One jittered configuration and what it produced."""

    digest: str
    values: dict[str, float]
    total_return_pct: float
    max_drawdown_pct: float


@dataclass(frozen=True, slots=True)
class SensitivityReport:
    """Every draw, the rejects, and how much of the edge survived."""

    baseline_return_pct: float
    draws: tuple[Draw, ...]
    rejected_draws: int
    parameters: tuple[str, ...]

    @property
    def median_return_pct(self) -> float | None:
        if not self.draws:
            return None
        return percentile_of(sorted(draw.total_return_pct for draw in self.draws), 50)

    @property
    def fragility(self) -> float | None:
        """Share of the reported edge lost at the median perturbed draw.

        ``None`` when the baseline made nothing, because a share of zero is
        undefined and reporting 0.0 would read as "perfectly robust" for a
        strategy that never had an edge to lose.
        """
        median = self.median_return_pct
        if median is None or self.baseline_return_pct == 0:
            return None
        return (self.baseline_return_pct - median) / abs(self.baseline_return_pct)

    def survives(self, max_fragility: float) -> bool | None:
        """Whether the edge held up. ``None`` when fragility is undefined."""
        fragility = self.fragility
        if fragility is None:
            return None
        return fragility <= max_fragility

    def explain(self) -> str:
        fragility = self.fragility
        if fragility is None:
            return (
                f"{len(self.draws)} draws over {len(self.parameters)} parameters; "
                "the baseline made nothing, so there is no edge to test"
            )
        return (
            f"{len(self.draws)} draws over {len(self.parameters)} parameters lost "
            f"{fragility:.0%} of the baseline return at the median"
            + (f"; {self.rejected_draws} draws were invalid" if self.rejected_draws else "")
        )


def read_path(config: StrategyConfig, path: str) -> float:
    """Read a dotted parameter path, e.g. ``sizing.risk_per_trade_pct``."""
    current: Any = config
    for part in path.split("."):
        current = getattr(current, part)
    if not isinstance(current, int | float) or isinstance(current, bool):
        raise ValueError(
            f"{path} is {type(current).__name__}, which cannot be perturbed; only "
            "numeric parameters can be jittered"
        )
    return float(current)


def _write_path(payload: dict[str, Any], path: str, value: float) -> None:
    parts = path.split(".")
    cursor: Any = payload
    for part in parts[:-1]:
        cursor = cursor[part]
    cursor[parts[-1]] = value


@dataclass(frozen=True, slots=True)
class ParameterPerturbation:
    """Re-runs the backtest under jittered parameters.

    ``rerun`` is supplied by the caller because only the caller knows how to
    build an engine for a configuration — the data source, the cost model and
    the manifest all belong to it. Injecting it keeps this module about the
    jitter and the arithmetic.

    ``parameters`` are dotted paths and have no default: which knobs matter is a
    strategy question, and a default list would perturb whatever this module
    happened to think was important.
    """

    base: StrategyConfig
    parameters: tuple[str, ...]
    rerun: Callable[[StrategyConfig], BacktestResult]
    name: str = "parameter_perturbation"

    def __post_init__(self) -> None:
        if not self.parameters:
            raise ValueError("no parameters to perturb; name at least one dotted path")
        for path in self.parameters:
            read_path(self.base, path)  # raises on a bad path, now rather than mid-run

    @property
    def config_parameters(self) -> dict[str, object]:
        return {"parameters": list(self.parameters), "base_digest": self.base.digest}

    def run(self, result: BacktestResult, spec: MonteCarloSpec) -> MonteCarloResult:
        """The protocol-shaped answer: a distribution over perturbed runs."""
        if spec.method != "parameter_perturbation":
            raise ValueError(
                f"{self.name} runs parameter_perturbation, not {spec.method!r}; the "
                "resampling methods belong to ResamplingMonteCarlo"
            )
        report = self.report(result, spec)
        if not report.draws:
            return MonteCarloResult(spec=spec, iterations_completed=0)
        returns = sorted(draw.total_return_pct for draw in report.draws)
        drawdowns = sorted(draw.max_drawdown_pct for draw in report.draws)
        return MonteCarloResult(
            spec=spec,
            return_percentiles={p: percentile_of(returns, p) for p in PERCENTILES},
            drawdown_percentiles={p: percentile_of(drawdowns, p) for p in PERCENTILES},
            ruin_probability=sum(1 for value in returns if value <= -1.0) / len(returns),
            iterations_completed=len(report.draws),
        )

    def report(self, result: BacktestResult, spec: MonteCarloSpec) -> SensitivityReport:
        """The richer answer, including what fraction of the edge survived."""
        baseline = result.metrics.total_return_pct if result.metrics is not None else 0.0
        rng = random.Random(spec.seed)
        draws: list[Draw] = []
        rejected = 0
        for _ in range(spec.iterations):
            candidate = self._jitter(rng, spec.perturbation_pct)
            if candidate is None:
                rejected += 1
                continue
            config, values = candidate
            outcome = self.rerun(config)
            if outcome.metrics is None:
                rejected += 1
                continue
            draws.append(
                Draw(
                    digest=config.digest,
                    values=values,
                    total_return_pct=outcome.metrics.total_return_pct,
                    max_drawdown_pct=outcome.metrics.max_drawdown_pct,
                )
            )
        return SensitivityReport(
            baseline_return_pct=baseline,
            draws=tuple(draws),
            rejected_draws=rejected,
            parameters=self.parameters,
        )

    def _jitter(
        self, rng: random.Random, pct: float
    ) -> tuple[StrategyConfig, dict[str, float]] | None:
        """One draw, or ``None`` when the schema rejects it.

        Every named parameter moves in the same draw. See the module docstring
        for why one-at-a-time is not what this measures.
        """
        payload = self.base.model_dump()
        values: dict[str, float] = {}
        for path in self.parameters:
            original = read_path(self.base, path)
            moved = original * (1 + rng.uniform(-pct, pct))
            if isinstance(_original_value(self.base, path), int):
                moved = float(max(1, round(moved)))
            _write_path(payload, path, moved)
            values[path] = moved
        try:
            return StrategyConfig(**payload), values
        except (ConfigError, ValueError):
            # A draw the schema refuses. Counted by the caller, never resampled:
            # silently redrawing hides that the baseline sits on a boundary.
            return None


def _original_value(config: StrategyConfig, path: str) -> Any:
    current: Any = config
    for part in path.split("."):
        current = getattr(current, part)
    return current
