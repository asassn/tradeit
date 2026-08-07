"""Pattern detectors, one module per pattern family.

One detector per pattern type, deliberately. They have genuinely different
parameters, they are validated separately, and a single class that finds twelve
patterns is a class nobody will ever refactor. Renaming one detector's output to
serve another pattern is the specific anti-pattern the architecture forbids.
"""

from tradeit.patterns.detectors.bull_flag import BullFlagDetector

__all__ = ["BullFlagDetector"]
