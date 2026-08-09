"""The retest engine: price returning to a level it has already broken.

One rule dominates this module, and it is the reason retest analysis is usually
wrong when it is done casually:

    **A retest is evaluated against the original causal breakout level, never a
    level redefined after the event.**

Refitting the level through post-breakout bars is not a small optimisation. A
support line fitted to include the pullback low will pass through the pullback
low, so every retest holds, and the "retest success rate" computed from it is
approximately 100% by construction. The boundary this module reads is the one
frozen on :class:`~tradeit.breakouts.base.BreakoutEvent` when the event opened,
and there is no code path here that can replace it.

The second rule is nearly as important:

    **Not every undercut is a failure.**

Price that dips below a level it cleared four days ago and closes back above it
has *tested* the level, not broken it. What separates the two is how far, for
how long, and on what volume — and all three have to be volatility-aware,
because a fixed percentage calls the same behaviour a hold on a quiet name and a
failure on a fast one. ``max_undercut_atr`` is configured strictly below
``FailureConfig.decisive_close_atr`` (the engine config validates it), so an
ordinary retest can never trip the failure rule it is supposed to survive.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import replace

import numpy as np

from tradeit.breakouts.base import RetestRecord
from tradeit.breakouts.boundary import BreakoutBoundary
from tradeit.breakouts.config import RetestConfig
from tradeit.core.models import OhlcvBar
from tradeit.patterns.scoring import decay_score, ramp_score


def is_pullback(
    bar: OhlcvBar,
    *,
    breakout_close: float,
    atr: float | None,
    config: RetestConfig,
) -> bool:
    """Whether this bar counts as pulling back from the breakout.

    Measured from the breakout close rather than from the running high. Using
    the running high would make every advance followed by a normal day look like
    a pullback, which is how a retest engine ends up "detecting" retests during
    uptrends.
    """
    close = float(bar.close)
    if close >= breakout_close:
        return False
    if atr is None or atr <= 0:
        return close < breakout_close * 0.98
    return (breakout_close - close) / atr >= config.retest_trigger_atr


def build_retest(
    bars: Sequence[OhlcvBar],
    boundary: BreakoutBoundary,
    *,
    started_session: dt.date,
    breakout_volume: float,
    config: RetestConfig,
) -> RetestRecord:
    """Summarise a retest from the bars that constitute it.

    ``bars`` runs from the first pullback session to the session being
    evaluated. Every figure is computed from those bars and the frozen boundary;
    nothing consults a later bar, and the function has no argument that would
    let one in.
    """
    if not bars:
        raise ValueError("a retest needs at least one bar")

    level = boundary.nominal
    atr = boundary.atr_at_open

    lows = [float(b.low) for b in bars]
    low = min(lows)
    low_index = int(np.argmin(lows))
    low_session = bars[low_index].session_date

    undercut = max(0.0, level - low)
    max_undercut_atr = 0.0 if not atr else undercut / atr

    sessions_below = sum(1 for b in bars if float(b.close) < level)

    volume_ratio = (
        None
        if breakout_volume <= 0
        else float(np.mean([float(b.volume) for b in bars])) / breakout_volume
    )
    range_ratio = (
        None if not atr else float(np.mean([float(b.high) - float(b.low) for b in bars])) / atr
    )

    # Recovery closes are counted from the low onward. A close above the level
    # *before* the low is not a recovery — it is part of the decline.
    recovery_closes = sum(1 for b in bars[low_index:] if float(b.close) > level)
    recovered_session = None
    run = 0
    for bar in bars[low_index:]:
        if float(bar.close) > level:
            run += 1
            if run >= config.recovery_closes:
                recovered_session = bar.session_date
                break
        else:
            run = 0

    record = RetestRecord(
        started_session=started_session,
        low=low,
        low_session=low_session,
        max_undercut_atr=max_undercut_atr,
        sessions=len(bars),
        sessions_below_level=sessions_below,
        volume_ratio=volume_ratio,
        range_ratio=range_ratio,
        recovery_closes=recovery_closes,
        recovered_session=recovered_session,
    )
    return _with_quality(record, config=config)


def _with_quality(record: RetestRecord, *, config: RetestConfig) -> RetestRecord:
    return replace(record, quality=retest_quality(record, config=config))


def retest_quality(record: RetestRecord, *, config: RetestConfig) -> float:
    """RETEST_QUALITY_SCORE for a completed or in-progress retest.

    Four terms, and the weighting says what the engine believes a good retest
    looks like: it did not go far below the level (30%), it did so on
    contracting volume (25%) in a contracting range (15%), and price closed back
    above (30%).

    The undercut term is scored with a *floor* rather than to zero. A retest
    that dug 1.5 ATR below the level and still recovered is a poor retest, not a
    non-existent one, and zeroing the term would let the recovery weight carry
    a structure that clearly broke.
    """
    undercut_term = decay_score(
        record.max_undercut_atr,
        full_at=0.0,
        zero_at=config.max_undercut_atr,
        floor=10.0,
    )
    if record.sessions_below_level > config.max_sessions_below:
        undercut_term *= 0.5

    volume_term = (
        50.0
        if record.volume_ratio is None
        else decay_score(
            record.volume_ratio, full_at=config.volume_contraction_full, zero_at=1.5, floor=5.0
        )
    )
    range_term = (
        50.0
        if record.range_ratio is None
        else decay_score(
            record.range_ratio, full_at=config.range_contraction_full, zero_at=2.0, floor=5.0
        )
    )
    recovery_term = ramp_score(
        float(record.recovery_closes), zero_at=0.0, full_at=float(config.recovery_closes)
    )

    return (
        undercut_term * config.weight_undercut
        + volume_term * config.weight_volume
        + range_term * config.weight_range
        + recovery_term * config.weight_recovery
    )


def retest_failed(
    record: RetestRecord,
    *,
    config: RetestConfig,
) -> bool:
    """Whether the retest has broken the level rather than tested it.

    Two independent conditions, either sufficient: price went further below the
    level than the configured tolerance allows, or it spent more sessions below
    it than a test would. The second exists because a shallow but persistent
    drift below a level is a break that never produces a dramatic bar — the
    failure mode a purely depth-based rule misses entirely.
    """
    if record.max_undercut_atr > config.max_undercut_atr:
        return True
    return record.sessions_below_level > config.max_sessions_below


def retest_expired(record: RetestRecord, *, max_sessions: int) -> bool:
    """Whether the retest has taken so long it is no longer a retest.

    A pullback that has churned around the level for five weeks is a new
    consolidation. Continuing to call it a retest of a breakout from last month
    would attribute its eventual resolution to an event it has stopped being
    about. The limit lives in :class:`~tradeit.breakouts.config.ExpirationConfig`
    with the other clocks rather than here, so all of them are reviewable in one
    place.
    """
    return record.sessions > max_sessions


def measurements(record: RetestRecord) -> dict[str, float]:
    out = {
        "retest_sessions": float(record.sessions),
        "retest_sessions_below": float(record.sessions_below_level),
        "retest_max_undercut_atr": record.max_undercut_atr,
        "retest_recovery_closes": float(record.recovery_closes),
        "retest_quality_score": record.quality,
    }
    if record.volume_ratio is not None:
        out["retest_volume_ratio"] = record.volume_ratio
    if record.range_ratio is not None:
        out["retest_range_ratio"] = record.range_ratio
    return out


__all__ = [
    "build_retest",
    "is_pullback",
    "measurements",
    "retest_expired",
    "retest_failed",
    "retest_quality",
]
