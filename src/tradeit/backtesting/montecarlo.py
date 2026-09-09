"""Monte Carlo: what else could have happened, given the same edge.

A backtest reports one path. That path is a single draw from a distribution,
and the drawdown it happened to contain is an anecdote — the ordering of wins
and losses was luck, and a different ordering of the *same trades* produces a
different worst moment. ``MonteCarloResult`` states the consequence: the 5th
percentile of maximum drawdown is the number that should size a live position.

Two methods, two different questions
------------------------------------

``trade_sequence``
    Resample the observed trades. *Given this edge, how bad could the path have
    been?* A smooth equity curve whose 5th-percentile path is catastrophic was
    smooth by luck of ordering.
``bootstrap_returns``
    Resample the curve's periodic returns instead. Asks the same question of
    the whole account rather than of the trades, so it includes the stretches
    with nothing open — a strategy that is flat 80% of the time looks very
    different under this method, correctly.

``parameter_perturbation`` is declared by ``MonteCarloSpec`` and **is not
implemented here**, because it cannot be: it requires re-running the backtest
under jittered parameters, and this runner is handed a finished result. Asking
for it raises rather than silently returning a distribution built by the wrong
method, which would answer a question nobody asked.

Trades are resampled as fractions of the equity they were risked against
--------------------------------------------------------------------------

Not as raw dollars. A $500 profit means something different on a $50,000
account than on a $500,000 one, and replaying dollar P&L over a compounding
path silently converts a percentage strategy into a fixed-dollar one — which
flatters the late trades and understates the early ones. Each trade's P&L is
divided by the equity recorded on its entry date, and the resampled path
compounds those fractions.

**Block resampling is available and is not the default.** Drawing trades
independently destroys any streakiness in the sequence, which makes the
distribution too kind if losses genuinely cluster. ``block_size`` draws
contiguous runs instead and preserves it.

**Ruin has no default threshold.** What counts as ruin is a business decision —
for some accounts it is a total loss, for most it is the drawdown after which
nobody would keep trading the strategy — and a default here would put a number
into a headline nobody chose.
"""

from __future__ import annotations

import datetime as dt
import itertools
import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradeit.backtesting.base import (
    BacktestResult,
    BacktestTrade,
    MonteCarloResult,
    MonteCarloSpec,
)

__all__ = ["PERCENTILES", "ResamplingMonteCarlo"]

#: Reported percentiles. The tails lead, because the middle of the distribution
#: is the part a single backtest already showed you.
PERCENTILES = (5, 10, 25, 50, 75, 90, 95)


def _percentile(values: Sequence[float], percentile: int) -> float:
    """Linear-interpolated percentile of an already-sorted sequence."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile / 100
    low = int(position)
    high = min(low + 1, len(values) - 1)
    weight = position - low
    return values[low] * (1 - weight) + values[high] * weight


def _path_statistics(fractions: Sequence[float]) -> tuple[float, float]:
    """Total return and maximum drawdown of a compounded fractional path."""
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for fraction in fractions:
        equity *= 1 + fraction
        if equity <= 0:
            return -1.0, 1.0
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return equity - 1.0, worst


@dataclass(frozen=True, slots=True)
class ResamplingMonteCarlo:
    """Bootstraps a finished result into a distribution of outcomes.

    ``ruin_threshold`` is the fraction of starting equity at or below which a
    path counts as ruined: 0.5 means "lost half the account".
    """

    ruin_threshold: float
    name: str = "resampling"

    @property
    def parameters(self) -> dict[str, object]:
        return {"ruin_threshold": self.ruin_threshold}

    def run(self, result: BacktestResult, spec: MonteCarloSpec) -> MonteCarloResult:
        if spec.method == "parameter_perturbation":
            raise ValueError(
                "parameter_perturbation cannot be run from a finished result; it "
                "requires re-running the backtest under jittered parameters, which "
                "this runner has no way to do. Returning a trade-resampled "
                "distribution instead would answer a different question under the "
                "same name."
            )
        fractions = (
            self._trade_fractions(result)
            if spec.method == "trade_sequence"
            else self._return_fractions(result)
        )
        if len(fractions) < 2:
            # Percentiles of one observation are that observation repeated.
            return MonteCarloResult(spec=spec, iterations_completed=0)

        rng = random.Random(spec.seed)
        returns: list[float] = []
        drawdowns: list[float] = []
        ruined = 0
        for _ in range(spec.iterations):
            path = self._resample(fractions, rng, spec.block_size)
            total, drawdown = _path_statistics(path)
            returns.append(total)
            drawdowns.append(drawdown)
            if 1 + total <= self.ruin_threshold:
                ruined += 1

        returns.sort()
        drawdowns.sort()
        return MonteCarloResult(
            spec=spec,
            return_percentiles={p: _percentile(returns, p) for p in PERCENTILES},
            drawdown_percentiles={p: _percentile(drawdowns, p) for p in PERCENTILES},
            ruin_probability=ruined / spec.iterations,
            iterations_completed=spec.iterations,
        )

    @staticmethod
    def _resample(
        fractions: Sequence[float], rng: random.Random, block_size: int | None
    ) -> list[float]:
        """One path, the same length as the original.

        Sampled with replacement: sampling without it is a permutation, which
        has the same total return every time and would report a distribution of
        drawdowns around a single fixed endpoint.
        """
        count = len(fractions)
        if not block_size or block_size < 2:
            return [fractions[rng.randrange(count)] for _ in range(count)]
        path: list[float] = []
        while len(path) < count:
            start = rng.randrange(count)
            # Wraps around the end rather than truncating, so late trades are
            # sampled as often as early ones.
            path.extend(fractions[(start + offset) % count] for offset in range(block_size))
        return path[:count]

    @staticmethod
    def _trade_fractions(result: BacktestResult) -> list[float]:
        """Each trade's net P&L as a fraction of the equity it was risked against.

        A trade whose entry date is not on the curve is skipped rather than
        divided by the starting capital: the fallback would make early trades
        look correct and later ones look enormous.
        """
        equity_on = dict(result.equity_curve)
        fractions: list[float] = []
        for trade in result.trades:
            equity = _equity_at(equity_on, trade)
            if equity is None or equity <= 0:
                continue
            fractions.append(float(trade.net_pnl / equity))
        return fractions

    @staticmethod
    def _return_fractions(result: BacktestResult) -> list[float]:
        curve = result.equity_curve
        out: list[float] = []
        for (_, previous), (_, current) in itertools.pairwise(curve):
            if previous <= 0:
                break
            out.append(float((current - previous) / previous))
        return out


def _equity_at(equity_on: dict[dt.date, Decimal], trade: BacktestTrade) -> Decimal | None:
    return equity_on.get(trade.entry_date)
