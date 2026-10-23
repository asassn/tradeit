"""Turning measurements into a promotion decision, and refusing to guess.

:mod:`tradeit.readiness.criteria` is the list. This applies it to evidence and
returns a verdict per criterion plus one decision.

Three states, not two
---------------------

A criterion is ``PASS``, ``FAIL`` or ``UNRESOLVED``, and the third is the one
that makes the framework usable:

* **A missing measurement is UNRESOLVED, never FAIL.** They are different
  findings — *we measured it and it was not good enough* versus *nobody
  measured it* — and collapsing them would let an unmeasured strategy look
  merely unlucky. It would also make the fix look like "improve the strategy"
  when the fix is "run the test".
* **A threshold nobody approved is UNRESOLVED too**, with its own reason. The
  criterion is declared and the measurement may even exist; what is missing is
  the number, and judging against an invented one is the thing the roadmap
  forbids.

**Promotion requires every applicable criterion to PASS.** An ``UNRESOLVED``
blocks exactly as an ``FAIL`` does — fail closed — but the report says which,
because the work needed is different.

**Demotion fires on any one criterion.** The asymmetry is the roadmap's:
promotion is a conjunction because all the evidence must hold; demotion is a
disjunction because one broken thing is sufficient reason to stop. A framework
needing unanimity to demote would keep a decayed strategy running.

What this module will not do
----------------------------

It does not compute metrics. It is handed an :class:`Evidence` mapping and
reads named fields from it, so that *what was measured* and *whether it was good
enough* stay separable — and so a report can name the measurement that was
absent rather than reporting an opaque failure. A module that computed its own
inputs could also quietly choose them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from tradeit.readiness.criteria import (
    Criterion,
    ThresholdSource,
    criteria_for,
    demotion_criteria_for,
)
from tradeit.strategy.lifecycle import LADDER, StrategyState

__all__ = [
    "Decision",
    "ReadinessReport",
    "Verdict",
    "VerdictOutcome",
    "assess_demotion",
    "assess_promotion",
    "hurdle_evidence",
]


class VerdictOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    #: Measured nothing, or judged against nothing. Blocks like a failure and
    #: is reported differently, because the work it implies is different.
    UNRESOLVED = "unresolved"


class Decision(StrEnum):
    PROMOTE = "promote"
    HOLD = "hold"
    DEMOTE = "demote"


@dataclass(frozen=True, slots=True)
class Verdict:
    """One criterion's answer, with the number it was given."""

    criterion: Criterion
    outcome: VerdictOutcome
    measured: float | None
    reason: str = ""

    def describe(self) -> str:
        shown = "—" if self.measured is None else f"{self.measured:g}"
        bar = "?" if not self.criterion.threshold.usable else f"{self.criterion.threshold.value:g}"
        arrow = "≥" if self.criterion.comparison == "at_least" else "≤"
        tail = f" ({self.reason})" if self.reason else ""
        return f"{self.outcome.upper():<10} {self.criterion.name}: {shown} {arrow} {bar}{tail}"


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Every criterion's verdict, and the one decision that follows."""

    target: StrategyState
    decision: Decision
    verdicts: tuple[Verdict, ...]

    def _of(self, outcome: VerdictOutcome) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.outcome is outcome)

    @property
    def failures(self) -> tuple[Verdict, ...]:
        return self._of(VerdictOutcome.FAIL)

    @property
    def unresolved(self) -> tuple[Verdict, ...]:
        return self._of(VerdictOutcome.UNRESOLVED)

    @property
    def passes(self) -> tuple[Verdict, ...]:
        return self._of(VerdictOutcome.PASS)

    @property
    def blocked_on_unapproved_thresholds(self) -> tuple[Verdict, ...]:
        """Criteria blocked because nobody has approved their number.

        Separated because it is the one blocking reason no amount of further
        measurement will clear. It needs a decision, not a run.
        """
        return tuple(
            verdict
            for verdict in self.unresolved
            if verdict.criterion.threshold.source is ThresholdSource.UNAPPROVED
        )

    def describe(self) -> str:
        lines = [
            f"readiness for {self.target.value}: {self.decision.upper()}",
            f"  {len(self.passes)} passed, {len(self.failures)} failed, "
            f"{len(self.unresolved)} unresolved",
        ]
        lines.extend(f"  {verdict.describe()}" for verdict in self.verdicts)
        owed = self.blocked_on_unapproved_thresholds
        if owed:
            lines.append(
                f"  {len(owed)} of the unresolved await an APPROVED THRESHOLD, which no "
                "further measurement will supply:"
            )
            lines.extend(f"    {v.criterion.name}: {v.criterion.threshold.citation}" for v in owed)
        return "\n".join(lines)


