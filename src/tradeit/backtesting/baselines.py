"""Baselines a candidate signal has to beat, and the wiring to run one.

The roadmap requires every candidate to be compared against simple
alternatives -- buy and hold, the benchmark, a moving-average rule, a naive
classifier -- and states the consequence plainly: **a sophisticated model that
cannot beat them robustly is not promoted for being sophisticated.** That only
works if the baselines exist as real, runnable strategies rather than as
numbers somebody quotes, so they live here.

:class:`MovingAverageCross` is deliberately unremarkable. It is not a proposal
and nothing about it is tuned; if the surrounding machinery flatters anything,
it will flatter this, and that is exactly what it is for.

:func:`build_engine` assembles the Phase 8 and Phase 9 components into a
runnable engine. It exists because the assembly is long, easy to get subtly
wrong, and identical between an ordinary run and a survivorship comparison --
and two copies of it would drift.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from tradeit.backtesting.engine import EventDrivenEngine, SessionData
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.core.enums import SignalDirection
from tradeit.core.models import OhlcvBar
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.reproducibility.versioning import RunManifest
from tradeit.risk.base import RiskRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.strategy.base import OpportunityScore, ScoreComponent
from tradeit.strategy.config import StrategyConfig

__all__ = ["MovingAverageCross", "build_engine"]


@dataclass
class MovingAverageCross:
    """Long when the fast mean crosses above the slow one. A baseline.

    It carries its own history because the engine hands it one session at a
    time, and it only ever sees sessions it has already been given, in order.
    That is what keeps it honest: there is no way for it to look forward,
    because it has never been shown a bar it should not have.

    ``stop_pct`` has no default. Where the stop sits decides position size,
    which decides everything downstream, and a baseline with an invented stop
    is not a baseline anybody agreed to.
    """

    fast: int
    slow: int
    stop_pct: Decimal
    closes: dict[int, deque[Decimal]] = field(default_factory=dict)
    _was_above: dict[int, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.fast >= self.slow:
            raise ValueError(f"fast ({self.fast}) must be shorter than slow ({self.slow})")
        if not 0 < self.stop_pct < 1:
            raise ValueError("stop_pct must be a fraction between 0 and 1")

    def __call__(
        self, session_date: dt.date, bars: Mapping[int, OhlcvBar]
    ) -> Sequence[EntryCandidate]:
        out: list[EntryCandidate] = []
        for instrument_id, bar in bars.items():
            history = self.closes.setdefault(instrument_id, deque(maxlen=self.slow))
            history.append(bar.close)
            if len(history) < self.slow:
                continue
            values = list(history)
            # Seeded with Decimal(0): an unseeded sum starts at int 0, which
            # makes the result Decimal-or-int and the division float-or-Decimal.
            fast_mean = sum(values[-self.fast :], Decimal(0)) / self.fast
            slow_mean = sum(values, Decimal(0)) / self.slow
            above = fast_mean > slow_mean
            # A cross, not a state: entering every session the fast mean sits
            # above the slow one would re-buy the same name indefinitely.
            crossed_up = above and not self._was_above.get(instrument_id, above)
            self._was_above[instrument_id] = above
            if not crossed_up:
                continue
            strength = float((fast_mean - slow_mean) / slow_mean)
            out.append(
                EntryCandidate(
                    score=OpportunityScore(
                        instrument_id=instrument_id,
                        session_date=session_date,
                        direction=SignalDirection.LONG,
                        total=strength,
                        components=(
                            ScoreComponent(
                                name="ma_spread",
                                raw_value=strength,
                                normalised=strength,
                                weight=1.0,
                            ),
                        ),
                        feature_set_digest="ma_cross_baseline",
                        strategy_config_digest="baseline",
                    ),
                    entry_price=bar.close,
                    stop_price=bar.close * (1 - self.stop_pct),
                    sector=None,
                    average_dollar_volume=bar.close * bar.volume,
                )
            )
        return out


def build_engine(
    config: StrategyConfig,
    data: SessionData,
    manifest: RunManifest,
    *,
    participation: Decimal,
    risk_free_rate: float,
    delisting_after_sessions: int,
    delisting_recovery: Decimal,
) -> EventDrivenEngine:
    """Assemble the live components behind a simulated venue.

    Every number comes from ``config`` except the four that have no home in it,
    and those are required arguments rather than defaults for the usual reason:
    participation, the risk-free rate and both delisting assumptions each change
    the answer, and none of them should be inherited by accident.
    """
    rules: tuple[RiskRule, ...] = (
        PortfolioHeatRule(config=config.risk),
        MaxPositionsRule(config=config.risk),
        PositionSizeRule(max_position_pct_of_equity=config.sizing.max_position_pct_of_equity),
        GrossExposureRule(config=config.risk),
    )
    return EventDrivenEngine(
        cycle=PortfolioCycle(
            sizer=RiskBasedSizer(
                sizing=config.sizing, risk=config.risk, max_participation=participation
            ),
            engine=MostRestrictiveEngine(rule_set=rules, sizing=config.sizing),
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=config.exits),
        ),
        costs=ParticipationCostModel(config=config.costs),
        fills=BarFillModel(max_participation=participation),
        data=data,
        analyzer=StandardPerformanceAnalyzer(
            annualisation_factor=config.indicators.annualisation_factor,
            risk_free_rate=risk_free_rate,
        ),
        manifest=manifest,
        delisting_after_sessions=delisting_after_sessions,
        delisting_recovery=delisting_recovery,
    )
