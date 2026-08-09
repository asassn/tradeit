"""Breakout detection and confirmation.

Phase 5. Answers "what is price doing relative to an established structural
boundary, and how convincing is that behaviour?" — and deliberately nothing
else. Whether to buy, how much, where to stop and what it is worth all live
downstream, and nothing in this package can reach them or express them.
"""

from tradeit.breakouts.base import (
    BreakoutEvent,
    BreakoutObservation,
    ConfirmationPath,
    EarningsContext,
    GapClass,
    RetestRecord,
    event_identity,
)
from tradeit.breakouts.boundary import BreakoutBoundary, boundary_from_pattern
from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.context import BreakoutContext
from tradeit.breakouts.engine import BreakoutEngine, SessionInputs
from tradeit.breakouts.lifecycle import BreakoutState, BreakoutTransition, TransitionReason
from tradeit.breakouts.monitor import BreakoutMonitor, MonitorResult
from tradeit.breakouts.volume import VolumeReading

__all__ = [
    "BreakoutBoundary",
    "BreakoutContext",
    "BreakoutEngine",
    "BreakoutEngineConfig",
    "BreakoutEvent",
    "BreakoutMonitor",
    "BreakoutObservation",
    "BreakoutState",
    "BreakoutTransition",
    "ConfirmationPath",
    "EarningsContext",
    "GapClass",
    "MonitorResult",
    "RetestRecord",
    "SessionInputs",
    "TransitionReason",
    "VolumeReading",
    "boundary_from_pattern",
    "event_identity",
]