def _evaluate(criterion: Criterion, evidence: Mapping[str, object]) -> Verdict:
    if not criterion.threshold.usable:
        return Verdict(
            criterion=criterion,
            outcome=VerdictOutcome.UNRESOLVED,
            measured=_numeric(evidence.get(criterion.metric)),
            reason="threshold not approved",
        )
    if criterion.metric not in evidence:
        return Verdict(
            criterion=criterion,
            outcome=VerdictOutcome.UNRESOLVED,
            measured=None,
            reason="not measured",
        )
    measured = _numeric(evidence[criterion.metric])
    if measured is None:
        return Verdict(
            criterion=criterion,
            outcome=VerdictOutcome.UNRESOLVED,
            measured=None,
            reason=f"measurement is not a number: {evidence[criterion.metric]!r}",
        )
    return Verdict(
        criterion=criterion,
        outcome=(VerdictOutcome.PASS if criterion.satisfied_by(measured) else VerdictOutcome.FAIL),
        measured=measured,
    )


def _numeric(value: object) -> float | None:
    """A measurement as a number, or None when it is not one.

    `bool` is handled before `int` deliberately -- it is a subclass, and a
    criterion like `trustworthy` is naturally reported as True/False while
    being compared against 1. Converting it explicitly keeps the criterion list
    free of a parallel boolean comparison path.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def assess_promotion(
    frm: StrategyState, to: StrategyState, evidence: Mapping[str, object]
) -> ReadinessReport:
    """Whether the evidence supports entering ``to``.

    ``frm`` is taken but not used to relax anything: it is here so a caller
    cannot assess a promotion without naming where it starts, and so a later
    version can refuse an assessment for a transition the ladder forbids
    without changing the signature. The ladder itself is enforced by
    :func:`tradeit.strategy.lifecycle.may_follow`, which this does not
    duplicate — two implementations of one rule is how they diverge.
    """
    del frm
    criteria = criteria_for(to)
    if not criteria:
        # Not a rung, or a rung with nothing asked of it. HOLD rather than
        # PROMOTE: a target with no criteria is a gap in this module, and
        # answering "promote" to it would turn that gap into an approval.
        return ReadinessReport(target=to, decision=Decision.HOLD, verdicts=())
    verdicts = tuple(_evaluate(criterion, evidence) for criterion in criteria)
    passed = all(verdict.outcome is VerdictOutcome.PASS for verdict in verdicts)
    return ReadinessReport(
        target=to,
        decision=Decision.PROMOTE if passed else Decision.HOLD,
        verdicts=verdicts,
    )


def assess_demotion(current: StrategyState, evidence: Mapping[str, object]) -> ReadinessReport:
    """Whether anything says this version should come down.

    Any single FAIL demotes. An UNRESOLVED does **not**: demoting a running
    strategy because a monitor was not reporting would make a broken dashboard
    into a trading decision. It is surfaced in the report instead, and a caller
    that wants to treat unmonitored as unsafe can read `unresolved` and decide
    — which is a policy choice and belongs to the caller, not here.
    """
    criteria = demotion_criteria_for(current)
    verdicts = tuple(_evaluate(criterion, evidence) for criterion in criteria)
    demote = any(verdict.outcome is VerdictOutcome.FAIL for verdict in verdicts)
    return ReadinessReport(
        target=current,
        decision=Decision.DEMOTE if demote else Decision.HOLD,
        verdicts=verdicts,
    )


def _ladder_position(state: StrategyState) -> int:  # pragma: no cover - helper
    return LADDER.index(state)


def hurdle_evidence(out_of_sample_t: float, *, hurdle: float) -> dict[str, float]:
    """``out_of_sample_t_over_hurdle``: the held-out t as a multiple of the bar.

    `out_of_sample_clears_hurdle` compares this against **1.0**, so the bar is
    "clears the hurdle the ledger computed for this test" and no constant is
    encoded anywhere. Rejected alternative: a standing minimum. The hurdle
    *rises* with the trial count — 2.6563 at 144 trials — so any fixed number
    becomes weaker than the ledger as trials accumulate and would eventually
    pass a result the ledger rejects.

    ``hurdle`` must come from
    :func:`tradeit.backtesting.overfitting.small_sample_hurdle` for the trials
    and blocks of **this** test. It is passed in rather than computed here so
    `readiness` keeps depending on nothing but `strategy.lifecycle`: importing
    `overfitting` would pull `backtesting.base` and
    `reproducibility.versioning` into a module whose thinness is the point —
    the same coupling-by-convenience that made the observability seam useless
    for a while.

    **Refuses a non-positive hurdle** rather than dividing by it. A zero or
    negative bar would make every ratio infinite or sign-flipped, and a
    criterion that passes everything looks identical to one that is working.
    """
    if hurdle <= 0:
        raise ValueError(
            f"a hurdle of {hurdle} is not a bar; small_sample_hurdle is positive for "
            "every trial count, so this is a miscomputed or defaulted value and "
            "dividing by it would pass every result"
        )
    return {"out_of_sample_t_over_hurdle": out_of_sample_t / hurdle}
