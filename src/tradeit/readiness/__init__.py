"""Phase 12 — is this good enough to trust with money, and how would we know if it stopped?

The roadmap names the gap this fills precisely: the measurement contracts
already existed, and *"what does not exist is the framework that turns those
numbers into a decision"*.

``decay`` answers the second half of the question -- *how would we know if it
stopped?* -- and carries the minimum detectable shortfall with every verdict,
so a reassuring answer on too few trades cannot be mistaken for evidence.

``criteria`` is the list of what must be shown, with every threshold carrying
the written decision it came from. ``assess`` applies it: a verdict per
criterion, promotion as a conjunction, demotion as a disjunction, and a
threshold nobody approved blocking rather than being applied.

**This framework authorises nothing by itself.** A PROMOTE verdict for LIVE is a
statement that the evidence clears the declared bars; the ADR-0004 interlock
still requires a machine-local authorisation file, and the owner still decides.
"""

from tradeit.readiness.assess import (
    Decision,
    ReadinessReport,
    Verdict,
    VerdictOutcome,
    assess_demotion,
    assess_promotion,
)
from tradeit.readiness.criteria import (
    DEMOTION_CRITERIA,
    PROMOTION_CRITERIA,
    Comparison,
    Criterion,
    Threshold,
    ThresholdSource,
    criteria_for,
    demotion_criteria_for,
)
from tradeit.readiness.decay import (
    DecayAssessment,
    DecayVerdict,
    Expectation,
    assess_decay,
)

__all__ = [
    "DEMOTION_CRITERIA",
    "PROMOTION_CRITERIA",
    "Comparison",
    "Criterion",
    "DecayAssessment",
    "DecayVerdict",
    "Decision",
    "Expectation",
    "ReadinessReport",
    "Threshold",
    "ThresholdSource",
    "Verdict",
    "VerdictOutcome",
    "assess_decay",
    "assess_demotion",
    "assess_promotion",
    "criteria_for",
    "demotion_criteria_for",
]
