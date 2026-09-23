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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from tradeit.analytics.kernels import atr_percent
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

    **Where the stop sits is the whole experiment, not a detail.** With
    risk-based sizing, shares bought are ``risk_amount / (entry - stop)``, so
    the stop chooses the position size:

    * a **fixed-percentage** stop makes ``entry - stop`` proportional to price,
      so notional is *constant* -- an equal-dollar portfolio
    * an **ATR** stop makes ``entry - stop`` proportional to volatility, so
      notional is proportional to ``1 / ATR%`` -- an equal-*risk* portfolio,
      holding less of the wild names

    Those are the two arms of the volatility experiment, and they differ in
    exactly one thing. Exactly one of ``stop_pct`` and ``stop_atr_multiple``
    must be given; neither has a default, because an invented stop is an
    invented position size.

    The liquidity floors, when supplied, are applied on the rule's own trailing
    history, so a name is considered only while it was tradeable.
    """

    fast: int
    slow: int
    stop_pct: Decimal | None = None
    stop_atr_multiple: Decimal | None = None
    atr_period: int = 14
    min_price: Decimal | None = None
    min_dollar_volume: Decimal | None = None
    dollar_volume_lookback: int = 20
    #: The same split source the engine uses (``CorpusSessionData.splits_on``).
    #: Must be set before the first session -- see ``__call__``. The engine hands
    #: strategies RAW bars, so history this rule keeps must be restated at each
    #: ex-date or a 2-for-1 reads as a 50% fall inside every window spanning it,
    #: distorting both the crossover and the ATR stop that sizes arm B.
    splits_on: Callable[[dt.date], Mapping[int, Decimal]] | None = None
    closes: dict[int, deque[Decimal]] = field(default_factory=dict)
    _highs: dict[int, deque[Decimal]] = field(default_factory=dict)
    _lows: dict[int, deque[Decimal]] = field(default_factory=dict)
    _volumes: dict[int, deque[Decimal]] = field(default_factory=dict)
    _was_above: dict[int, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.fast >= self.slow:
            raise ValueError(f"fast ({self.fast}) must be shorter than slow ({self.slow})")
        if (self.stop_pct is None) == (self.stop_atr_multiple is None):
            raise ValueError(
                "give exactly one of stop_pct and stop_atr_multiple: the stop decides "
                "position size, so two rules for it means two different portfolios"
            )
        if self.stop_pct is not None and not 0 < self.stop_pct < 1:
            raise ValueError("stop_pct must be a fraction between 0 and 1")
        if self.stop_atr_multiple is not None and self.stop_atr_multiple <= 0:
            raise ValueError("stop_atr_multiple must be positive")

    def _tradeable(self, instrument_id: int, bar: OhlcvBar) -> bool:
        """Whether the floors, if any, were met on trailing data."""
        if self.min_price is not None and bar.close < self.min_price:
            return False
        if self.min_dollar_volume is None:
            return True
        closes = list(self.closes[instrument_id])[-self.dollar_volume_lookback :]
        volumes = list(self._volumes[instrument_id])[-self.dollar_volume_lookback :]
        if len(volumes) < self.dollar_volume_lookback:
            return False
        turnover = sum((c * v for c, v in zip(closes, volumes, strict=True)), Decimal(0)) / len(
            volumes
        )
        return turnover >= self.min_dollar_volume

    def _stop(self, instrument_id: int, bar: OhlcvBar) -> Decimal | None:
        """Where the stop sits, which is where the position size comes from."""
        if self.stop_pct is not None:
            return bar.close * (1 - self.stop_pct)
        assert self.stop_atr_multiple is not None
        highs = np.array([float(v) for v in self._highs[instrument_id]])
        lows = np.array([float(v) for v in self._lows[instrument_id]])
        closes = np.array([float(v) for v in self.closes[instrument_id]])
        if len(closes) < self.atr_period + 2:
            return None
        atr_pct = atr_percent(highs, lows, closes, self.atr_period)[-1]
        if not np.isfinite(atr_pct) or atr_pct <= 0:
            return None
        return bar.close * (Decimal(1) - self.stop_atr_multiple * Decimal(str(atr_pct)))

    def _restate_for_splits(self, session_date: dt.date) -> None:
        """Put stored history on the post-split share count, as the engine does."""
        assert self.splits_on is not None
        for instrument_id, ratio in self.splits_on(session_date).items():
            if ratio <= 0 or instrument_id not in self.closes:
                continue
            for store in (self.closes, self._highs, self._lows):
                store[instrument_id] = deque(
                    (v / ratio for v in store[instrument_id]), maxlen=self.slow
                )
            self._volumes[instrument_id] = deque(
                (v * ratio for v in self._volumes[instrument_id]), maxlen=self.slow
            )

    def __call__(
        self, session_date: dt.date, bars: Mapping[int, OhlcvBar]
    ) -> Sequence[EntryCandidate]:
        if self.splits_on is None:
            # Fail closed. Until 2026-09-18 this rule kept raw history and never
            # restated it, and a run that silently skipped the restatement would
            # reproduce that defect with nothing to say so.
            raise ValueError(
                "splits_on is not set: assign CorpusSessionData.splits_on before running"
            )
        self._restate_for_splits(session_date)
        out: list[EntryCandidate] = []
        for instrument_id, bar in bars.items():
            history = self.closes.setdefault(instrument_id, deque(maxlen=self.slow))
            history.append(bar.close)
            self._highs.setdefault(instrument_id, deque(maxlen=self.slow)).append(bar.high)
            self._lows.setdefault(instrument_id, deque(maxlen=self.slow)).append(bar.low)
            self._volumes.setdefault(instrument_id, deque(maxlen=self.slow)).append(bar.volume)
            if len(history) < self.slow:
                continue
            if not self._tradeable(instrument_id, bar):
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
            stop = self._stop(instrument_id, bar)
            if stop is None or stop >= bar.close:
                # No usable stop -- too little history for an ATR, or a
                # volatility so large the stop would sit at or above entry.
                # Refused rather than widened: a stop chosen to make a trade
                # possible is not a risk limit.
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
                    stop_price=stop,
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
    delisting_recovery_by_instrument: Mapping[int, Decimal] | None = None,
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
                sizing=config.sizing,
                risk=config.risk,
                max_participation=participation,
                # Without this the cost guard cannot price a round trip and
                # silently does not apply -- the defect §38 recorded.
                costs=config.costs,
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
        delisting_recovery_by_instrument=dict(delisting_recovery_by_instrument or {}),
    )
