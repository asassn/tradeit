"""Confirmation paths and the profiles that gate them.

**Three paths, because there are genuinely three ways a breakout earns belief.**
Forcing all of them through one gate would not simplify the engine; it would
discard the two the gate does not describe, and the discarded ones are not
exotic. A modest break that quietly holds above the level for a week is the
classic base breakout. A break, a controlled pullback and a recovery is the
textbook retest. Only the first path — strong close, real volume, immediate
follow-through — is the one most systems implement, and a system that
implements only it will report the other two as unconfirmed forever.

    PATH A — momentum    close strongly above, volume, next bar follows through
    PATH B — retest      break, controlled pullback, level holds, price recovers
    PATH C — acceptance  modest break, several closes above, volatility contracts

Which path produced a confirmation is recorded on the event, so "how do
confirmations actually arrive?" is answerable from the data rather than assumed.

**Profiles describe evidence, not risk.** CONSERVATIVE wants more evidence
before it will say "confirmed"; AGGRESSIVE will accept a first strong close.
Neither says anything about how much money to put behind the result — that is
Phase 8's, it is not derivable from anything here, and the naming is the one
place where the two could plausibly be confused, so the docstrings say it
repeatedly.

**Coverage gates confirmation, and is not a trading threshold.** A profile
declines to confirm below ``min_evidence_coverage`` because beneath that the
confirmation score is computed from too little to mean what it says — the same
reasoning as ``DetectorContract.minimum_evidence_coverage`` in Phase 4. It is a
statement about the score's own validity, not about whether the trade is good.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from tradeit.breakouts.base import ConfirmationPath, RetestRecord
from tradeit.breakouts.config import ProfileConfig
from tradeit.breakouts.measures import AcceptanceReading, FollowThroughReading


@dataclass(frozen=True, slots=True)
class PathEvidence:
    """Everything the paths read. Post-breakout facts plus the frozen quality.

    ``breakout_quality`` appears here — the only place a path consults it —
    because the momentum path's premise is that the breakout bar itself was
    convincing enough to act as evidence. It is read, never recomputed.
    """

    qualifying_closes: int
    consecutive_closes: int
    breakout_quality: float
    relative_volume: float | None
    acceptance: AcceptanceReading
    follow_through: FollowThroughReading
    retest: RetestRecord | None
    evidence_coverage: float
    sessions_since_breakout: int


@dataclass(frozen=True, slots=True)
class PathDecision:
    """Whether a path confirmed, and what stopped it if not.

    ``blockers`` is the field that makes a pending event explicable. "Awaiting
    evidence" is not an answer a reviewer can act on; "momentum: follow-through
    52 < 55, relative volume 1.21 < 1.3" is.
    """

    path: ConfirmationPath
    confirmed: bool
    blockers: tuple[str, ...] = ()
    note: str = ""

    def __bool__(self) -> bool:
        return self.confirmed


@dataclass(frozen=True, slots=True)
class ConfirmationDecision:
    """The profile's verdict across every path it is willing to try."""

    path: ConfirmationPath
    confirmed: bool
    decisions: tuple[PathDecision, ...] = ()
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def reason(self) -> str:
        if self.confirmed:
            return f"confirmed via the {self.path} path"
        if not self.blockers:
            return "no confirmation path is applicable yet"
        return "; ".join(self.blockers)


def evaluate_momentum(evidence: PathEvidence, profile: ProfileConfig) -> PathDecision:
    """Strong close, real volume, immediate follow-through.

    The window requirement is a *floor on elapsed time*, not on the score: a
    profile asking for three bars of follow-through cannot be satisfied on day
    one however good the first bar looked, because the evidence it wants has not
    had time to exist. Reporting that as "not confirmed, awaiting evidence"
    rather than as a low score is what keeps "no evidence yet" distinguishable
    from "evidence against".
    """
    blockers: list[str] = []
    if evidence.qualifying_closes < profile.required_closes:
        blockers.append(
            f"momentum: {evidence.qualifying_closes} qualifying close(s), "
            f"{profile.required_closes} required"
        )
    if evidence.breakout_quality < profile.min_breakout_quality:
        blockers.append(
            f"momentum: breakout quality {evidence.breakout_quality:.0f} < "
            f"{profile.min_breakout_quality:.0f}"
        )
    if evidence.relative_volume is None:
        blockers.append("momentum: relative volume could not be measured")
    elif evidence.relative_volume < profile.min_relative_volume:
        blockers.append(
            f"momentum: relative volume {evidence.relative_volume:.2f} < "
            f"{profile.min_relative_volume:.2f}"
        )
    if profile.follow_through_window > 0:
        if evidence.sessions_since_breakout < profile.follow_through_window:
            blockers.append(
                f"momentum: {evidence.sessions_since_breakout} of "
                f"{profile.follow_through_window} follow-through session(s) elapsed"
            )
        elif not evidence.follow_through.available:
            blockers.append("momentum: no follow-through evidence available")
        elif evidence.follow_through.score < profile.min_follow_through:
            blockers.append(
                f"momentum: follow-through {evidence.follow_through.score:.0f} < "
                f"{profile.min_follow_through:.0f}"
            )
    return PathDecision(ConfirmationPath.MOMENTUM, not blockers, tuple(blockers))


