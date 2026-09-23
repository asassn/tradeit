"""A cross-sectional factor as a candidate source for the event-driven engine.

The engine is built around a strategy that *nominates* and a portfolio that
*decides* -- the portfolio's sizer, risk rules and stop ladder choose what is
actually bought and when it is sold. :class:`FactorTilt` fits that division
exactly: on each rebalance date it ranks the sample's tradeable securities by a
factor and nominates a fraction of them, best first, and leaves everything else
to the platform. It never sizes, never exits and never sees a bar it has not been
given.

Written for ``docs/prereg/LOW_VOLATILITY_BACKTEST_2026-09-18.md``, where one
instance nominates the calmest fifth by 60-session realized volatility and a
second, identical in every other respect, nominates the same number at random.
The difference between their portfolios is the selection, and nothing else.

**Splits.** The engine hands strategies RAW bars -- it applies splits to holdings
itself -- so a strategy that keeps its own history must restate that history at
each ex-date, or a 2-for-1 reads as a 50% fall inside every window that spans
it. ``splits_on`` is the same source the engine uses, and the restatement
mirrors the engine's own: prices divided by the ratio, volume multiplied. The
moving-average baseline in :mod:`tradeit.backtesting.baselines` does **not** do
this; that is a known defect of the baseline, recorded in §36, and the reason this
class does not inherit from it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

import numpy as np

from tradeit.analytics.kernels import atr_percent, realized_volatility
from tradeit.core.enums import SignalDirection
from tradeit.core.models import OhlcvBar
from tradeit.portfolio.cycle import EntryCandidate
from tradeit.strategy.base import OpportunityScore, ScoreComponent

__all__ = ["FactorTilt", "Selection"]


class Selection(StrEnum):
    """How nominees are chosen from the eligible securities on a rebalance date."""

    #: Lowest realized volatility first.
    CALM = "calm"
    #: A seeded hash of (security, date): a selection that knows nothing, used as
    #: the control arm so the only difference between two portfolios is choice.
    RANDOM = "random"
    #: Highest external score first -- earnings surprise in
    #: ``COMBINATION_PORTFOLIO_2026-09-22``. The score is supplied per
    #: (security, session) rather than computed here, because a fundamental is
    #: point-in-time evidence about a filing and this class only sees bars.
    SURPRISE = "surprise"
    #: The mean of two within-date ranks: volatility ascending and the external
    #: score descending. A plain mean, because a weight would be a parameter.
    COMBINED = "combined"


@dataclass
class FactorTilt:
    """Nominate a fraction of the tradeable securities on each rebalance date.

    Every parameter is required and has no default, because each one is a
    registered choice and an inherited default would be an unregistered one.
    """

    selection: Selection
    rebalance_dates: frozenset[dt.date]
    splits_on: Callable[[dt.date], Mapping[int, Decimal]]
    volatility_lookback: int
    fraction: float
    min_history: int
    #: The cheapest a security may close at on the rebalance date. Required:
    #: without it a stock collapsing to a $0.0001 placeholder print kept the
    #: turnover of its last normal weeks, read as calm, and was bought -- 20.6
    #: million shares whose per-share commission sank a registered backtest.
    min_price: Decimal
    min_dollar_volume: Decimal
    dollar_volume_lookback: int
    max_atr_percent: float
    stop_pct: Decimal
    seed: int
    atr_period: int = 14
    #: ``VOLATILITY_STOP_2026-09-22``: when set, the stop is this many ATR(14)
    #: below entry instead of ``stop_pct`` of it, bounded by the two fractions
    #: below. None keeps the fixed-percentage stop, which is still the
    #: platform's rule until that registration's criteria are met.
    atr_stop_multiple: float | None = None
    atr_stop_floor: Decimal = Decimal("0.03")
    atr_stop_cap: Decimal = Decimal("0.13")
    _closes: dict[int, deque[float]] = field(default_factory=dict)
    _highs: dict[int, deque[float]] = field(default_factory=dict)
    _lows: dict[int, deque[float]] = field(default_factory=dict)
    _turnover: dict[int, deque[float]] = field(default_factory=dict)
    _seen: dict[int, int] = field(default_factory=dict)
    #: ``MARKET_REGIME_GATE_2026-09-22``: when set, nominate nothing on a
    #: rebalance date where the sample's own equal-weighted index sits below its
    #: simple average over this many sessions. None leaves the gate off, which
    #: is the platform's behaviour until that registration's criteria are met.
    regime_lookback: int | None = None
    _index: list[float] = field(default_factory=list)
    #: ``(security, session) -> score``, higher is better. Required by SURPRISE
    #: and COMBINED and ignored by the others. A security absent from it is not
    #: nominable by those two arms and is untouched for the rest, which is what
    #: keeps the control a control.
    external: Mapping[tuple[int, dt.date], float] = field(default_factory=dict)
    #: What each rebalance nominated, kept so a run can be audited afterwards.
    nominated: dict[dt.date, tuple[int, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 < self.fraction <= 1:
            raise ValueError("fraction must be in (0, 1]")
        if not 0 < self.stop_pct < 1:
            raise ValueError("stop_pct must be a fraction between 0 and 1")
        if self.min_price <= 0:
            raise ValueError("min_price must be positive")
        if self.min_history < max(self.volatility_lookback, self.dollar_volume_lookback) + 1:
            raise ValueError("min_history must cover the longest lookback")

    # -- history --------------------------------------------------------------

    def _window(self) -> int:
        return max(self.volatility_lookback, self.dollar_volume_lookback, self.atr_period) + 2

    def _restate_for_splits(self, session_date: dt.date) -> None:
        """Put stored history on the post-split share count, as the engine does."""
        for instrument_id, ratio in self.splits_on(session_date).items():
            if ratio <= 0 or instrument_id not in self._closes:
                continue
            r = float(ratio)
            for store in (self._closes, self._highs, self._lows):
                store[instrument_id] = deque(
                    (v / r for v in store[instrument_id]), maxlen=self._window()
                )
            # Dollar turnover is price times shares: the two restatements cancel,
            # so it is left alone -- restating it would be the §0.9 error again.

    def _extend_index(self, bars: Mapping[int, OhlcvBar]) -> None:
        """One more point on the sample's own equal-weighted index.

        The mean of this session's one-session returns across the securities
        that have a previous close, compounded onto the running level. Built
        from returns rather than from a mean price so a security entering or
        leaving the sample moves the index by its return and not by its price.
        """
        moves = [
            float(bar.close) / self._closes[instrument_id][-1]
            for instrument_id, bar in bars.items()
            if self._closes.get(instrument_id)
            and bar.close > 0
            and self._closes[instrument_id][-1] > 0
        ]
        if not moves:
            return
        level = self._index[-1] if self._index else 1.0
        self._index.append(level * float(np.mean(moves)))

    def regime_is_on(self) -> bool:
        """Whether the gate permits nominating. Always true when it is off."""
        if self.regime_lookback is None:
            return True
        if len(self._index) < self.regime_lookback:
            # Not enough history to judge the regime. Permitting is the choice
            # that leaves the ungated behaviour intact rather than inventing a
            # flat period at the start of every run.
            return True
        window = self._index[-self.regime_lookback :]
        return self._index[-1] >= float(np.mean(window))

    def observe(self, session_date: dt.date, bars: Mapping[int, OhlcvBar]) -> None:
        """Take one session into history without nominating anything.

        Called for warm-up sessions before trading starts, and by ``__call__``
        on every session after.
        """
        self._restate_for_splits(session_date)
        window = self._window()
        for instrument_id, bar in bars.items():
            self._closes.setdefault(instrument_id, deque(maxlen=window)).append(float(bar.close))
            self._highs.setdefault(instrument_id, deque(maxlen=window)).append(float(bar.high))
            self._lows.setdefault(instrument_id, deque(maxlen=window)).append(float(bar.low))
            self._turnover.setdefault(instrument_id, deque(maxlen=window)).append(
                float(bar.close * bar.volume)
            )
            self._seen[instrument_id] = self._seen.get(instrument_id, 0) + 1

    # -- the factor -----------------------------------------------------------

    def _eligible(self, instrument_id: int, bar: OhlcvBar) -> float | None:
        """The security's volatility if it may be nominated today, else ``None``."""
        if self._seen.get(instrument_id, 0) < self.min_history or bar.close < self.min_price:
            return None
        turnover = list(self._turnover[instrument_id])[-self.dollar_volume_lookback :]
        if float(np.mean(turnover)) < float(self.min_dollar_volume):
            return None
        closes = np.array(self._closes[instrument_id])
        highs = np.array(self._highs[instrument_id])
        lows = np.array(self._lows[instrument_id])
        with np.errstate(divide="ignore", invalid="ignore"):
            atr = atr_percent(highs, lows, closes, self.atr_period)[-1]
            vol = realized_volatility(closes, self.volatility_lookback)[-1]
        if not (np.isfinite(atr) and atr <= self.max_atr_percent and np.isfinite(vol)):
            return None
        return float(vol)

    def stop_for(self, instrument_id: int, close: Decimal) -> Decimal:
        """Where this candidate's stop sits.

        The fixed rule is a fraction of price and knows nothing about the
        security. §46 measured what that costs: a stop 8% away never binds for
        something that moves 2% a month, so the position is never stopped and
        is delisted instead. The scaled rule asks the security how far it
        usually travels and puts the stop outside *that*, bounded so a quiet
        name cannot get an absurdly tight stop nor a wild one an absurdly loose.
        """
        if self.atr_stop_multiple is None:
            return close * (Decimal(1) - self.stop_pct)
        highs = np.array(self._highs.get(instrument_id, ()), dtype=np.float64)
        lows = np.array(self._lows.get(instrument_id, ()), dtype=np.float64)
        closes = np.array(self._closes.get(instrument_id, ()), dtype=np.float64)
        fraction: Decimal | None = None
        if closes.shape[0] > self.atr_period:
            with np.errstate(divide="ignore", invalid="ignore"):
                atr = atr_percent(highs, lows, closes, self.atr_period)[-1]
            if np.isfinite(atr) and atr > 0:
                fraction = Decimal(str(self.atr_stop_multiple * float(atr)))
        if fraction is None:
            # No usable ATR: fall back to the fixed rule rather than invent a
            # stop. A fabricated stop is worse than the one being replaced.
            fraction = self.stop_pct
        fraction = min(max(fraction, self.atr_stop_floor), self.atr_stop_cap)
        return close * (Decimal(1) - fraction)

    def _random_score(self, instrument_id: int, session_date: dt.date) -> float:
        digest = hashlib.sha256(
            f"{self.seed}:{instrument_id}:{session_date.isoformat()}".encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    def __call__(
        self, session_date: dt.date, bars: Mapping[int, OhlcvBar]
    ) -> Sequence[EntryCandidate]:
        # The index is extended BEFORE observe() overwrites the previous
        # closes, since it needs both sides of each session's return.
        self._extend_index(bars)
        self.observe(session_date, bars)
        if session_date not in self.rebalance_dates:
            return []
        if not self.regime_is_on():
            self.nominated[session_date] = ()
            return []
        eligible = {
            instrument_id: vol
            for instrument_id, bar in bars.items()
            if (vol := self._eligible(instrument_id, bar)) is not None
        }
        count = max(1, int(len(eligible) * self.fraction)) if eligible else 0
        if self.selection is Selection.CALM:
            # Calmest first; ties broken by id so the order is reproducible.
            ranked = sorted(eligible, key=lambda i: (eligible[i], i))
            chosen = ranked[:count]
            score = {i: -eligible[i] for i in chosen}
        elif self.selection in (Selection.SURPRISE, Selection.COMBINED):
            scored = {
                i: v for i in eligible if (v := self.external.get((i, session_date))) is not None
            }
            count = max(1, int(len(scored) * self.fraction)) if scored else 0
            if self.selection is Selection.SURPRISE:
                ranked = sorted(scored, key=lambda i: (-scored[i], i))
            else:
                # Two within-date ranks, averaged. Ranking rather than scaling
                # is deliberate: the two quantities have no common unit, and any
                # standardisation would be a choice this registration does not make.
                calm_rank = {
                    i: n for n, i in enumerate(sorted(scored, key=lambda i: (eligible[i], i)))
                }
                score_rank = {
                    i: n for n, i in enumerate(sorted(scored, key=lambda i: (-scored[i], i)))
                }
                ranked = sorted(scored, key=lambda i: ((calm_rank[i] + score_rank[i]) / 2.0, i))
            chosen = ranked[:count]
            score = {i: float(len(ranked) - n) for n, i in enumerate(ranked) if i in set(chosen)}
        else:
            ranked = sorted(eligible, key=lambda i: self._random_score(i, session_date))
            chosen = ranked[:count]
            score = {i: 1.0 - self._random_score(i, session_date) for i in chosen}
        self.nominated[session_date] = tuple(chosen)
        return [self._candidate(i, bars[i], session_date, score[i]) for i in chosen]

    def _candidate(
        self, instrument_id: int, bar: OhlcvBar, session_date: dt.date, score: float
    ) -> EntryCandidate:
        return EntryCandidate(
            score=OpportunityScore(
                instrument_id=instrument_id,
                session_date=session_date,
                direction=SignalDirection.LONG,
                total=score,
                components=(
                    ScoreComponent(
                        name=f"factor_{self.selection.value}",
                        raw_value=score,
                        normalised=score,
                        weight=1.0,
                    ),
                ),
                feature_set_digest=f"factor_tilt_{self.selection.value}",
                strategy_config_digest="factor_tilt",
            ),
            entry_price=bar.close,
            # Equal-dollar sizing, so the arms differ only in what they
            # choose; the stop rule is the same for every arm in a run.
            stop_price=self.stop_for(instrument_id, bar.close),
            sector=None,
            average_dollar_volume=bar.close * bar.volume,
        )
