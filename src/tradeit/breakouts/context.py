"""Market, sector, relative-strength and event context around a breakout.

Everything here is **evidence, not a veto**. A breakout in a bear market is a
breakout in a bear market: that is a fact worth storing, and it is emphatically
not Phase 5's business to reject it. Opportunity scoring and the portfolio
phases decide how much regime matters, and they can only do so if Phase 5 hands
them the observation rather than a decision already taken on their behalf.

Three failure modes this module is built to avoid:

**Silent bearishness from missing data.** No sector series does not mean a weak
sector. Every optional input has an explicit "unavailable, because ..." path
that reduces evidence coverage and leaves the quality score renormalised over
what was actually measurable. Scoring an absent input as zero would report a
data gap as a market judgement, and the two are indistinguishable downstream.

**Fabricated history.** A sector classification is a fact about a point in time.
Using today's GICS mapping to say which sector a stock was in three years ago
manufactures a sector membership that nobody could have known, and does so in a
way that looks entirely reasonable. ``SectorReading`` therefore carries the
source and the as-of date, and an ETF proxy is labelled as an ETF proxy.

**Relative strength confused with RSI.** They share three letters and nothing
else. RS here is price versus a benchmark; RSI is a bounded oscillator over a
single series. The type is named, the field names say ``benchmark``, and the
docstrings say so — because the confusion is common enough that it will
otherwise be introduced by someone reading only the abbreviation.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from tradeit.breakouts.base import EarningsContext
from tradeit.core.enums import MarketRegime
from tradeit.patterns.scoring import ramp_score


class SectorSourceKind(StrEnum):
    """Where a sector reading came from, so a reader can weigh it.

    ``ETF_PROXY`` is legitimate and common — a sector ETF's price series is a
    perfectly good participation proxy — but it is not the same object as a
    membership-weighted breadth figure, and a dataset that cannot tell them
    apart will average them.
    """

    CLASSIFICATION = "classification"
    ETF_PROXY = "etf_proxy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RelativeStrengthReading:
    """Price versus a benchmark around the breakout. **Not RSI.**

    The four facts the brief asks for, kept as separate booleans rather than
    collapsed into a rating, because they occur in informative combinations: a
    new RS high *before* price is a much stronger observation than one made with
    price, and RS deteriorating while price breaks out is the combination most
    worth flagging.
    """

    #: RS line percentile within its own recent history, 0-100.
    rs_percentile: float | None = None
    #: Cross-sectional percentile against the universe, when supplied.
    universe_percentile: float | None = None
    near_rs_high: bool = False
    new_rs_high_before_price: bool = False
    new_rs_high_with_price: bool = False
    improving_after_breakout: bool | None = None
    deteriorating_despite_breakout: bool = False
    available: bool = True
    unavailable_reason: str = ""

    def score(self) -> float | None:
        """RS_CONFIRMATION_SCORE, or ``None`` when RS could not be measured."""
        if not self.available:
            return None
        base = self.rs_percentile if self.rs_percentile is not None else 50.0
        if self.new_rs_high_before_price:
            base = min(100.0, base + 20.0)
        elif self.new_rs_high_with_price:
            base = min(100.0, base + 10.0)
        elif self.near_rs_high:
            base = min(100.0, base + 5.0)
        if self.deteriorating_despite_breakout:
            base = max(0.0, base - 25.0)
        if self.improving_after_breakout:
            base = min(100.0, base + 10.0)
        return base

    def to_measurements(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if self.rs_percentile is not None:
            out["rs_percentile"] = self.rs_percentile
        if self.universe_percentile is not None:
            out["rs_universe_percentile"] = self.universe_percentile
        score = self.score()
        if score is not None:
            out["rs_confirmation_score"] = score
        out["rs_new_high_before_price"] = float(self.new_rs_high_before_price)
        out["rs_deteriorating"] = float(self.deteriorating_despite_breakout)
        return out


def measure_relative_strength(
    closes: Sequence[float],
    benchmark_closes: Sequence[float] | None,
    *,
    lookback: int = 60,
    breakout_index: int | None = None,
    universe_percentile: float | None = None,
) -> RelativeStrengthReading:
    """Build an RS reading from aligned price and benchmark closes.

    The RS line is the simple ratio. Both series must be the same length and
    aligned session-for-session; a misaligned benchmark yields a plausible
    number computed from mismatched days, which is worse than no number.

    ``breakout_index`` positions the breakout inside the series so "before" and
    "with" can be distinguished. Everything after it is used only for the
    ``improving_after_breakout`` flag, which is a confirmation-side observation
    and is never fed back into the frozen breakout quality score.
    """
    if benchmark_closes is None:
        return RelativeStrengthReading(
            available=False, unavailable_reason="no benchmark series supplied"
        )
    if len(benchmark_closes) != len(closes):
        return RelativeStrengthReading(
            available=False,
            unavailable_reason=(
                f"benchmark has {len(benchmark_closes)} points against "
                f"{len(closes)} price points; a misaligned ratio would be "
                "computed from mismatched sessions"
            ),
        )
    if len(closes) < 10:
        return RelativeStrengthReading(
            available=False, unavailable_reason="fewer than ten sessions of history"
        )

    ratio = np.asarray(closes, dtype=float) / np.asarray(benchmark_closes, dtype=float)
    window = ratio[-lookback:] if len(ratio) > lookback else ratio
    rank = float(np.sum(window <= ratio[-1]) / len(window) * 100.0)

    index = len(ratio) - 1 if breakout_index is None else breakout_index
    index = max(0, min(index, len(ratio) - 1))

    up_to_breakout = ratio[: index + 1]
    breakout_window = (
        up_to_breakout[-lookback:] if len(up_to_breakout) > lookback else up_to_breakout
    )
    rs_high_at_breakout = bool(ratio[index] >= breakout_window.max() - 1e-12)
    near_high = bool(ratio[index] >= breakout_window.max() * 0.98)

    price = np.asarray(closes, dtype=float)
    price_window = price[: index + 1]
    price_window = price_window[-lookback:] if len(price_window) > lookback else price_window
    price_high_at_breakout = bool(price[index] >= price_window.max() - 1e-12)

    # RS made its high earlier than price did: the "before" case.
    before = False
    if rs_high_at_breakout and not price_high_at_breakout:
        before = True
    elif len(breakout_window) > 1:
        rs_high_pos = int(np.argmax(breakout_window))
        price_high_pos = int(np.argmax(price_window))
        before = rs_high_pos < price_high_pos and rs_high_at_breakout

    after = ratio[index + 1 :]
    improving = None if after.size == 0 else bool(after[-1] > ratio[index])
    deteriorating = bool(after.size > 0 and after[-1] < ratio[index] and price[-1] > price[index])

    return RelativeStrengthReading(
        rs_percentile=rank,
        universe_percentile=universe_percentile,
        near_rs_high=near_high,
        new_rs_high_before_price=before,
        new_rs_high_with_price=rs_high_at_breakout and price_high_at_breakout,
        improving_after_breakout=improving,
        deteriorating_despite_breakout=deteriorating,
    )


@dataclass(frozen=True, slots=True)
class SectorReading:
    """Sector behaviour around the breakout, with its provenance attached."""

    strength_score: float | None = None
    trend: str = ""
    participation: float | None = None
    #: Sector return over the benchmark's, over the same lookback.
    relative_performance: float | None = None
    source: SectorSourceKind = SectorSourceKind.UNKNOWN
    as_of: dt.date | None = None
    available: bool = True
    unavailable_reason: str = ""

    def score(self) -> float | None:
        if not self.available:
            return None
        if self.strength_score is not None:
            return self.strength_score
        if self.relative_performance is not None:
            return ramp_score(self.relative_performance, zero_at=-0.10, full_at=0.10)
        return None

    def to_measurements(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, value in (
            ("sector_strength", self.strength_score),
            ("sector_participation", self.participation),
            ("sector_relative_performance", self.relative_performance),
        ):
            if value is not None:
                out[name] = value
        score = self.score()
        if score is not None:
            out["sector_score"] = score
        return out


#: Regime scores. Deliberately flat: the gap between BULL_TRENDING and NEUTRAL
#: is not four times the gap between NEUTRAL and BEAR_CHOPPY, and pretending to
#: that precision would give the smallest-weighted component in the engine its
#: most opinionated curve.
REGIME_SCORES: dict[MarketRegime, float] = {
    MarketRegime.BULL_TRENDING: 90.0,
    MarketRegime.BULL_CHOPPY: 70.0,
    MarketRegime.NEUTRAL: 50.0,
    MarketRegime.HIGH_VOLATILITY: 40.0,
    MarketRegime.BEAR_CHOPPY: 30.0,
    MarketRegime.BEAR_TRENDING: 15.0,
}


@dataclass(frozen=True, slots=True)
class MarketReading:
    """The regime the breakout occurred in. Stored, never used to reject.

    The score below exists so regime can carry a small weight in the composite,
    and the weight is deliberately small (6%). Phase 5's job is to characterise
    context; if a later phase concludes that breakouts in bear markets should be
    discarded entirely, it has the regime on every event and can do so — with
    its own reasoning, at its own layer.
    """

    regime: MarketRegime = MarketRegime.UNKNOWN
    regime_confidence: float | None = None
    available: bool = True
    unavailable_reason: str = ""

    def score(self) -> float | None:
        if not self.available or self.regime is MarketRegime.UNKNOWN:
            return None
        return REGIME_SCORES.get(self.regime, 50.0)

    def to_measurements(self) -> dict[str, float]:
        out: dict[str, float] = {}
        score = self.score()
        if score is not None:
            out["market_regime_score"] = score
        if self.regime_confidence is not None:
            out["market_regime_confidence"] = self.regime_confidence
        return out


@dataclass(frozen=True, slots=True)
class BreakoutContext:
    """Everything optional the engine may use and must work without.

    Every field is optional and every one has a stated unavailable path. A
    breakout evaluated with no benchmark, no sector and no regime is still a
    breakout — with three components marked unavailable, a coverage figure in
    the sixties, and a quality score renormalised over what was measured.
    """

    benchmark_closes: Sequence[float] | None = None
    sector_closes: Sequence[float] | None = None
    rs_universe_percentile: float | None = None
    sector: SectorReading = field(default_factory=SectorReading)
    market: MarketReading = field(default_factory=MarketReading)
    #: Sessions on which an earnings event is known to fall. An empty tuple with
    #: ``earnings_calendar_available`` false means "we do not know", which is a
    #: different claim from "there were none".
    earnings_sessions: Sequence[dt.date] = ()
    earnings_calendar_available: bool = False
    #: Sessions either side of the breakout within which earnings count as near.
    earnings_window: int = 3
    data_snapshot_digest: str = ""

    def aligned_with(self, length: int) -> bool:
        for series in (self.benchmark_closes, self.sector_closes):
            if series is not None and len(series) != length:
                return False
        return True

    def earnings_context_for(
        self, session: dt.date, previous_session: dt.date | None = None
    ) -> EarningsContext:
        """Classify the breakout's proximity to an earnings report.

        Returns ``UNKNOWN_EVENT_CONTEXT`` when no calendar was supplied, rather
        than ``NO_EARNINGS_NEARBY``. The two look identical in a report that
        treats missing as negative, and the whole point of the vocabulary is
        that Phase 6 can separate an earnings-driven breakout from an ordinary
        one — which it cannot do if half the "no earnings" rows actually mean
        "nobody checked".
        """
        if not self.earnings_calendar_available:
            return EarningsContext.UNKNOWN_EVENT_CONTEXT
        if previous_session is not None and any(
            previous_session < date <= session for date in self.earnings_sessions
        ):
            return EarningsContext.POST_EARNINGS_GAP
        window = dt.timedelta(days=self.earnings_window * 2)
        if any(abs((date - session).days) <= window.days for date in self.earnings_sessions):
            return EarningsContext.EARNINGS_EVENT_NEARBY
        return EarningsContext.NO_EARNINGS_NEARBY

    def to_payload(self) -> dict[str, Any]:
        return {
            "has_benchmark": self.benchmark_closes is not None,
            "has_sector": self.sector_closes is not None,
            "sector_source": str(self.sector.source),
            "sector_available": self.sector.available,
            "market_regime": str(self.market.regime),
            "earnings_calendar_available": self.earnings_calendar_available,
            "data_snapshot_digest": self.data_snapshot_digest,
        }


__all__ = [
    "REGIME_SCORES",
    "BreakoutContext",
    "MarketReading",
    "RelativeStrengthReading",
    "SectorReading",
    "SectorSourceKind",
    "measure_relative_strength",
]
