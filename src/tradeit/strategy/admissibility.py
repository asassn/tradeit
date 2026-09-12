"""What claims a corpus's survivorship classification permits.

``STRATEGY_BUILDER.md`` §5 places one condition between ``VALIDATED`` and the
rungs above it — a version must run *"on a corpus whose classification permits
the claim"* — and the platform's standing rule says what that means today:

    No strategy result computed on ``research-01`` today is evidence of
    profitability. A backtest on it is missing the companies that failed, so a
    good result means the failures are absent, not that the strategy works.
    **Build the machinery on it; do not believe its numbers.** The rule lifts
    when the gate reaches ``MATERIALLY_SURVIVORSHIP_CORRECTED`` and a decision
    is recorded, and not before.

That distinction — *run* versus *believe* — is the whole of this module. A
version may enter ``BACKTESTING`` on a survivor-biased corpus, because building
and exercising the machinery is exactly what the corpus is for. It may not
**leave ``BACKTESTING`` upward**, because every rung above it asserts that the
results meant something.

**The line is transcribed, not chosen.** The threshold, the classification and
the sentence that lifts the rule are all written down already; encoding them is
transcription.

**Updated 2026-09-12, and the update is also a transcription.** This module
used to permit evidence for every class except ``SURVIVOR_BIASED``, because that
was the only class the gate could return: ``EDGAR_DELISTING_DENOMINATOR.md``
§7e measured the old denominator unreachable — perfect identity resolution
still landed near 23.8% against a 0.25 threshold. When the owner adopted §5's
denominator on 2026-09-12 the grade moved to
``PARTIALLY_SURVIVORSHIP_CORRECTED`` **on the same corpus, the same day, with no
new data** — and under the old code that change of arithmetic would by itself
have unlocked promotion above ``BACKTESTING``.

The owner kept the rule in force through that change. So the line now sits
where the rule says it does: evidence requires
``MATERIALLY_SURVIVORSHIP_CORRECTED`` or better. That is still not a finer
gradation of what each class *permits* — ``PARTIALLY`` and ``SURVIVOR_BIASED``
are treated alike, and the two classes above are treated alike — it is the same
binary line, moved to where the decision put it. 37.4% coverage means nearly two
thirds of the companies EDGAR shows exiting are still unpriced.

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

    Binary by design, with the line between the second and third classes since
    2026-09-12. See the module docstring: the move was a transcription of the
    owner's decision, not a new gradation, and the finer ones remain unwritten.
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
    if classification is SurvivorshipClass.PARTIALLY_SURVIVORSHIP_CORRECTED:
        return CorpusAdmissibility(
            classification=classification,
            permits_evidence=False,
            reason=(
                "the correction is real and partial: at the 0.25 boundary nearly three "
                "quarters of the dated exits are still unpriced, and a deficit that size "
                "changes conclusions. Machinery may be built and run on it, and its "
                "numbers may not be believed. See EDGAR_DELISTING_DENOMINATOR.md §7e, "
                "2026-09-12"
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