def evaluate_retest(evidence: PathEvidence, profile: ProfileConfig) -> PathDecision:
    """Break, controlled pullback, level holds, price recovers.

    Requires an actual recovery, not merely a retest that has not yet failed.
    An event sitting at the level on day three of a pullback is
    RETEST_HOLDING — informative, and not the same as confirmed.
    """
    blockers: list[str] = []
    retest = evidence.retest
    if retest is None:
        return PathDecision(ConfirmationPath.RETEST, False, ("retest: no pullback has occurred",))
    if not retest.held:
        blockers.append("retest: price has not closed back above the level")
    if retest.quality < profile.min_retest_quality:
        blockers.append(f"retest: quality {retest.quality:.0f} < {profile.min_retest_quality:.0f}")
    return PathDecision(ConfirmationPath.RETEST, not blockers, tuple(blockers))


def evaluate_acceptance(evidence: PathEvidence, profile: ProfileConfig) -> PathDecision:
    """Modest break, several closes above the level, volatility contracting.

    The range condition is what separates acceptance from drift. Price grinding
    above a level in an ever-widening range is not acceptance; it is a fight.
    Where the range cannot be measured — no ATR — the condition is skipped and
    recorded as skipped rather than assumed satisfied.
    """
    blockers: list[str] = []
    if evidence.acceptance.consecutive_closes < profile.acceptance_closes:
        blockers.append(
            f"acceptance: {evidence.acceptance.consecutive_closes} consecutive close(s) "
            f"above the level, {profile.acceptance_closes} required"
        )
    ratio = evidence.acceptance.range_ratio
    note = ""
    if ratio is None:
        note = "acceptance: range contraction not measurable (no ATR)"
    elif ratio > profile.acceptance_max_range:
        blockers.append(f"acceptance: range {ratio:.2f}x ATR > {profile.acceptance_max_range:.2f}x")
    return PathDecision(ConfirmationPath.ACCEPTANCE, not blockers, tuple(blockers), note)


_EVALUATORS = {
    "momentum": evaluate_momentum,
    "retest": evaluate_retest,
    "acceptance": evaluate_acceptance,
}


def evaluate_profile(
    evidence: PathEvidence,
    profile: ProfileConfig,
    *,
    paths: Sequence[str] | None = None,
) -> ConfirmationDecision:
    """Try the profile's paths in order and return the first that confirms.

    First-match rather than best-match. There is no meaningful ranking between
    "confirmed on momentum" and "confirmed on retest" — they are different
    routes to the same state, and inventing a preference would be a trading
    opinion Phase 5 has no basis for. The order in ``profile.paths`` is
    configuration, so a later phase can state a preference explicitly if it
    finds one.
    """
    candidates = list(paths if paths is not None else profile.paths)
    if evidence.evidence_coverage < profile.min_evidence_coverage:
        return ConfirmationDecision(
            ConfirmationPath.NONE,
            False,
            (),
            (
                f"evidence coverage {evidence.evidence_coverage:.0f} < "
                f"{profile.min_evidence_coverage:.0f}: the confirmation score is "
                "computed from too little evidence to mean what it says",
            ),
        )

    decisions: list[PathDecision] = []
    for name in candidates:
        decision = _EVALUATORS[name](evidence, profile)
        decisions.append(decision)
        if decision.confirmed:
            return ConfirmationDecision(decision.path, True, tuple(decisions))

    blockers = tuple(b for d in decisions for b in d.blockers)
    return ConfirmationDecision(ConfirmationPath.NONE, False, tuple(decisions), blockers)


__all__ = [
    "ConfirmationDecision",
    "PathDecision",
    "PathEvidence",
    "evaluate_acceptance",
    "evaluate_momentum",
    "evaluate_profile",
    "evaluate_retest",
]
