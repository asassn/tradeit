"""Driving Phase 4 and Phase 5 over an imported snapshot.

The detectors and the breakout engine existed and were tested against synthetic
corpora long before anything ran them over real bars. This package is the
missing middle: a causal bar feed and a driver that walks a snapshot session by
session, keeps identities alive, and persists what the two phases produce.
"""

from tradeit.scanning.feed import CausalFeed, FeedMode, SessionView
from tradeit.scanning.runner import (
    SCAN_DOES_NOT_PRODUCE,
    SCAN_PRODUCES,
    InstrumentScan,
    ScanOptions,
    ScanReport,
    SnapshotScanner,
)

__all__ = [
    "SCAN_DOES_NOT_PRODUCE",
    "SCAN_PRODUCES",
    "CausalFeed",
    "FeedMode",
    "InstrumentScan",
    "ScanOptions",
    "ScanReport",
    "SessionView",
    "SnapshotScanner",
]
