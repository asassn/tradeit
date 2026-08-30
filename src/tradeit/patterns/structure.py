"""Resistance, support, and the shape of a consolidation.

Shared structural primitives. Every detector needs to answer "what level must
price clear?" and "where does this structure fail?", and answering them twelve
different ways would produce twelve subtly different notions of resistance in
one system.

**Resistance is derived only from confirmed swings** (see
:mod:`tradeit.patterns.swings`). The one exception is
:func:`single_extreme_resistance`, which uses the highest bar in a window and
reports ``method="single_extreme"`` with low confidence — appropriate for a
freshly-formed flag with no repeated tests yet, and deliberately labelled so it
is never mistaken for a level the market has actually respected.

**A level is not a level until price respects it.** A high touched once is the
highest bar so far; a level touched four times is where supply lives. Both are
returned, distinguished by ``touches`` and ``confidence``, because a detector
that treats them identically will report a first-day flag as confidently as a
month-old base.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError
from tradeit.patterns.base import Boundary, PricePoint
from tradeit.patterns.swings import Swing, SwingKind, touches_of_level


@dataclass(frozen=True, slots=True)
class LineFit:
    """A least-squares line through dated points, with its own goodness.

    ``r_squared`` is what stops a "trendline" being drawn through noise. Two
    points always fit perfectly and mean nothing; five points with r² of 0.15
    are not a line, and a detector that reports them as an upper channel
    boundary is inventing geometry.
    """

    slope_per_session: float
    intercept: float
    r_squared: float
    anchor_index: int
    point_count: int

    def value_at(self, index: int) -> float:
        return self.intercept + self.slope_per_session * (index - self.anchor_index)


def fit_line(indices: Sequence[int], prices: Sequence[float], *, anchor_index: int) -> LineFit:
    """Least-squares fit, with r² reported honestly for degenerate cases.

    Two points get ``r_squared = 0.0`` rather than 1.0. They *do* fit a line
    perfectly, and reporting that as a perfect fit would let any two highs
    masquerade as a channel boundary — the number is meant to express "the data
    supports a line", and two points support nothing.
    """
    if len(indices) != len(prices):
        raise ConfigError("indices and prices must be the same length")
    if len(indices) < 2:
        raise ConfigError("a line needs at least two points")

    x = np.asarray(indices, dtype=np.float64) - anchor_index
    y = np.asarray(prices, dtype=np.float64)

    if len(indices) == 2:
        slope = float((y[1] - y[0]) / (x[1] - x[0])) if x[1] != x[0] else 0.0
        return LineFit(slope, float(y[0] - slope * x[0]), 0.0, anchor_index, 2)

    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    residual = float(np.sum((y - predicted) ** 2))
    total = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - residual / total if total > 0 else 0.0
    return LineFit(float(slope), float(intercept), max(0.0, r_squared), anchor_index, len(indices))


def horizontal_resistance(
    bars: Sequence[OhlcvBar],
    swings: Sequence[Swing],
    *,
    start_index: int,
    end_index: int,
    tolerance_pct: float = 0.015,
    min_touches: int = 2,
) -> Boundary | None:
    """A flat level where several swing highs cluster.

    Clusters swing highs within ``tolerance_pct`` of each other and picks the
    cluster with the most members, breaking ties on the higher level. Preferring
    the *higher* of two equally-tested clusters is deliberate: resistance is the
    level price must clear, and choosing a lower one would declare a breakout
    while supply above is untouched.
    """
    window = [s for s in swings if start_index <= s.index <= end_index]
    if len(window) < min_touches:
        return None

    best: tuple[list[Swing], float] | None = None
    for anchor in window:
        cluster = [s for s in window if abs(s.price - anchor.price) <= anchor.price * tolerance_pct]
        if len(cluster) < min_touches:
            continue
        level = float(np.mean([s.price for s in cluster]))
        if best is None or (len(cluster), level) > (len(best[0]), best[1]):
            best = (cluster, level)

    if best is None:
        return None

    cluster, level = best
    touch_indices = touches_of_level(
        bars,
        level,
        start_index=start_index,
        end_index=end_index,
        tolerance_pct=tolerance_pct,
        kind=SwingKind.HIGH,
    )
    spread = float(np.std([s.price for s in cluster]) / level) if level > 0 else 1.0

    return Boundary(
        kind="resistance",
        method="horizontal_cluster",
        level=level,
        anchor_date=bars[cluster[0].index].session_date,
        slope_per_session=0.0,
        touches=tuple(PricePoint(bars[i].session_date, float(bars[i].high)) for i in touch_indices),
        start_date=bars[cluster[0].index].session_date,
        end_date=bars[cluster[-1].index].session_date,
        confidence=_boundary_confidence(len(touch_indices), spread_pct=spread, r_squared=None),
    )


def sloped_boundary(
    bars: Sequence[OhlcvBar],
    swings: Sequence[Swing],
    *,
    start_index: int,
    end_index: int,
    kind: str = "resistance",
    min_points: int = 2,
) -> Boundary | None:
    """A trendline through swing points, for channels and triangles.

    Returns ``None`` below ``min_points`` rather than fabricating a line from
    one pivot. The r² travels into the confidence, so a well-supported channel
    boundary and a line drawn through scatter are distinguishable downstream.
    """
    window = [s for s in swings if start_index <= s.index <= end_index]
    if len(window) < min_points:
        return None

    fit = fit_line(
        [s.index for s in window], [s.price for s in window], anchor_index=window[0].index
    )
    swing_kind = SwingKind.HIGH if kind == "resistance" else SwingKind.LOW

    return Boundary(
        kind=kind,
        method="regression_channel",
        level=fit.value_at(window[0].index),
        anchor_date=bars[window[0].index].session_date,
        slope_per_session=fit.slope_per_session,
        touches=tuple(
            PricePoint(
                bars[s.index].session_date,
                float(bars[s.index].high if swing_kind is SwingKind.HIGH else bars[s.index].low),
            )
            for s in window
        ),
        start_date=bars[window[0].index].session_date,
        end_date=bars[window[-1].index].session_date,
        confidence=_boundary_confidence(len(window), spread_pct=None, r_squared=fit.r_squared),
    )


def single_extreme_resistance(
    bars: Sequence[OhlcvBar], *, start_index: int, end_index: int
) -> Boundary | None:
    """The highest bar in the window. Honest, weak, and labelled as such.

    A new consolidation has no repeated tests yet, and refusing to name a
    resistance level until it does would leave every young pattern without the
    number that defines it. So this exists — with ``confidence`` capped low and
    ``method="single_extreme"``, so that nothing downstream mistakes "the
    highest bar so far" for "a level the market has respected".
    """
    lo, hi = max(0, start_index), min(len(bars) - 1, end_index)
    if hi < lo:
        return None

    window = bars[lo : hi + 1]
    peak = max(window, key=lambda b: b.high)
    return Boundary(
        kind="resistance",
        method="single_extreme",
        level=float(peak.high),
        anchor_date=peak.session_date,
        touches=(PricePoint(peak.session_date, float(peak.high)),),
        start_date=window[0].session_date,
        end_date=window[-1].session_date,
        confidence=25.0,
    )


def structural_support(
    bars: Sequence[OhlcvBar],
    swings: Sequence[Swing],
    *,
    start_index: int,
    end_index: int,
    tolerance_pct: float = 0.015,
) -> Boundary | None:
    """Where the consolidation has held.

    Prefers a cluster of confirmed swing lows; falls back to the lowest low in
    the window. The fallback is labelled ``single_extreme`` and carries low
    confidence for the same reason as its resistance counterpart.
    """
    window = [s for s in swings if start_index <= s.index <= end_index]

    if len(window) >= 2:
        best: tuple[list[Swing], float] | None = None
        for anchor in window:
            cluster = [
                s for s in window if abs(s.price - anchor.price) <= anchor.price * tolerance_pct
            ]
            if len(cluster) < 2:
                continue
            level = float(np.mean([s.price for s in cluster]))
            # Ties break to the *lower* level: support is where the structure
            # fails, and an optimistic support level understates the risk.
            if best is None or (len(cluster), -level) > (len(best[0]), -best[1]):
                best = (cluster, level)

        if best is not None:
            cluster, level = best
            touch_indices = touches_of_level(
                bars,
                level,
                start_index=start_index,
                end_index=end_index,
                tolerance_pct=tolerance_pct,
                kind=SwingKind.LOW,
            )
            spread = float(np.std([s.price for s in cluster]) / level) if level > 0 else 1.0
            return Boundary(
                kind="support",
                method="horizontal_cluster",
                level=level,
                anchor_date=bars[cluster[0].index].session_date,
                touches=tuple(
                    PricePoint(bars[i].session_date, float(bars[i].low)) for i in touch_indices
                ),
                start_date=bars[cluster[0].index].session_date,
                end_date=bars[cluster[-1].index].session_date,
                confidence=_boundary_confidence(
                    len(touch_indices), spread_pct=spread, r_squared=None
                ),
            )

    lo, hi = max(0, start_index), min(len(bars) - 1, end_index)
    if hi < lo:
        return None
    trough = min(bars[lo : hi + 1], key=lambda b: b.low)
    return Boundary(
        kind="support",
        method="single_extreme",
        level=float(trough.low),
        anchor_date=trough.session_date,
        touches=(PricePoint(trough.session_date, float(trough.low)),),
        start_date=bars[lo].session_date,
        end_date=bars[hi].session_date,
        confidence=25.0,
    )


def _boundary_confidence(
    touches: int, *, spread_pct: float | None, r_squared: float | None
) -> float:
    """How much to believe a boundary: how often tested, how tightly.

    Touch count dominates because it is the thing that makes a level real.
    Three separate tests of one price is a market fact; a tight fit through two
    points is arithmetic.
    """
    touch_term = min(touches / 4.0, 1.0)
    if spread_pct is not None:
        # 1% scatter among clustered highs is tight; 3% is not a level.
        tightness = max(0.0, 1.0 - spread_pct / 0.03)
    elif r_squared is not None:
        tightness = max(0.0, min(1.0, r_squared))
    else:
        tightness = 0.5
    return float(min(100.0, (0.65 * touch_term + 0.35 * tightness) * 100.0))


# ---------------------------------------------------------------------------
# Consolidation shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsolidationShape:
    """Measured geometry of a sideways or drifting range.

    Every field is a measurement, not a judgement. Scoring happens in the
    detectors, where the preferred ranges differ: a bull flag wants a mild
    downward drift, a flat base wants no drift at all, and an ascending triangle
    wants rising lows. One shared measurement, three different opinions about
    what is good.
    """

    start_index: int
    end_index: int
    start_date: dt.date
    end_date: dt.date
    sessions: int
    high: float
    low: float
    #: (high - low) / high. Scale-free, so a $5 stock and a $500 stock compare.
    depth_pct: float
    #: Slope of a regression through the closes, as a fraction of price per
    #: session. Negative is a downward drift.
    slope_pct_per_session: float
    slope_r_squared: float
    #: Mean true range over the window as a fraction of price.
    mean_range_pct: float
    #: Later-half range over earlier-half range. Below 1 means contracting.
    range_contraction: float
    #: Later-half volume over earlier-half volume. Below 1 means drying up.
    volume_contraction: float
    #: Standard deviation of closes over their mean.
    close_dispersion: float

    @property
    def is_contracting(self) -> bool:
        return self.range_contraction < 1.0


def measure_consolidation(
    bars: Sequence[OhlcvBar], *, start_index: int, end_index: int
) -> ConsolidationShape | None:
    """Measure a window's shape without judging it."""
    lo, hi = max(0, start_index), min(len(bars) - 1, end_index)
    if hi - lo + 1 < 3:
        return None

    window = bars[lo : hi + 1]
    highs = np.array([float(b.high) for b in window])
    lows = np.array([float(b.low) for b in window])
    closes = np.array([float(b.close) for b in window])
    volumes = np.array([float(b.volume) for b in window])

    high, low = float(highs.max()), float(lows.min())
    depth = (high - low) / high if high > 0 else 0.0

    fit = fit_line(list(range(len(closes))), closes.tolist(), anchor_index=0)
    mean_close = float(closes.mean())
    slope_pct = fit.slope_per_session / mean_close if mean_close > 0 else 0.0

    ranges = highs - lows
    mean_range_pct = float((ranges / np.where(closes > 0, closes, np.nan)).mean())

    half = len(window) // 2
    early_range = float(ranges[:half].mean()) if half else 0.0
    late_range = float(ranges[half:].mean()) if half else 0.0
    range_contraction = late_range / early_range if early_range > 0 else 1.0

    early_volume = float(volumes[:half].mean()) if half else 0.0
    late_volume = float(volumes[half:].mean()) if half else 0.0
    volume_contraction = late_volume / early_volume if early_volume > 0 else 1.0

    dispersion = float(closes.std() / mean_close) if mean_close > 0 else 0.0

    return ConsolidationShape(
        start_index=lo,
        end_index=hi,
        start_date=window[0].session_date,
        end_date=window[-1].session_date,
        sessions=len(window),
        high=high,
        low=low,
        depth_pct=depth,
        slope_pct_per_session=slope_pct,
        slope_r_squared=fit.r_squared,
        mean_range_pct=mean_range_pct if np.isfinite(mean_range_pct) else 0.0,
        range_contraction=range_contraction,
        volume_contraction=volume_contraction,
        close_dispersion=dispersion,
    )
