"""Breakout event identity, observations and the event record itself.

Phase 4 asked "what structural setup exists?". Phase 5 asks "what is price doing
relative to that setup's boundary, and how convincing is that behaviour?" — and
explicitly does not ask whether to buy anything. Nothing in this package can see
a position, a size, a stop or a target, and the separation is structural rather
than a matter of discipline: there is no field to put them in.

**Four numbers, deliberately not three and definitely not one.**

* ``breakout_quality`` — how favourable the observed breakout characteristics
  were. Frozen at the breakout bar. A strong first close is a strong first close
  forever; nothing that happens afterwards makes the bar itself better or worse.
* ``confirmation_score`` — how much subsequent evidence corroborates it. Starts
  low by construction and moves as evidence arrives.
* ``evidence_coverage`` — how much of the intended evidence was available.
* ``confidence`` — how certain the engine is that it has identified the
  structural event correctly at all: a well-defined level with five touches
  under a high-quality pattern, or a single-extreme level under a marginal one.

Collapsing any pair of these destroys a distinction that cannot be recovered
downstream. Quality 94 / confirmation 42 is a strong breakout that has not yet
proved anything; quality 94 / confirmation 42 / coverage 55 is the same thing
with half the evidence missing; and a single blended 68 is none of them.

**Freezing the quality score is a causality control, not an optimisation.** It
is what makes "future retests cannot retroactively change the original breakout
score" true by construction rather than by test — though the test exists too.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from tradeit.breakouts.boundary import BreakoutBoundary
from tradeit.breakouts.lifecycle import (
    BreakoutState,
    BreakoutTransition,
    TransitionReason,
    check_transition,
)
from tradeit.core.enums import Bartimeframe
from tradeit.errors import ConfigError
from tradeit.patterns.base import ComponentScore, Evidence
from tradeit.reproducibility.versioning import content_hash


class ConfirmationPath(StrEnum):
    """Which route produced a confirmation.

    Three, because there are genuinely three ways a breakout earns belief, and
    forcing all of them through one gate would discard the two the gate does not
    describe. Recorded on the event so that "how do confirmations arrive?" is
    answerable, and so a later phase can weigh the paths differently without
    Phase 5 having pre-judged which is best.
    """

    #: Strong close, real volume, immediate follow-through.
    MOMENTUM = "momentum"
    #: Broke out, pulled back, the level held, price recovered.
    RETEST = "retest"
    #: Modest break, several closes above the level, volatility contracting.
    ACCEPTANCE = "acceptance"
    #: Not confirmed. Stored rather than left null so the absence is explicit.
    NONE = "none"


class EarningsContext(StrEnum):
    """Whether a breakout sits near an earnings event.

    Context only. Phase 5 records the proximity and says nothing about whether
    the earnings were good — that is Phase 6's, and inferring it from the price
    reaction would be exactly the shortcut the phase separation exists to
    prevent.
    """

    #: No earnings event within the configured window.
    NO_EARNINGS_NEARBY = "no_earnings_nearby"
    #: An earnings date falls within the window around the breakout.
    EARNINGS_EVENT_NEARBY = "earnings_event_nearby"
    #: The breakout bar itself is the first session after a report.
    POST_EARNINGS_GAP = "post_earnings_gap"
    #: No earnings calendar was supplied. Not the same as "no earnings", and
    #: recorded separately so a dataset built without a calendar cannot be
    #: mistaken for one where the answer was no.
    UNKNOWN_EVENT_CONTEXT = "unknown_event_context"


class GapClass(StrEnum):
    """Size of an opening gap through the boundary, in ATR bands.

    A gap breakout can be very strong and highly extended at the same time, and
    the classification exists so both facts are reportable without the engine
    being forced to call the gap "good" or "bad".
    """

    NONE = "none"
    SMALL = "small"
    MODERATE = "moderate"
    LARGE = "large"
    EXTREME = "extreme"


def event_identity(
    *,
    instrument_id: int,
    timeframe: Bartimeframe,
    pattern_key: str,
    boundary: BreakoutBoundary,
    attempt_number: int,
) -> str:
    """Stable identity for one breakout attempt.

    Keyed on what does not change across the attempt's life: the instrument, the
    timeframe, the structure whose boundary is being challenged, and which
    attempt this is. The state, both scores, the retest and the outcome all
    move; none of them is in the key.

    ``attempt_number`` is in the key on purpose. Item 26 requires that a pattern
    with three goes at the same level produces three records rather than one
    that overwrites itself, and the only way an attempt count survives is if
    attempts have distinct identities.

    When no pattern is attached the level and its anchor stand in, rounded so
    that a boundary re-derived to the eighth decimal does not fork the identity.
    """
    if attempt_number < 1:
        raise ConfigError(f"attempt_number {attempt_number} must be at least 1")
    anchor = (
        pattern_key
        if pattern_key
        else f"level:{round(boundary.nominal, 4)}@{boundary.anchor_date.isoformat()}"
    )
    return content_hash(
        {
            "instrument_id": instrument_id,
            "timeframe": str(timeframe),
            "anchor": anchor,
            "attempt": attempt_number,
        }
    )[:24]


@dataclass(frozen=True, slots=True)
class BreakoutObservation:
    """What the engine saw on one session. Append-only.

    The reason the event record can carry mutable current state without lying:
    every previous belief is here unchanged, including the measurements behind
    it. "Why did the confirmation score fall on Thursday?" is answerable from
    this and unanswerable from the composite alone.
    """

    session_date: dt.date
    knowledge_time: dt.datetime
    state: BreakoutState
    reason: TransitionReason

    breakout_quality: float
    confirmation_score: float
    evidence_coverage: float
    confidence: float

    close: float
    high: float
    low: float
    #: Signed distance from the nominal level, as a fraction.
    distance_pct: float
    #: Signed distance in ATR, ``None`` when the ATR was unknown.
    distance_atr: float | None = None

    #: Everything measured this session, keyed by name. Stored flat so a report
    #: can aggregate on it without re-deriving anything.
    measurements: Mapping[str, float] = field(default_factory=dict)
    supporting: tuple[Evidence, ...] = ()
    contradicting: tuple[Evidence, ...] = ()
    path: ConfirmationPath = ConfirmationPath.NONE
    note: str = ""

    def __post_init__(self) -> None:
        for label, value in (
            ("breakout_quality", self.breakout_quality),
            ("confirmation_score", self.confirmation_score),
            ("evidence_coverage", self.evidence_coverage),
            ("confidence", self.confidence),
        ):
            if not 0.0 <= value <= 100.0:
                raise ConfigError(f"{label} {value} outside [0, 100]")
        if not self.low <= self.close <= self.high:
            raise ConfigError(
                f"observation close {self.close} outside [{self.low}, {self.high}] "
                f"on {self.session_date}"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date.isoformat(),
            "state": str(self.state),
            "reason": str(self.reason),
            "breakout_quality": round(self.breakout_quality, 6),
            "confirmation_score": round(self.confirmation_score, 6),
            "evidence_coverage": round(self.evidence_coverage, 6),
            "confidence": round(self.confidence, 6),
            "close": round(self.close, 6),
            "high": round(self.high, 6),
            "low": round(self.low, 6),
            "distance_pct": round(self.distance_pct, 8),
            "distance_atr": None if self.distance_atr is None else round(self.distance_atr, 6),
            "measurements": {k: round(v, 8) for k, v in sorted(self.measurements.items())},
            "supporting": [e.detail for e in self.supporting],
            "contradicting": [e.detail for e in self.contradicting],
            "path": str(self.path),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class RetestRecord:
    """What a pullback to the former resistance did.

    Evaluated against the **original** boundary carried on the event, never a
    level re-derived after the breakout. That is not a stylistic preference: a
    level refitted through post-breakout bars drifts toward wherever price
    actually turned, which makes every retest look like it held.
    """

    started_session: dt.date
    #: Lowest low reached during the retest.
    low: float
    low_session: dt.date
    #: Maximum penetration *below* the nominal level, in ATR. Zero when price
    #: never traded below it.
    max_undercut_atr: float
    sessions: int = 0
    sessions_below_level: int = 0
    #: Mean volume during the retest over the breakout bar's volume.
    volume_ratio: float | None = None
    #: Mean true range during the retest over the ATR at breakout.
    range_ratio: float | None = None
    #: Closes back above the nominal level since the low.
    recovery_closes: int = 0
    quality: float = 0.0
    recovered_session: dt.date | None = None

    @property
    def held(self) -> bool:
        return self.recovered_session is not None

    def to_payload(self) -> dict[str, Any]:
        return {
            "started_session": self.started_session.isoformat(),
            "low": round(self.low, 6),
            "low_session": self.low_session.isoformat(),
            "max_undercut_atr": round(self.max_undercut_atr, 6),
            "sessions": self.sessions,
            "sessions_below_level": self.sessions_below_level,
            "volume_ratio": None if self.volume_ratio is None else round(self.volume_ratio, 6),
            "range_ratio": None if self.range_ratio is None else round(self.range_ratio, 6),
            "recovery_closes": self.recovery_closes,
            "quality": round(self.quality, 6),
            "recovered_session": (
                self.recovered_session.isoformat() if self.recovered_session else None
            ),
        }


@dataclass(frozen=True, slots=True)
class BreakoutEvent:
    """One attempt at one boundary, from first approach to resolution.

    Frozen, and advanced by returning a new instance. The history and the
    observations are tuples that only ever grow, so an event that was
    CONFIRMATION_PENDING at 42 on Tuesday remains exactly that in the record
    whatever Thursday brings — item 15 of the brief requires the progression to
    survive the failure, and the only reliable way to guarantee that is to make
    the earlier states unwritable.
    """

    event_key: str
    instrument_id: int
    timeframe: Bartimeframe
    boundary: BreakoutBoundary
    attempt_number: int

    opened_session: dt.date
    state: BreakoutState

    #: Provenance. Without all four a stored event cannot be reproduced.
    pattern_detector_name: str = ""
    pattern_detector_version: int = 0
    pattern_config_digest: str = ""
    breakout_config_digest: str = ""
    scorer_version: int = 1
    data_snapshot_digest: str = ""
    profile_name: str = ""

    #: The three timestamps item 1 asks for, kept separate because they answer
    #: different questions and are frequently different days.
    first_approach_session: dt.date | None = None
    first_penetration_session: dt.date | None = None
    first_qualifying_close_session: dt.date | None = None

    #: Frozen at the breakout bar. Zero until one occurs.
    breakout_quality: float = 0.0
    quality_components: tuple[ComponentScore, ...] = ()
    #: Recomputed each session from post-breakout evidence.
    confirmation_score: float = 0.0
    confirmation_components: tuple[ComponentScore, ...] = ()
    confidence: float = 0.0

    supporting_evidence: tuple[Evidence, ...] = ()
    contradicting_evidence: tuple[Evidence, ...] = ()

    history: tuple[BreakoutTransition, ...] = ()
    observations: tuple[BreakoutObservation, ...] = ()

    #: The current retest, or the last one if it has resolved. Kept after
    #: resolution rather than cleared, because a breakout that survived a
    #: 0.5-ATR undercut is a different object from one that never pulled back
    #: and the difference must remain in the record.
    retest: RetestRecord | None = None
    #: Whether a retest is currently running. Separate from ``retest`` because
    #: the record outlives the episode it describes.
    retest_active: bool = False
    rejection_count: int = 0
    confirmed_path: ConfirmationPath = ConfirmationPath.NONE
    confirmed_session: dt.date | None = None
    terminal_reason: TransitionReason | None = None

    #: The breakout bar itself, kept because failure rules refer to it and
    #: re-deriving it from the series would mean re-deriving which bar it was.
    breakout_close: float | None = None
    breakout_low: float | None = None
    breakout_volume: float | None = None
    #: Highest close since the breakout, for follow-through and extension.
    peak_close_since_breakout: float | None = None
    lowest_low_since_breakout: float | None = None
    qualifying_closes: int = 0
    consecutive_closes_above: int = 0

    earnings_context: EarningsContext = EarningsContext.UNKNOWN_EVENT_CONTEXT
    gap_class: GapClass = GapClass.NONE

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ConfigError(f"attempt_number {self.attempt_number} must be at least 1")
        for label, value in (
            ("breakout_quality", self.breakout_quality),
            ("confirmation_score", self.confirmation_score),
            ("confidence", self.confidence),
        ):
            if not 0.0 <= value <= 100.0:
                raise ConfigError(f"{label} {value} outside [0, 100]")

    # -- derived facts -------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return not self.state.is_terminal

    @property
    def is_active(self) -> bool:
        """Whether the engine still has anything to do with this event."""
        return not self.state.is_resolved

    @property
    def last_session(self) -> dt.date:
        return self.observations[-1].session_date if self.observations else self.opened_session

    @property
    def sessions_observed(self) -> int:
        return len(self.observations)

    @property
    def has_broken_out(self) -> bool:
        return self.first_qualifying_close_session is not None

    @property
    def evidence_coverage(self) -> float:
        """Coverage over both component sets, weighted by their own weights.

        One number rather than two because a consumer asking "how much of the
        intended evidence did this event have?" means all of it. The per-set
        figures remain recoverable from the components.
        """
        components = (*self.quality_components, *self.confirmation_components)
        total = sum(c.weight for c in components)
        if total <= 0:
            return 0.0
        available = sum(c.weight for c in components if not c.unavailable)
        return round(available / total * 100.0, 6)

    @property
    def quality_coverage(self) -> float:
        total = sum(c.weight for c in self.quality_components)
        if total <= 0:
            return 0.0
        available = sum(c.weight for c in self.quality_components if not c.unavailable)
        return round(available / total * 100.0, 6)

    @property
    def confirmation_coverage(self) -> float:
        total = sum(c.weight for c in self.confirmation_components)
        if total <= 0:
            return 0.0
        available = sum(c.weight for c in self.confirmation_components if not c.unavailable)
        return round(available / total * 100.0, 6)

    def coverage_gaps(self) -> dict[str, str]:
        return {
            c.name: c.unavailable_reason
            for c in (*self.quality_components, *self.confirmation_components)
            if c.unavailable
        }

    def component(self, name: str) -> ComponentScore | None:
        for item in (*self.quality_components, *self.confirmation_components):
            if item.name == name:
                return item
        return None

    def observation_on(self, session: dt.date) -> BreakoutObservation | None:
        for item in self.observations:
            if item.session_date == session:
                return item
        return None

    def state_on(self, session: dt.date) -> BreakoutState | None:
        """What the engine believed on a session, from the stored history.

        The prefix-consistency tests compare this against a fresh evaluation
        truncated at the same session; if they ever disagree, either the history
        is being rewritten or the evaluation is reading ahead.
        """
        seen: BreakoutState | None = None
        for item in self.observations:
            if item.session_date > session:
                break
            seen = item.state
        return seen

    # -- advancing -----------------------------------------------------------

    def advanced(
        self,
        observation: BreakoutObservation,
        **updates: Any,
    ) -> BreakoutEvent:
        """Append one session's observation and apply the resulting changes.

        The transition is checked here rather than by the caller so that no code
        path can append an observation whose state the machine forbids.
        """
        check_transition(self.state, observation.state, event_id=self.event_key)
        transition = BreakoutTransition(
            session_date=observation.session_date,
            from_state=self.state,
            to_state=observation.state,
            reason=observation.reason,
            breakout_quality=observation.breakout_quality,
            confirmation_score=observation.confirmation_score,
            evidence_coverage=observation.evidence_coverage,
            path="" if observation.path is ConfirmationPath.NONE else str(observation.path),
            note=observation.note,
        )
        return replace(
            self,
            state=observation.state,
            history=(*self.history, transition),
            observations=(*self.observations, observation),
            breakout_quality=observation.breakout_quality,
            confirmation_score=observation.confirmation_score,
            confidence=observation.confidence,
            **updates,
        )

    # -- reporting -----------------------------------------------------------

    def explain(self) -> str:
        """The human-readable form the brief specifies in item 36.

        Ends where Phase 5 ends. There is no BUY line, no size, no stop and no
        target, and there is nowhere in this object those could come from.
        """
        lines = [
            f"Instrument:         {self.instrument_id}",
            f"Timeframe:          {self.timeframe}",
            f"Pattern:            {self.boundary.pattern_type or '(unattached level)'}",
            f"Pattern Quality:    {self.boundary.pattern_quality:.0f}",
            f"Attempt:            {self.attempt_number}",
            f"Breakout State:     {self.state}",
            f"Breakout Quality:   {self.breakout_quality:.0f}",
            f"Confirmation:       {self.confirmation_score:.0f}",
            f"Evidence Coverage:  {self.evidence_coverage:.0f}",
            f"Confidence:         {self.confidence:.0f}",
            f"Resistance:         {self.boundary.nominal:.2f}",
            f"Tolerance Zone:     {self.boundary.floor():.2f} - {self.boundary.threshold():.2f}",
        ]
        if self.breakout_close is not None:
            above = self.boundary.distance_pct(self.breakout_close) * 100.0
            lines.append(f"Breakout Close:     {self.breakout_close:.2f}")
            lines.append(f"Close Above Level:  {above:+.2f}%")
        last = self.observations[-1] if self.observations else None
        if last is not None:
            for key in (
                "relative_volume",
                "close_location",
                "penetration_atr",
                "extension_atr",
            ):
                if key in last.measurements:
                    lines.append(f"{key.replace('_', ' ').title():<20}{last.measurements[key]:.2f}")
        if self.confirmed_path is not ConfirmationPath.NONE:
            lines.append(f"Confirmation Path:  {self.confirmed_path}")
        lines.append(f"Earnings Context:   {self.earnings_context}")
        if self.gap_class is not GapClass.NONE:
            lines.append(f"Gap:                {self.gap_class}")

        lines.append("")
        lines.append("Components:")
        for item in (*self.quality_components, *self.confirmation_components):
            label = "n/a" if item.unavailable else f"{item.score:.0f}"
            lines.append(f"  {item.name:<26} {label:>4}  (weight {item.weight:.2f})")
        gaps = self.coverage_gaps()
        if gaps:
            lines.append("")
            lines.append("Unavailable:")
            lines.extend(f"  {name}: {reason}" for name, reason in sorted(gaps.items()))

        lines.append("")
        lines.append("Supporting Evidence:")
        lines.extend(f"  * {e}" for e in self.supporting_evidence or ())
        if not self.supporting_evidence:
            lines.append("  (none)")
        lines.append("Contradicting Evidence:")
        lines.extend(f"  * {e}" for e in self.contradicting_evidence or ())
        if not self.contradicting_evidence:
            lines.append("  (none)")
        lines.append("")
        lines.append(f"State Reason:       {self._state_reason()}")
        return "\n".join(lines)

    def _state_reason(self) -> str:
        last = self.history[-1] if self.history else None
        if last is None:
            return "no observations recorded"
        if self.state is BreakoutState.CONFIRMATION_PENDING:
            return (
                f"initial breakout quality is {self.breakout_quality:.0f}, but the "
                f"{self.profile_name or 'configured'} confirmation policy requires "
                "additional evidence"
            )
        if self.state is BreakoutState.CONFIRMED:
            return f"confirmed via the {self.confirmed_path} path"
        if self.state is BreakoutState.FAILED_BREAKOUT:
            return f"failed: {last.reason}"
        if self.state is BreakoutState.EXPIRED:
            return f"expired: {last.reason}"
        return str(last.reason)

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "instrument_id": self.instrument_id,
            "timeframe": str(self.timeframe),
            "attempt_number": self.attempt_number,
            "state": str(self.state),
            "opened_session": self.opened_session.isoformat(),
            "boundary": self.boundary.to_payload(),
            "first_approach_session": _iso(self.first_approach_session),
            "first_penetration_session": _iso(self.first_penetration_session),
            "first_qualifying_close_session": _iso(self.first_qualifying_close_session),
            "breakout_quality": round(self.breakout_quality, 6),
            "confirmation_score": round(self.confirmation_score, 6),
            "confidence": round(self.confidence, 6),
            "evidence_coverage": round(self.evidence_coverage, 6),
            "quality_components": [_component_payload(c) for c in self.quality_components],
            "confirmation_components": [
                _component_payload(c) for c in self.confirmation_components
            ],
            "supporting_evidence": [e.detail for e in self.supporting_evidence],
            "contradicting_evidence": [e.detail for e in self.contradicting_evidence],
            "history": [t.to_payload() for t in self.history],
            "retest": self.retest.to_payload() if self.retest else None,
            "retest_active": self.retest_active,
            "rejection_count": self.rejection_count,
            "confirmed_path": str(self.confirmed_path),
            "confirmed_session": _iso(self.confirmed_session),
            "terminal_reason": str(self.terminal_reason) if self.terminal_reason else None,
            "earnings_context": str(self.earnings_context),
            "gap_class": str(self.gap_class),
            "profile": self.profile_name,
            "scorer_version": self.scorer_version,
            "pattern_detector": {
                "name": self.pattern_detector_name,
                "version": self.pattern_detector_version,
            },
            "config_digests": {
                "pattern": self.pattern_config_digest,
                "breakout": self.breakout_config_digest,
            },
        }


def _iso(value: dt.date | None) -> str | None:
    return value.isoformat() if value else None


def _component_payload(component: ComponentScore) -> dict[str, Any]:
    return {
        "name": component.name,
        "score": None if component.unavailable else round(component.score, 6),
        "weight": component.weight,
        "requirement": str(component.requirement),
        "unavailable_reason": component.unavailable_reason,
        "measurements": {k: round(v, 8) for k, v in sorted(component.measurements.items())},
    }


def merge_evidence(
    components: Sequence[ComponentScore],
) -> tuple[tuple[Evidence, ...], tuple[Evidence, ...]]:
    """Collect the evidence scattered across components into two lists.

    Order follows component order rather than being sorted, so a reader sees the
    reasoning in the order the engine applied it.
    """
    supporting: list[Evidence] = []
    contradicting: list[Evidence] = []
    for component in components:
        supporting.extend(component.evidence)
        contradicting.extend(component.contradicting)
    return tuple(supporting), tuple(contradicting)


__all__ = [
    "BreakoutEvent",
    "BreakoutObservation",
    "ConfirmationPath",
    "EarningsContext",
    "GapClass",
    "RetestRecord",
    "event_identity",
    "merge_evidence",
]
