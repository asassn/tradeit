"""Pattern identity and evolution.

A bull flag detected on Monday and the same flag on Friday are one pattern, not
five database rows. The brief is explicit about this, and the Phase 4A work
produced a concrete demonstration of why it cannot be an afterthought:

**Re-detection alone loses a pattern at the moment it resolves.** A breakout is
re-detectable for exactly ``right_bars`` sessions. After that the new high is
itself confirmed, it re-anchors the flagpole, and the old consolidation is no
longer the most recent structure — so running the detector returns nothing. A
system that only ever re-detects would report BROKEN_OUT_UNCONFIRMED for three
sessions and then silently forget the pattern existed, which is precisely when
downstream stages care most about it.

So a tracker keeps the record. Each day it matches fresh detections against
open patterns by identity, advances the ones it recognises, and applies terminal
states to the ones that have disappeared for a reason.

**What "carrying forward" may and may not do.** It may update state, note that
price has moved, and mark a pattern resolved or expired. It may **not** rewrite
geometry from data the original detection could not see. That is the same
retroactive-refinement leak the swing module exists to prevent, wearing
different clothes: a stored pattern whose resistance quietly improves is a
pattern that can no longer be reproduced from the data that produced it.

The state history is append-only for the same reason. A pattern that was
MATURE on Wednesday was MATURE on Wednesday, regardless of what happened on
Thursday.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from tradeit.core.enums import PatternType
from tradeit.errors import ConfigError
from tradeit.patterns.base import PatternInstance, PatternState
from tradeit.patterns.lifecycle import (
    StateTransition,
    TransitionReason,
    check_transition,
    is_legal,
)


@dataclass(frozen=True, slots=True)
class TrackedPattern:
    """A pattern across its whole life.

    ``current`` is the most recent detection; ``history`` is every state it has
    occupied. ``first_seen`` is when the system first *noticed* the structure,
    which is deliberately distinct from ``geometry.start_date`` — the structure
    began before anyone saw it, and conflating the two would let a pattern claim
    it was detected earlier than it was.
    """

    identity_key: str
    instrument_id: int
    pattern_type: PatternType
    current: PatternInstance
    first_seen: dt.date
    last_seen: dt.date
    history: tuple[StateTransition, ...] = ()
    #: Identity keys of patterns this one absorbed.
    superseded: tuple[str, ...] = ()

    @property
    def state(self) -> PatternState:
        return self.current.state

    @property
    def is_open(self) -> bool:
        return not self.current.state.is_terminal

    @property
    def sessions_tracked(self) -> int:
        return len(self.history)

    @property
    def peak_quality(self) -> float:
        """The best this pattern ever looked.

        Worth keeping separately from the current quality: a structure that
        scored 88 and has decayed to 61 is a different story from one that has
        been mediocre throughout, and only the current score cannot tell them
        apart.
        """
        return max((t.quality for t in self.history), default=self.current.quality)

    def with_transition(
        self,
        instance: PatternInstance,
        reason: TransitionReason,
        *,
        note: str = "",
    ) -> TrackedPattern:
        """Advance to a new observation, appending to the history."""
        check_transition(
            self.current.state,
            instance.state,
            pattern_type=self.pattern_type,
            identity_key=self.identity_key,
        )
        transition = StateTransition(
            session_date=instance.as_of_session,
            from_state=self.current.state,
            to_state=instance.state,
            reason=reason,
            quality=instance.quality,
            evidence_coverage=instance.evidence_coverage,
            note=note,
        )
        return replace(
            self,
            current=instance,
            last_seen=instance.as_of_session,
            history=(*self.history, transition),
        )

    def explain_history(self) -> str:
        return "\n".join(str(t) for t in self.history)


@dataclass(slots=True)
class PatternTracker:
    """Maintains pattern identity across detection runs.

    Stateful by design — it is the memory that re-detection lacks. Feed it each
    session's detections in chronological order; it matches, advances, and
    closes.
    """

    #: Sessions a pattern may go undetected before it is declared lost. Small,
    #: because a structure that vanishes for a week has stopped being the thing
    #: that was detected.
    grace_sessions: int = 3
    #: Sessions a resolved pattern is carried after its last detection, so a
    #: breakout stays visible past the re-detection window that produced it.
    resolution_carry_sessions: int = 10
    _open: dict[str, TrackedPattern] = field(default_factory=dict)
    _closed: list[TrackedPattern] = field(default_factory=list)

    def observe(
        self,
        detections: Sequence[PatternInstance],
        session: dt.date,
        *,
        closes: Mapping[int, float] | None = None,
    ) -> list[TrackedPattern]:
        """Fold one session's detections into the tracked set.

        ``closes`` maps instrument to that session's close and is how a pattern
        that has *left* the detector's view gets resolved honestly. Without it,
        a flag that broke out just outside the re-detection window is recorded
        as "lost" -- the single most misleading outcome available, because the
        pattern did not fail or fade, it did the thing it was being watched for.

        Using it is not retroactive refinement. The comparison is between the
        pattern's *stored* resistance and support -- measured when the pattern
        was detected, from data visible then -- and today's close. No geometry
        is recomputed and nothing about the original measurement changes.

        Returns every pattern that is open after this session, which is what a
        screen consumes. Terminal patterns move to :meth:`closed` rather than
        being deleted — a pattern that failed is evidence, and a detector's
        false-positive rate cannot be measured from surviving patterns alone.
        """
        for instance in detections:
            if instance.as_of_session != session:
                raise ConfigError(
                    f"detection is as of {instance.as_of_session} but the session is "
                    f"{session}; feeding a tracker out of order silently corrupts "
                    "every history it holds"
                )

        seen_keys: set[str] = set()
        for instance in detections:
            key = instance.identity_key
            seen_keys.add(key)
            existing = self._open.get(key)
            if existing is None:
                self._open[key] = TrackedPattern(
                    identity_key=key,
                    instrument_id=instance.instrument_id,
                    pattern_type=instance.pattern_type,
                    current=instance,
                    first_seen=session,
                    last_seen=session,
                    history=(
                        StateTransition(
                            session_date=session,
                            from_state=None,
                            to_state=instance.state,
                            reason=TransitionReason.DETECTED,
                            quality=instance.quality,
                            evidence_coverage=instance.evidence_coverage,
                        ),
                    ),
                )
            else:
                if not is_legal(
                    existing.current.state, instance.state, pattern_type=existing.pattern_type
                ):
                    # A fresh detection that would move this identity along an
                    # edge that does not exist is a *different* structure that
                    # happens to start on the same date. Minting a new identity
                    # preserves the fact that the first one failed, which is the
                    # fact a false-positive rate is computed from.
                    self._fork(existing, instance, session)
                    continue
                self._open[key] = existing.with_transition(instance, TransitionReason.ADVANCED)

        self._age_unseen(seen_keys, session, closes or {})
        self._retire_terminal()
        return self.open_patterns()

    def _fork(self, existing: TrackedPattern, instance: PatternInstance, session: dt.date) -> None:
        """Retire an identity and start a new one for genuinely new structure.

        Reached when a detection would require an illegal edge -- almost always
        a terminal pattern whose instrument has produced a fresh structure with
        the same start date. Retiring rather than resurrecting is what keeps the
        failure on the record.
        """
        # Retire the old identity into a terminal state first. Closing a
        # non-terminal pattern would break the invariant that everything in the
        # closed set has an outcome, and "it stopped being tracked" is not an
        # outcome anyone can compute a failure rate from.
        retired = (
            existing
            if existing.current.state.is_terminal
            else existing.with_transition(
                _restated(existing.current, PatternState.EXPIRED, session),
                TransitionReason.SUPERSEDED,
                note=f"identity ended; structure re-detected as {instance.state}",
            )
        )
        self._closed.append(retired)
        del self._open[existing.identity_key]

        forked = f"{instance.identity_key}:{session.isoformat()}"
        self._open[forked] = TrackedPattern(
            identity_key=forked,
            instrument_id=instance.instrument_id,
            pattern_type=instance.pattern_type,
            current=instance,
            first_seen=session,
            last_seen=session,
            history=(
                StateTransition(
                    session_date=session,
                    from_state=None,
                    to_state=instance.state,
                    reason=TransitionReason.DETECTED,
                    quality=instance.quality,
                    evidence_coverage=instance.evidence_coverage,
                    note=(
                        f"new identity: {existing.identity_key} was "
                        f"{existing.current.state} and cannot become {instance.state}"
                    ),
                ),
            ),
        )

    def _age_unseen(
        self, seen_keys: set[str], session: dt.date, closes: Mapping[int, float]
    ) -> None:
        """Handle patterns the detector did not return this session.

        Four cases, and conflating them loses information:

        * **Resolved while out of view.** Price has cleared the stored
          resistance, or broken the stored invalidation level. The pattern did
          not vanish; it did what it was being watched for. Checked first,
          because misfiling this as "lost" discards the outcome the whole
          exercise exists to observe.

        * **Already resolved.** A BROKEN_OUT_UNCONFIRMED pattern stops being
          re-detectable once its breakout high is confirmed. It is carried
          forward unchanged, because it did not stop existing -- the detector
          stopped being able to see it.
        * **Missing briefly.** Structure can fail one session's tests and pass
          the next. A short grace period avoids churning identities on noise.
        * **Gone.** Past the grace period without resolving, the structure is
          no longer what was detected.
        """
        for key, tracked in list(self._open.items()):
            if key in seen_keys:
                continue

            gap = _session_gap(tracked.last_seen, session)

            resolved = self._resolve_from_price(tracked, session, closes)
            if resolved is not None:
                self._open[key] = resolved
                continue

            if tracked.state is PatternState.BROKEN_OUT_UNCONFIRMED:
                if gap <= self.resolution_carry_sessions:
                    continue  # carried; nothing has changed about it
                self._open[key] = tracked.with_transition(
                    _restated(tracked.current, PatternState.EXPIRED, session),
                    TransitionReason.TIMED_OUT,
                    note=(
                        "resolved pattern carried "
                        f"{self.resolution_carry_sessions} sessions past its last "
                        "detection, then retired"
                    ),
                )
                continue

            if gap <= self.grace_sessions:
                continue

            self._open[key] = tracked.with_transition(
                _restated(tracked.current, PatternState.EXPIRED, session),
                TransitionReason.LOST,
                note=f"not re-detected for {gap} sessions",
            )

    def _resolve_from_price(
        self, tracked: TrackedPattern, session: dt.date, closes: Mapping[int, float]
    ) -> TrackedPattern | None:
        """Advance an out-of-view pattern using its own stored levels.

        Only levels measured at detection time are consulted, so this observes
        an outcome rather than revising a measurement. Invalidation is checked
        before breakout: a structure that broke down and then rallied through
        its old resistance is not a breakout, it is a different security than
        the one that was detected.
        """
        close = closes.get(tracked.instrument_id)
        if close is None or tracked.state.is_terminal:
            return None

        instance = tracked.current
        if instance.invalidation_price is not None and close < instance.invalidation_price:
            return tracked.with_transition(
                _restated(instance, PatternState.INVALIDATED, session),
                TransitionReason.SUPPORT_BROKEN,
                note=f"close {close:.2f} below stored invalidation "
                f"{instance.invalidation_price:.2f}",
            )

        resistance = instance.resistance_price
        if (
            resistance is not None
            and close > resistance
            and tracked.state is not PatternState.BROKEN_OUT_UNCONFIRMED
        ):
            return tracked.with_transition(
                _restated(instance, PatternState.BROKEN_OUT_UNCONFIRMED, session),
                TransitionReason.CARRIED_FORWARD,
                note=(
                    f"close {close:.2f} above stored resistance {resistance:.2f}; "
                    "a geometric observation only -- validity is Phase 5's question"
                ),
            )
        return None

    def _retire_terminal(self) -> None:
        for key, tracked in list(self._open.items()):
            if not tracked.is_open:
                self._closed.append(tracked)
                del self._open[key]

    # -- reads ---------------------------------------------------------------

    def open_patterns(self) -> list[TrackedPattern]:
        return sorted(self._open.values(), key=lambda t: (-t.current.quality, t.identity_key))

    def closed_patterns(self) -> list[TrackedPattern]:
        return list(self._closed)

    def get(self, identity_key: str) -> TrackedPattern | None:
        return self._open.get(identity_key)

    def screenable(self, *, min_quality: float = 0.0) -> list[TrackedPattern]:
        """Patterns a later stage may consider.

        Excludes FORMING: surfacing an incomplete structure means scoring a
        guess about what it will become.
        """
        return [
            t
            for t in self.open_patterns()
            if t.state.may_be_screened and t.current.quality >= min_quality
        ]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tracked in self._open.values():
            counts[str(tracked.state)] = counts.get(str(tracked.state), 0) + 1
        return {
            "open": len(self._open),
            "closed": len(self._closed),
            **dict(sorted(counts.items())),
        }


def _restated(instance: PatternInstance, state: PatternState, session: dt.date) -> PatternInstance:
    """A carried-forward instance with a new state and nothing else changed.

    The geometry, the scores and the evidence are untouched. That is the whole
    discipline: carrying a pattern forward records that its *situation* changed,
    never that its *structure* was different from what was originally measured.
    Recomputing geometry here from later data would be the retroactive
    refinement the architecture forbids.
    """
    return replace(instance, state=state, as_of_session=session)


def _session_gap(earlier: dt.date, later: dt.date) -> int:
    """Approximate trading sessions between two dates.

    Calendar-based rather than exchange-calendar-based, deliberately: a tracker
    that needed a calendar would need one per exchange, and the grace period is
    a tolerance rather than a measurement. Five calendar days is one trading
    week to within a holiday, which is the precision this decision needs.
    """
    return max(0, (later - earlier).days * 5 // 7)


def merge_overlapping(
    patterns: Iterable[TrackedPattern], *, containment: float = 0.8
) -> list[TrackedPattern]:
    """Mark smaller structures superseded by larger ones that contain them.

    Deliberately conservative, and deliberately not a filter. The brief is
    explicit that multiple interpretations may legitimately coexist -- a
    structure can be a TIGHT_CONSOLIDATION at 92 and a BULL_FLAG at 88, and
    forcing a single label early discards information that later scoring is
    better placed to use.

    So this only supersedes patterns of the *same type* where one is largely
    contained in the other, which is the case where two rows genuinely describe
    one structure rather than two readings of it.
    """
    ordered = sorted(patterns, key=lambda t: -t.current.session_count)
    kept: list[TrackedPattern] = []
    absorbed: dict[str, list[str]] = {}

    for candidate in ordered:
        parent = next(
            (
                k
                for k in kept
                if k.instrument_id == candidate.instrument_id
                and k.pattern_type is candidate.pattern_type
                and _containment(k.current, candidate.current) >= containment
            ),
            None,
        )
        if parent is None:
            kept.append(candidate)
        else:
            absorbed.setdefault(parent.identity_key, []).append(candidate.identity_key)

    return [
        replace(k, superseded=tuple(absorbed.get(k.identity_key, ())))
        if k.identity_key in absorbed
        else k
        for k in kept
    ]


def _containment(outer: PatternInstance, inner: PatternInstance) -> float:
    """Fraction of the inner pattern's span that sits inside the outer one."""
    inner_start, inner_end = inner.geometry.start_date, inner.geometry.end_date
    outer_start, outer_end = outer.geometry.start_date, outer.geometry.end_date

    overlap_start = max(inner_start, outer_start)
    overlap_end = min(inner_end, outer_end)
    if overlap_end < overlap_start:
        return 0.0

    inner_span = (inner_end - inner_start).days or 1
    return (overlap_end - overlap_start).days / inner_span
