"""Causal chart-pattern recognition.

Phase 4. Answers "does this structure resemble a valid bullish pattern, and how
good is it?" — and deliberately nothing else. Breakout validity, fundamental
quality, opportunity scoring and position sizing all live downstream, and
nothing in this package can reach them.
"""

from tradeit.patterns.base import (
    Boundary,
    ComponentScore,
    Detector,
    Evidence,
    EvidenceKind,
    PatternCandidate,
    PatternContext,
    PatternGeometry,
    PatternInstance,
    PatternState,
    PricePoint,
)

__all__ = [
    "Boundary",
    "ComponentScore",
    "Detector",
    "Evidence",
    "EvidenceKind",
    "PatternCandidate",
    "PatternContext",
    "PatternGeometry",
    "PatternInstance",
    "PatternState",
    "PricePoint",
]
