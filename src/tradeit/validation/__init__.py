"""The empirical validation harness: what real data would tell us, once it exists.

Everything through Phase 5 was built and characterised on synthetic series. The
characterisation was worth doing — it found three real defects — but a random
walk is not a market, and the properties that matter most are exactly the ones a
generator cannot exercise.

This package is the machinery for the run that answers those questions, and it
is complete and executable before any real data arrives. That order is
deliberate: infrastructure written *after* the data lands is infrastructure
written by someone who has already seen the answer.

Two things it refuses to do. It will not report a check that could not run as a
pass — BLOCKED is a separate status, counted first in every conclusion. And it
will not compute a performance statistic;
:data:`~tradeit.validation.checks.FORBIDDEN_MEASURES` is enforced over every
result the runner collects, because the moment a hit rate exists, thresholds
start getting tuned against it.
"""

from tradeit.validation.checks import (
    FORBIDDEN_MEASURES,
    CheckClock,
    CheckResult,
    CheckStatus,
    Phase,
    ValidationCheck,
)
from tradeit.validation.context import ValidationContext, load_context
from tradeit.validation.data_checks import data_checks
from tradeit.validation.phase_checks import phase_checks
from tradeit.validation.runner import (
    NOT_MEASURED,
    ValidationRun,
    all_checks,
    run_validation,
)

__all__ = [
    "FORBIDDEN_MEASURES",
    "NOT_MEASURED",
    "CheckClock",
    "CheckResult",
    "CheckStatus",
    "Phase",
    "ValidationCheck",
    "ValidationContext",
    "ValidationRun",
    "all_checks",
    "data_checks",
    "load_context",
    "phase_checks",
    "run_validation",
]
