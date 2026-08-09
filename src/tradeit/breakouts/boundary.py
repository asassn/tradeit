"""The level a breakout is measured against, and the zone around it.

Two commitments live here, and both are causality controls rather than
conveniences.

**The boundary is frozen when the event opens.** A breakout event snapshots the
pattern's resistance — level, slope, touch count, confidence, the ATR that was
current — and every later measurement is taken against that snapshot. The
alternative is to re-read the pattern's boundary each session, which sounds
harmless and is not: the pattern layer legitimately refines its levels as new
touches accumulate, so an event evaluated against a live boundary would be
judged against a level that partly reflects the breakout it is judging. That is
resistance optimised with post-breakout bars, which the brief forbids in item 1
and which no test downstream could detect after the fact.

**The zone is derived, never chosen after the outcome.** ``tolerance_pct``
comes from configuration, the instrument's ATR and the boundary's own
confidence, all of which are knowable at the moment the event opens. Nothing in
this module can see a future price, and the type carries no field that would let
one in.

Three prices, and the difference between them is the whole point:

* **nominal** — where the pattern says resistance is.
* **zone** — nominal ± tolerance. Inside it, price is *at* the level; the
  engine will not claim a break or a failure from a print in here.
* **threshold** — the top of the zone. A close above this is a qualifying
  close; a close at nominal + one tick is not.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tradeit.breakouts.config import ToleranceConfig
from tradeit.errors import ConfigError
from tradeit.patterns.base import Boundary


class BoundaryKind(StrEnum):
    """Where the level a breakout was measured against came from.

    The distinction is about *provenance*, not quality. An experimental level
    may be perfectly sensible and a structural one may be marginal; what the tag
    guarantees is that a consumer can tell which population a row belongs to
    without inferring it from the confidence score.
    """

    #: Derived by a Phase 4 detector from causally-confirmed geometry, carried
    #: with its pattern identity. The only kind the production monitor accepts.
    STRUCTURAL_PATTERN_BOUNDARY = "structural_pattern_boundary"
    #: A level supplied by a human — a research note, a chart annotation. Real,
    #: and not a detected structure.
    MANUAL_BOUNDARY = "manual_boundary"
    #: A level produced by an experimental or unreleased method. Kept separate
    #: from MANUAL so a research sweep is not mistaken for an analyst's opinion.
    EXPERIMENTAL_BOUNDARY = "experimental_boundary"
    #: Anything else, including imported levels of unknown origin. Present so
    #: that "we do not know where this came from" is expressible; a row that
    #: cannot say its provenance must not be able to claim a good one.
    OTHER = "other"

    @property
    def is_production_eligible(self) -> bool:
        """Whether the production monitor may open an event against it."""
        return self is BoundaryKind.STRUCTURAL_PATTERN_BOUNDARY

    @property
    def is_research_only(self) -> bool:
        return not self.is_production_eligible


def tolerance_for(
    *,
    level: float,
    atr: float | None,
    confidence: float,
    config: ToleranceConfig,
) -> float:
    """Fractional half-width of the zone around ``level``.

    The largest of a percentage floor and an ATR multiple, widened for a
    low-confidence boundary, then capped. Taking the largest rather than the sum
    is deliberate: the two terms are alternative expressions of the same
    uncertainty, and adding them would double-count a volatile penny stock.

    ``atr`` may be ``None`` — a very short series, or a level carried from a
    timeframe whose ATR has not warmed up. The percentage floor then stands
    alone, which is a weaker basis for the zone and is why the caller records
    the ATR alongside it rather than discarding it.
    """
    if level <= 0:
        raise ConfigError(f"boundary level {level} must be positive")
    if not 0.0 <= confidence <= 100.0:
        raise ConfigError(f"boundary confidence {confidence} outside [0, 100]")

    from_atr = 0.0 if atr is None or atr <= 0 else config.atr_multiple * atr / level
    base = max(config.min_tolerance_pct, from_atr)

    # Linear from ``low_confidence_widening`` at confidence 0 to 1.0 at 100.
    widening = config.low_confidence_widening - (
        (config.low_confidence_widening - 1.0) * confidence / 100.0
    )
    return min(config.max_tolerance_pct, base * widening)


@dataclass(frozen=True, slots=True)
class BreakoutBoundary:
    """A structural level plus its zone, as known when the event opened.

    ``anchor_date`` and ``slope_per_session`` carry sloped boundaries — an
    ascending triangle's rising lows, a pennant's converging upper line — so
    ``level_on`` evaluates the line rather than assuming horizontality. The
    session offset is supplied by the caller because trading-session arithmetic
    is the caller's knowledge, exactly as in :class:`~tradeit.patterns.base.Boundary`.
    """

    nominal: float
    anchor_date: dt.date
    tolerance_pct: float
    #: 0-100, from the pattern's boundary. Drives the zone width and feeds the
    #: BOUNDARY_QUALITY component of the breakout score.
    confidence: float
    #: How the pattern derived the level: "swing_highs", "horizontal_cluster",
    #: "regression_channel", "single_extreme".
    method: str = "unknown"
    touch_count: int = 0
    slope_per_session: float = 0.0
    #: The ATR current when the event opened, in price units. Every ATR-relative
    #: measurement on this event uses *this* number, so that a later volatility
    #: change cannot restate how far through the level the original bar closed.
    atr_at_open: float | None = None
    #: Identity of the pattern whose boundary this is. Empty for a boundary that
    #: is not attached to a pattern, which the engine permits and marks.
    pattern_key: str = ""
    pattern_type: str = ""
    pattern_quality: float = 0.0
    #: Where this level came from. A crossing of a level somebody typed into a
    #: notebook and a breakout of a causally-derived structure are both real
    #: observations and are not the same object; the production monitor accepts
    #: only the structural kind, and the tag travels into storage so a query can
    #: separate the populations. See ADR-0025.
    kind: BoundaryKind = BoundaryKind.OTHER

    def __post_init__(self) -> None:
        if self.nominal <= 0:
            raise ConfigError(f"boundary level {self.nominal} must be positive")
        if not 0.0 < self.tolerance_pct < 0.5:
            raise ConfigError(f"tolerance {self.tolerance_pct} outside (0, 0.5)")
        if not 0.0 <= self.confidence <= 100.0:
            raise ConfigError(f"boundary confidence {self.confidence} outside [0, 100]")
        if self.touch_count < 0:
            raise ConfigError("touch_count cannot be negative")

    # -- the three prices ----------------------------------------------------

    def level_on(self, sessions_from_anchor: int = 0) -> float:
        """The nominal level on a session, following the slope if there is one."""
        if self.slope_per_session == 0.0:
            return self.nominal
        return self.nominal + self.slope_per_session * sessions_from_anchor

    def zone(self, sessions_from_anchor: int = 0) -> tuple[float, float]:
        level = self.level_on(sessions_from_anchor)
        return level * (1.0 - self.tolerance_pct), level * (1.0 + self.tolerance_pct)

    def threshold(self, sessions_from_anchor: int = 0) -> float:
        """The price a close must exceed to qualify as a breakout close."""
        return self.level_on(sessions_from_anchor) * (1.0 + self.tolerance_pct)

    def floor(self, sessions_from_anchor: int = 0) -> float:
        """The bottom of the zone. Below this, price is back inside."""
        return self.level_on(sessions_from_anchor) * (1.0 - self.tolerance_pct)

    def contains(self, price: float, sessions_from_anchor: int = 0) -> bool:
        low, high = self.zone(sessions_from_anchor)
        return low <= price <= high

    def clears(self, price: float, sessions_from_anchor: int = 0) -> bool:
        return price > self.threshold(sessions_from_anchor)

    # -- distances -----------------------------------------------------------

    def distance_pct(self, price: float, sessions_from_anchor: int = 0) -> float:
        """Signed distance from the nominal level, as a fraction.

        Positive above. Reported from the *nominal* level rather than the zone
        edge because it is a description of where price is, not a judgement
        about whether it has broken out — the judgement uses the threshold.
        """
        level = self.level_on(sessions_from_anchor)
        return (price - level) / level

    def distance_atr(self, price: float, sessions_from_anchor: int = 0) -> float | None:
        """Signed distance in ATR units, or ``None`` when ATR is unknown.

        ``None`` rather than a fallback to percent. A silent unit switch is how
        a threshold expressed in ATR ends up compared against a percentage, and
        the resulting number looks entirely plausible.
        """
        if self.atr_at_open is None or self.atr_at_open <= 0:
            return None
        return (price - self.level_on(sessions_from_anchor)) / self.atr_at_open

    def penetration_atr(self, price: float, sessions_from_anchor: int = 0) -> float | None:
        """How far above the *threshold* price sits, in ATR.

        Measured from the threshold rather than the nominal level, so that
        clearing the ambiguity zone is worth zero and everything above it is
        genuine penetration. Negative when price has not cleared the zone.
        """
        if self.atr_at_open is None or self.atr_at_open <= 0:
            return None
        return (price - self.threshold(sessions_from_anchor)) / self.atr_at_open

    # -- serialisation -------------------------------------------------------

    @property
    def is_horizontal(self) -> bool:
        return self.slope_per_session == 0.0

    @property
    def is_attached(self) -> bool:
        """Whether this boundary came from a detected pattern.

        Item 1 of the brief requires attachment "wherever applicable". An
        unattached boundary is permitted — a horizontal level from prior highs
        is a real structure — but it is marked, because a breakout of a level
        nobody's detector claimed is a weaker object and the dataset must be
        able to separate the two populations.
        """
        return bool(self.pattern_key)

    @property
    def is_production_eligible(self) -> bool:
        """Whether the production monitor may open an event against this level.

        Both conditions, not either: the kind must be structural *and* a pattern
        identity must actually be present. A boundary tagged structural with no
        pattern key is a mislabelled row, and trusting the tag alone would let
        one bad construction call put research levels into the production
        population.
        """
        return self.kind.is_production_eligible and self.is_attached

    def to_payload(self) -> dict[str, Any]:
        return {
            "nominal": round(self.nominal, 6),
            "anchor_date": self.anchor_date.isoformat(),
            "tolerance_pct": round(self.tolerance_pct, 8),
            "confidence": round(self.confidence, 6),
            "method": self.method,
            "touch_count": self.touch_count,
            "slope_per_session": round(self.slope_per_session, 8),
            "atr_at_open": None if self.atr_at_open is None else round(self.atr_at_open, 6),
            "pattern_key": self.pattern_key,
            "pattern_type": self.pattern_type,
            "pattern_quality": round(self.pattern_quality, 6),
            "kind": str(self.kind),
        }


def boundary_from_pattern(
    boundary: Boundary,
    *,
    atr: float | None,
    config: ToleranceConfig,
    pattern_key: str = "",
    pattern_type: str = "",
    pattern_quality: float = 0.0,
    kind: BoundaryKind = BoundaryKind.STRUCTURAL_PATTERN_BOUNDARY,
) -> BreakoutBoundary:
    """Freeze a pattern's resistance into a breakout boundary.

    Rejects a support line outright. A breakout engine handed a support boundary
    would happily compute "penetration above support", producing numbers that
    are individually plausible and collectively meaningless — the kind of defect
    that survives review because every intermediate value looks fine.
    """
    if boundary.kind != "resistance":
        raise ConfigError(
            f"a breakout boundary must be resistance, got {boundary.kind!r}: "
            "measuring penetration above a support line yields plausible numbers "
            "about nothing"
        )
    tolerance = tolerance_for(
        level=boundary.level,
        atr=atr,
        confidence=boundary.confidence,
        config=config,
    )
    return BreakoutBoundary(
        nominal=boundary.level,
        anchor_date=boundary.anchor_date,
        tolerance_pct=tolerance,
        confidence=boundary.confidence,
        method=boundary.method,
        touch_count=boundary.touch_count,
        slope_per_session=boundary.slope_per_session,
        atr_at_open=atr,
        pattern_key=pattern_key,
        pattern_type=pattern_type,
        pattern_quality=pattern_quality,
        kind=kind,
    )


def manual_boundary(
    *,
    level: float,
    anchor_date: dt.date,
    atr: float | None,
    config: ToleranceConfig,
    confidence: float = 0.0,
    touches: int = 0,
    kind: BoundaryKind = BoundaryKind.MANUAL_BOUNDARY,
) -> BreakoutBoundary:
    """A level supplied by a human or an experiment, tagged as such.

    Supported deliberately: research needs to ask "what would the engine say
    about this level?" without pretending the level is a detected structure. The
    default confidence is zero and the default touch count is zero, because a
    level nobody derived has no touches anyone counted — and both feed the
    confidence score, which is where the difference surfaces.

    Refuses to be tagged structural. The one thing this function must not do is
    let a research level enter the production population, and a keyword argument
    is exactly how that would happen.
    """
    if kind.is_production_eligible:
        raise ConfigError(
            f"{kind} is the production kind and is reserved for boundaries derived "
            "by a detector from causally-confirmed geometry. Use "
            "boundary_from_pattern for those; a manual level tagged structural "
            "would enter the production population indistinguishably."
        )
    return BreakoutBoundary(
        nominal=level,
        anchor_date=anchor_date,
        tolerance_pct=tolerance_for(level=level, atr=atr, confidence=confidence, config=config),
        confidence=confidence,
        method="supplied",
        touch_count=touches,
        atr_at_open=atr,
        kind=kind,
    )


__all__ = [
    "BoundaryKind",
    "BreakoutBoundary",
    "boundary_from_pattern",
    "manual_boundary",
    "tolerance_for",
]
