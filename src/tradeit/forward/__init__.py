"""The forward record — evidence collected going forward rather than reconstructed.

The owner's decision of 2026-10-20: no further spending on history, research
turns forward. See `docs/FORWARD_EVIDENCE_2026-10-20.md` for why that is the
statistically stronger position rather than a compromise.
"""

from tradeit.forward.capture import (
    CaptureReport,
    CaptureSource,
    NoCredential,
    capture_forward,
)

__all__ = ["CaptureReport", "CaptureSource", "NoCredential", "capture_forward"]
