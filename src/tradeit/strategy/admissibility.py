"""What claims a corpus's survivorship classification permits.

``STRATEGY_BUILDER.md`` §5 places one condition between ``VALIDATED`` and the
rungs above it — a version must run *"on a corpus whose classification permits
the claim"* — and the platform's standing rule says what that means today:

    No strategy result computed on ``research-01`` today is evidence of
    profitability. The survivorship gate returns ``SURVIVOR_BIASED``. A
    backtest on it is missing the companies that failed, so a good result means
    the failures are absent, not that the strategy works. **Build the machinery
    on it; do not believe its numbers.** The rule lifts when the gate says
    something other than ``SURVIVOR_BIASED``, and not before.

That distinction — *run* versus *believe* — is the whole of this module. A
version may enter ``BACKTESTING`` on a survivor-biased corpus, because building
and exercising the machinery is exactly what the corpus is for. It may not
**leave ``BACKTESTING`` upward**, because every rung above it asserts that the
results meant something.

**The line is transcribed, not chosen.** The threshold, the classification and
the sentence that lifts the rule are all written down already; encoding them is
transcription. What is deliberately *not* here is any finer gradation — nothing
says that ``PARTIALLY_SURVIVORSHIP_CORRECTED`` permits paper trading but not
capital, and inventing that distinction would be exactly the "backtesting
assumption" that needs a scoped proposition rather than a commit. All three
non-biased classes are treated alike until somebody decides otherwise on
purpose.

A second thing left out on purpose: this asks nothing about *which* corpus a
run used. Binding a version to a corpus is persistence, which does not exist
yet; a caller passes the classification it actually measured, and passing a
stale one is the failure this cannot catch.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tradeit.edgar.denominator import SurvivorshipClass
from tradeit.strategy.lifecycle import (
    LADDER,
    LiveAuthorisation,
    StrategyState,
    StrategyVersion,
    is_promotion,
)

__all__ = [
    "CorpusAdmissibility",
    "EvidenceNotPermitted",
    "admissibility",
    "claims_evidence",
    "promote_on_corpus",
]

#: The first rung whose promotion asserts that results meant something.
#: Entering ``BACKTESTING`` asserts nothing; leaving it upward asserts
#: everything.
_FIRST_EVIDENCE_RUNG = StrategyState.OUT_OF_SAMPLE_TESTING


class EvidenceNotPermitted(ValueError):
    """A promotion rested on results the corpus cannot support."""

    def __init__(
        self, admissible: CorpusAdmissibility, frm: StrategyState, to: StrategyState
    ) -> None:
        super().__init__(
            f"{frm.value} -> {to.value} treats a result as evidence, and the corpus "
            f"cannot support that: {admissible.reason}"
        )
        self.admissibility = admissible


@dataclass(frozen=True, slots=True)
class CorpusAdmissibility:
    """Whether a corpus's numbers may be believed, and why."""

    classification: SurvivorshipClass
    permits_evidence: bool
    reason: str
    #: When the classification was measured. A classification is a measurement
    #: with an age, not a property, and one taken before the last import may
    #: no longer hold.
    measured_at: dt.datetime | None = None


def admissibility(
    classification: SurvivorshipClass, *, measured_at: dt.datetime | None = None
) -> CorpusAdmissibility:
    """Map a classification to what it permits.

    Binary by design. See the module docstring: the finer gradations are
    unwritten, and writing them here would be deciding them.
    """
    if classification is SurvivorshipClass.SURVIVOR_BIASED:
        return CorpusAdmissibility(
            classification=classification,
            permits_evidence=False,
            reason=(
                "the corpus is survivor-biased; a good result means the failures are "
                "absent, not that the strategy works. Machinery may be built and run "
                "on it, and its numbers may not be believed"
            ),
            measured_at=measured_at,
        )
    return CorpusAdmissibility(
        classification=classification,
        permits_evidence=True,
        reason=f"the gate returns {classification.value}, which is not survivor_biased",
        measured_at=measured_at,
    )


def claims_evidence(frm: StrategyState, to: StrategyState) -> bool:
    """Does this promotion assert that a result meant something?

    ``VALIDATED -> BACKTESTING`` does not: it says a version runs, which is a
    fact about the code. Everything above it does.
    """
    if not is_promotion(frm, to):
        return False
    return LADDER.index(to) >= LADDER.index(_FIRST_EVIDENCE_RUNG)


def promote_on_corpus(
    version: StrategyVersion,
    to: StrategyState,
    *,
    citation: str,
    classification: SurvivorshipClass,
    measured_at: dt.datetime | None = None,
    now: dt.datetime | None = None,
    live_authorisation: LiveAuthorisation | None = None,
) -> StrategyVersion:
    """:meth:`StrategyVersion.transition`, with the corpus condition applied.

    The corpus check runs *before* the lifecycle's own rules so the refusal a
    caller sees names the real obstacle. A version told "you skipped a rung"
    when the actual problem is that nothing it has measured can be believed
    would go and fix the wrong thing.
    """
    admissible = admissibility(classification, measured_at=measured_at)
    if claims_evidence(version.state, to) and not admissible.permits_evidence:
        raise EvidenceNotPermitted(admissible, version.state, to)
    return version.transition(to, citation=citation, now=now, live_authorisation=live_authorisation)
