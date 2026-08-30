"""Pattern detectors, one module per pattern family.

One detector per pattern type, deliberately. They have genuinely different
parameters, they are validated separately, and a single class that finds twelve
patterns is a class nobody will ever refactor. Renaming one detector's output to
serve another pattern is the specific anti-pattern the architecture forbids --
which is why `BaseDetector.discover` and `BaseDetector.score` are abstract: a
subclass cannot accidentally inherit a flag's opinion of what makes a structure
good.
"""

from tradeit.patterns.detectors._base import BaseDetector, DetectionInputs, Structure
from tradeit.patterns.detectors.ascending_triangle import AscendingTriangleDetector
from tradeit.patterns.detectors.base_on_base import BaseOnBaseDetector
from tradeit.patterns.detectors.breakout_retest import BreakoutRetestDetector
from tradeit.patterns.detectors.bull_flag import BullFlagDetector
from tradeit.patterns.detectors.cup_handle import CupHandleDetector
from tradeit.patterns.detectors.double_bottom import DoubleBottomDetector
from tradeit.patterns.detectors.flat_base import FlatBaseDetector
from tradeit.patterns.detectors.high_tight_flag import HighTightFlagDetector
from tradeit.patterns.detectors.inverse_head_shoulders import InverseHeadShouldersDetector
from tradeit.patterns.detectors.pennant import PennantDetector
from tradeit.patterns.detectors.tight_consolidation import TightConsolidationDetector
from tradeit.patterns.detectors.vcp import VcpDetector

__all__ = [
    "AscendingTriangleDetector",
    "BaseDetector",
    "BaseOnBaseDetector",
    "BreakoutRetestDetector",
    "BullFlagDetector",
    "CupHandleDetector",
    "DetectionInputs",
    "DoubleBottomDetector",
    "FlatBaseDetector",
    "HighTightFlagDetector",
    "InverseHeadShouldersDetector",
    "PennantDetector",
    "Structure",
    "TightConsolidationDetector",
    "VcpDetector",
]
