"""Turning an equity curve and a list of trades into numbers worth quoting.

Every metric here is a place where a defensible-looking formula flatters a
strategy, so each one records which variant was chosen and what the other one
would have done.

**Returns come from the equity curve, never from summing trade P&L.** The sum
of trade profits is not the account's return: it ignores how much capital each
trade actually used, and it ignores the stretches with nothing open. Two
strategies with identical trade lists and different sizing have different
returns, and only the curve knows that.

**Sharpe is computed from per-session returns and annualised by the square root
of the configured factor.** That is standard and it is also the assumption most
likely to be wrong, because it treats the curve's points as evenly spaced. A
curve with gaps annualises to a number that means nothing, so
:func:`analyse` requires the curve to be dense over its own range.

**Sortino divides by the count of all periods, not the count of losing ones.**
Dividing by the losing periods is the common error and it inflates the ratio
exactly when losses are rare — which is when somebody is most tempted to quote
it.

**A drawdown that never recovered still counts, and its duration runs to the
end of the curve.** Measuring only completed peak-to-recovery cycles reports a
strategy that ended 40% underwater as having a shorter worst drawdown than one
that recovered, which inverts the ranking.

**Ratios with an empty or degenerate denominator are ``None``, not zero and not
infinity.** No losing trades makes profit factor undefined rather than
excellent, and a zero drawdown makes Calmar undefined rather than perfect. A
number that is really "not applicable" gets compared against real ones.

**Neither the risk-free rate nor the annualisation factor has a default here.**
They are measurement assumptions, and a Sharpe ratio computed against an
invented 0% rate is not comparable with anything.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradeit.backtesting.base import BacktestTrade, PerformanceMetrics

__all__ = ["ATTRIBUTION_KEYS", "StandardPerformanceAnalyzer"]

#: The dimensions ``attribute`` will group by. Each names a field on
#: ``BacktestTrade``; asking for anything else is an error rather than an empty
#: result, because a silent empty attribution reads as "no concentration".
ATTRIBUTION_KEYS = ("sector", "regime_at_entry", "exit_reason")


def _returns(curve: Sequence[tuple[dt.date, Decimal]]) -> list[float]:
    """Simple period-over-period returns.

    Zero or negative equity ends the series: there is no meaningful return
    from a wiped-out account, and continuing would produce sign-flipped
    nonsense that looks like a recovery.
    """
    out: list[float] = []
    for (_, previous), (_, current) in itertools.pairwise(curve):
        if previous <= 0:
            break
        out.append(float((current - previous) / previous))
    return out


def _max_drawdown(curve: Sequence[tuple[dt.date, Decimal]]) -> tuple[float, int]:
    """Deepest peak-to-trough fall, and the longest time spent below a peak.

    Duration is measured in curve points rather than calendar days, so it reads
    as sessions. An unrecovered drawdown runs to the end of the curve.
    """
    if not curve:
        return 0.0, 0
    peak = curve[0][1]
    peak_index = 0
    worst = 0.0
    longest = 0
    for index, (_, equity) in enumerate(curve):
        if equity >= peak:
            longest = max(longest, index - peak_index)
            peak = equity
            peak_index = index
            continue
        if peak > 0:
            worst = max(worst, float((peak - equity) / peak))
    # The final stretch may never have recovered; it still happened.
    longest = max(longest, len(curve) - 1 - peak_index)
    return worst, longest


@dataclass(frozen=True, slots=True)
class StandardPerformanceAnalyzer:
    """Computes metrics from a curve and attributes P&L to its sources.

    ``risk_free_rate`` is annualised and expressed as a fraction (0.04 for 4%).
    It is divided by ``annualisation_factor`` to reach a per-session hurdle.
    """

    annualisation_factor: int
    risk_free_rate: float
    name: str = "standard"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "annualisation_factor": self.annualisation_factor,
            "risk_free_rate": self.risk_free_rate,
        }

    @property
    def _period_hurdle(self) -> float:
        return self.risk_free_rate / self.annualisation_factor

    def compute(
        self,
        trades: Sequence[BacktestTrade],
        equity_curve: Sequence[tuple[dt.date, Decimal]],
        benchmark: Sequence[tuple[dt.date, Decimal]] | None = None,
    ) -> PerformanceMetrics:
        """Every headline number for one run.

        A run that traded nothing is a real outcome and produces zeros with
        ``None`` ratios, not an exception. A run with no curve at all is a
        programming error and raises.
        """
        if len(equity_curve) < 2:
            raise ValueError(
                "an equity curve needs at least two points; one point is a balance, "
                "not a performance record"
            )
        dates = [day for day, _ in equity_curve]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise ValueError("the equity curve must be strictly ordered by date")

        start_equity = equity_curve[0][1]
        end_equity = equity_curve[-1][1]
        if start_equity <= 0:
            raise ValueError("a backtest cannot start with no capital")

        total_return = float((end_equity - start_equity) / start_equity)
        periodic = _returns(equity_curve)
        drawdown, drawdown_sessions = _max_drawdown(equity_curve)

        years = (dates[-1] - dates[0]).days / 365.25
        if end_equity <= 0:
            # Ruin. CAGR of a zero balance is not a small negative number.
            cagr = -1.0
        elif years > 0:
            # In float rather than Decimal: Decimal's fractional power goes
            # through exp/ln anyway, and raises on inputs this guards against.
            cagr = (float(end_equity) / float(start_equity)) ** (1 / years) - 1.0
        else:
            cagr = 0.0

        sharpe = self._sharpe(periodic)
        sortino = self._sortino(periodic)
        calmar = cagr / drawdown if drawdown > 0 else None

        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl < 0]
        gross_win = sum((t.net_pnl for t in wins), Decimal(0))
        gross_loss = -sum((t.net_pnl for t in losses), Decimal(0))

        r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
        win_r = [r for r in r_values if r > 0]
        loss_r = [r for r in r_values if r <= 0]

        return PerformanceMetrics(
            total_return_pct=total_return,
            cagr=cagr,
            max_drawdown_pct=drawdown,
            max_drawdown_duration_sessions=drawdown_sessions,
            sharpe=sharpe,
            sortino=sortino,
            calmar=calmar,
            win_rate=len(wins) / len(trades) if trades else 0.0,
            profit_factor=float(gross_win / gross_loss) if gross_loss > 0 else None,
            expectancy_r=statistics.fmean(r_values) if r_values else None,
            average_win_r=statistics.fmean(win_r) if win_r else None,
            average_loss_r=statistics.fmean(loss_r) if loss_r else None,
            trade_count=len(trades),
            exposure_pct=self._exposure(trades, dates),
            turnover=self._turnover(trades, equity_curve, years),
            benchmark_return_pct=self._benchmark_return(benchmark),
        )

    def _sharpe(self, periodic: Sequence[float]) -> float | None:
        if len(periodic) < 2:
            return None
        excess = [r - self._period_hurdle for r in periodic]
        deviation = statistics.stdev(excess)
        if deviation == 0:
            # A perfectly flat curve has no risk and no Sharpe. Reporting a
            # huge number here is how a strategy that never traded wins a
            # leaderboard.
            return None
        return statistics.fmean(excess) / deviation * math.sqrt(self.annualisation_factor)

    def _sortino(self, periodic: Sequence[float]) -> float | None:
        if len(periodic) < 2:
            return None
        excess = [r - self._period_hurdle for r in periodic]
        downside = [min(r, 0.0) ** 2 for r in excess]
        # Divided by every period, not only the losing ones. See the module
        # docstring: the other denominator inflates the ratio when losses are
        # rare, which is exactly when it gets quoted.
        deviation = math.sqrt(sum(downside) / len(excess))
        if deviation == 0:
            return None
        return statistics.fmean(excess) / deviation * math.sqrt(self.annualisation_factor)

    @staticmethod
    def _exposure(trades: Sequence[BacktestTrade], dates: Sequence[dt.date]) -> float:
        """Share of sessions with at least one position open.

        Counts a session once however many positions were open: this measures
        time in the market, not leverage. Concurrency belongs to gross
        exposure, which the risk snapshot records.
        """
        if not trades or not dates:
            return 0.0
        held = {
            day
            for day in dates
            if any(trade.entry_date <= day <= trade.exit_date for trade in trades)
        }
        return len(held) / len(dates)

    @staticmethod
    def _turnover(
        trades: Sequence[BacktestTrade],
        curve: Sequence[tuple[dt.date, Decimal]],
        years: float,
    ) -> float:
        """Traded notional per unit of average equity, per year.

        Both legs count, because both pay costs. Stated explicitly because
        "turnover" is defined at least four ways in common use and the others
        differ by factors of two.
        """
        if not trades or years <= 0:
            return 0.0
        average_equity = statistics.fmean(float(equity) for _, equity in curve)
        if average_equity <= 0:
            return 0.0
        traded = sum(
            float(trade.quantity) * float(trade.entry_price + trade.exit_price) for trade in trades
        )
        return traded / average_equity / years

    @staticmethod
    def _benchmark_return(
        benchmark: Sequence[tuple[dt.date, Decimal]] | None,
    ) -> float | None:
        if not benchmark or len(benchmark) < 2 or benchmark[0][1] <= 0:
            return None
        return float((benchmark[-1][1] - benchmark[0][1]) / benchmark[0][1])

    def attribute(self, trades: Sequence[BacktestTrade], by: str) -> dict[str, float]:
        """Net P&L per bucket, in currency rather than as a share.

        Deliberately not normalised to percentages. A share of a total that is
        itself negative is uninterpretable -- a bucket contributing +200 to a
        total of -100 would report as -200%, which reads as the worst sector
        when it was the only profitable one.

        Trades with no value for the dimension are grouped under ``unknown``
        rather than dropped, so the parts always sum to the whole.
        """
        if by not in ATTRIBUTION_KEYS:
            raise ValueError(
                f"cannot attribute by {by!r}; known dimensions are {', '.join(ATTRIBUTION_KEYS)}"
            )
        buckets: dict[str, float] = {}
        for trade in trades:
            key = getattr(trade, by) or "unknown"
            buckets[key] = buckets.get(key, 0.0) + float(trade.net_pnl)
        return dict(sorted(buckets.items()))
