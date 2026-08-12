"""Writing pattern lifecycles to the database without forgetting anything.

The rule this module exists to enforce: **advancing a pattern must never
destroy what the system believed yesterday.**

That is why the schema splits into a mutable current-state row and an
append-only observation log. The pattern row answers "what is the state of this
base?" cheaply, which a screen asks constantly. The observation log answers
"what did we think on 14 March?", which an audit, a backtest replay and a
false-positive analysis all ask, and which a mutable row alone cannot answer at
all.

Two things are deliberately *not* here. There is no update path that rewrites
an existing observation — the unique constraint on ``(pattern_id,
session_date)`` makes a second write for one session a conflict rather than an
overwrite. And there is no delete path for terminal patterns: a failed pattern
is the evidence a false-positive rate is computed from, and a system that
prunes its failures cannot measure itself.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.core.clock import utcnow
from tradeit.errors import DataError
from tradeit.patterns.base import PatternInstance
from tradeit.patterns.lifecycle import StateTransition
from tradeit.patterns.relationships import STORED_RELATIONSHIPS
from tradeit.patterns.tracking import TrackedPattern
from tradeit.storage import tables


class PatternRepository:
    """Persists tracked patterns and reads them back.

    Takes :class:`~tradeit.patterns.tracking.TrackedPattern` rather than raw
    instances, because a pattern without its history is exactly the object this
    design exists to avoid storing.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- writes --------------------------------------------------------------

    def save(self, tracked: TrackedPattern) -> tables.Pattern:
        """Insert or advance one pattern, appending any new observations.

        Idempotent by session: re-running a day's detection writes the pattern
        row's current values again but adds no duplicate observations, because
        an observation for a session that is already recorded is skipped rather
        than replaced. Replacing it would let a re-run silently change what the
        system claims it believed.
        """
        current = tracked.current
        row = self._find(tracked.identity_key, current.detector_version)

        if row is None:
            # A pattern is often first persisted after several sessions of
            # tracking, so its transition metadata must come from the history
            # rather than being left null. Setting previous_state and
            # terminal_at only on the *update* path meant a pattern saved once
            # -- which is what a batch job at the end of a run does -- lost both.
            previous = next(
                (
                    str(t.from_state)
                    for t in reversed(tracked.history)
                    if t.from_state is not None and t.from_state is not current.state
                ),
                None,
            )
            row = tables.Pattern(
                identity_key=tracked.identity_key,
                instrument_id=tracked.instrument_id,
                pattern_type=str(tracked.pattern_type),
                timeframe=str(current.timeframe),
                detector_name=current.detector_name,
                detector_version=current.detector_version,
                config_digest=current.config_digest,
                data_snapshot_digest=current.data_snapshot_digest,
                state=str(current.state),
                previous_state=previous,
                state_changed_at=utcnow(),
                terminal_at=utcnow() if current.state.is_terminal else None,
                first_detected_at=utcnow(),
                last_observed_at=utcnow(),
                structural_start_date=current.geometry.start_date,
                structural_end_date=current.geometry.end_date,
                structure_known_through=_known_through(tracked),
                first_detected_session=tracked.first_seen,
                last_observed_session=tracked.last_seen,
                quality=current.quality,
                peak_quality=tracked.peak_quality,
                evidence_coverage=current.evidence_coverage,
                confidence=current.confidence,
                resistance_price=_as_decimal(current.resistance_price),
                support_price=_as_decimal(current.support_price),
                invalidation_price=_as_decimal(current.invalidation_price),
                geometry=current.geometry.as_dict(),
                session_count=current.session_count,
            )
            self.session.add(row)
            self.session.flush()
        else:
            self._advance(row, tracked)

        self._append_observations(row, tracked)
        return row

    def _advance(self, row: tables.Pattern, tracked: TrackedPattern) -> None:
        """Update the current-state row. Structural fields are checked, not set.

        Geometry is written only for a pattern that was genuinely re-measured.
        A carried-forward pattern keeps the geometry it was detected with, and
        writing the same values again is harmless — but writing *different*
        ones would be the retroactive-refinement leak reaching the database, so
        a change in structural start is refused rather than persisted.
        """
        current = tracked.current
        # Written once, at insert, and never here. That is the whole point of
        # it: `structural_end_date` below is the latest re-measurement, and if
        # both moved there would again be nothing recording what was knowable
        # when the pattern was first detected.
        if row.structure_known_through is None:
            row.structure_known_through = _known_through(tracked)
        if row.structural_start_date != current.geometry.start_date:
            raise DataError(
                f"pattern {tracked.identity_key} would move its structural start from "
                f"{row.structural_start_date} to {current.geometry.start_date}. A "
                "pattern's origin cannot move; if the structure genuinely changed, it "
                "is a different pattern and needs a new identity."
            )

        if row.state != str(current.state):
            row.previous_state = row.state
            row.state = str(current.state)
            row.state_changed_at = utcnow()
            if current.state.is_terminal:
                row.terminal_at = utcnow()

        row.structural_end_date = current.geometry.end_date
        row.last_observed_at = utcnow()
        row.last_observed_session = tracked.last_seen
        row.quality = current.quality
        row.peak_quality = max(row.peak_quality, tracked.peak_quality)
        row.evidence_coverage = current.evidence_coverage
        row.confidence = current.confidence
        row.resistance_price = _as_decimal(current.resistance_price)
        row.support_price = _as_decimal(current.support_price)
        row.invalidation_price = _as_decimal(current.invalidation_price)
        row.geometry = current.geometry.as_dict()
        row.session_count = current.session_count
        self.session.flush()

    def _append_observations(self, row: tables.Pattern, tracked: TrackedPattern) -> None:
        existing = {
            date
            for (date,) in self.session.execute(
                select(tables.PatternObservation.session_date).where(
                    tables.PatternObservation.pattern_id == row.id
                )
            )
        }
        for transition in _one_per_session(tracked.history):
            if transition.session_date in existing:
                continue
            self.session.add(self._observation_row(row, tracked.current, transition))
        self.session.flush()

    @staticmethod
    def _observation_row(
        row: tables.Pattern, instance: PatternInstance, transition: StateTransition
    ) -> tables.PatternObservation:
        return tables.PatternObservation(
            pattern_id=row.id,
            session_date=transition.session_date,
            observed_at=utcnow(),
            knowledge_time=instance.knowledge_time,
            from_state=str(transition.from_state) if transition.from_state else None,
            to_state=str(transition.to_state),
            reason=str(transition.reason),
            # From the transition, never from `instance`. `instance` is the
            # pattern's *latest* measurement and every row in this loop would
            # otherwise be stamped with it — which is exactly the retrospective
            # smearing this column exists to make visible.
            structure_end_observed=transition.structure_end,
            quality=transition.quality,
            evidence_coverage=transition.evidence_coverage,
            confidence=instance.confidence,
            component_scores={
                c.name: {
                    "score": None if c.unavailable else round(c.score, 6),
                    "weight": c.weight,
                    "requirement": str(c.requirement),
                    "unavailable": c.unavailable,
                    "unavailable_reason": c.unavailable_reason,
                    "measurements": {k: round(v, 6) for k, v in sorted(c.measurements.items())},
                }
                for c in instance.components
            },
            supporting_evidence={"items": [e.detail for e in instance.supporting_evidence]},
            contradicting_evidence={"items": [e.detail for e in instance.contradicting_evidence]},
            note=transition.note or None,
        )

    def relate(
        self,
        from_pattern: tables.Pattern,
        to_pattern: tables.Pattern,
        relationship: str,
        *,
        as_of_session: dt.date | None = None,
        note: str = "",
    ) -> tables.PatternRelationship:
        """Record how two patterns relate.

        The vocabulary lives in :mod:`tradeit.patterns.relationships` so there
        is one definition of it rather than a string literal here and an enum
        there. Deliberately small -- six edges -- because a bigger ontology
        invites arguments about which one applies instead of recording the fact
        that two structures coexist.
        """
        if relationship not in STORED_RELATIONSHIPS:
            raise DataError(
                f"unknown pattern relationship {relationship!r}; "
                f"expected one of {sorted(STORED_RELATIONSHIPS)}"
            )
        if from_pattern.id == to_pattern.id:
            raise DataError("a pattern cannot relate to itself")

        edge = tables.PatternRelationship(
            from_pattern_id=from_pattern.id,
            to_pattern_id=to_pattern.id,
            relationship=relationship,
            established_at=utcnow(),
            as_of_session=as_of_session,
            note=note or None,
        )
        self.session.add(edge)
        self.session.flush()
        return edge

    # -- reads ---------------------------------------------------------------

    def _find(self, identity_key: str, detector_version: int) -> tables.Pattern | None:
        return self.session.scalars(
            select(tables.Pattern).where(
                tables.Pattern.identity_key == identity_key,
                tables.Pattern.detector_version == detector_version,
            )
        ).one_or_none()

    def open_for_instrument(self, instrument_id: int) -> list[tables.Pattern]:
        return list(
            self.session.scalars(
                select(tables.Pattern)
                .where(
                    tables.Pattern.instrument_id == instrument_id,
                    tables.Pattern.state.notin_(("invalidated", "expired")),
                )
                .order_by(tables.Pattern.quality.desc())
            )
        )

    def history(self, pattern_id: int) -> list[tables.PatternObservation]:
        return list(
            self.session.scalars(
                select(tables.PatternObservation)
                .where(tables.PatternObservation.pattern_id == pattern_id)
                .order_by(tables.PatternObservation.session_date)
            )
        )

    def as_of(self, pattern_id: int, session: dt.date) -> tables.PatternObservation | None:
        """What the system believed about this pattern on a given session.

        The question a mutable current-state row cannot answer, and the reason
        the observation log exists.
        """
        return self.session.scalars(
            select(tables.PatternObservation)
            .where(
                tables.PatternObservation.pattern_id == pattern_id,
                tables.PatternObservation.session_date <= session,
            )
            .order_by(tables.PatternObservation.session_date.desc())
            .limit(1)
        ).one_or_none()

    def by_detector_version(
        self, pattern_type: str, versions: Sequence[int]
    ) -> list[tables.Pattern]:
        """Patterns produced by specific detector versions.

        A backtest pins a version. Mixing v1.0 and v1.1 results into one
        population and presenting them as comparable is the specific mistake
        detector versioning exists to prevent.
        """
        return list(
            self.session.scalars(
                select(tables.Pattern).where(
                    tables.Pattern.pattern_type == pattern_type,
                    tables.Pattern.detector_version.in_(versions),
                )
            )
        )


def _one_per_session(history: Sequence[StateTransition]) -> list[StateTransition]:
    """Collapse transitions that share a session into one net change.

    ``pattern_observations`` is unique on ``(pattern_id, session_date)``, which
    is the schema asserting a real property: a session has one answer to "what
    happened to this pattern today?". A history carrying two entries for one
    date used to make the insert fail outright, taking the whole run with it —
    which is how a tracker defect (a forked identity aged on its own birth
    session) surfaced as a database error four layers away.

    Collapsing loses nothing that the row can express: the surviving record runs
    from the first transition's ``from_state`` to the last's ``to_state``, which
    is exactly the day's net change, and keeps the last one's scores because
    those are the values the pattern ended the session holding. Every reason and
    note is preserved in the collapsed row's note so the intermediate step is
    still readable.

    This is a backstop, not the fix. Two transitions on one session almost
    always means something upstream advanced a pattern twice in a day, and that
    is worth finding rather than smoothing over.
    """
    out: list[StateTransition] = []
    for transition in history:
        if out and out[-1].session_date == transition.session_date:
            first = out[-1]
            notes = [n for n in (first.note, transition.note) if n]
            if first.reason is not transition.reason:
                notes.insert(0, f"collapsed {first.reason} then {transition.reason}")
            out[-1] = replace(
                transition,
                from_state=first.from_state,
                note="; ".join(notes),
            )
            continue
        out.append(transition)
    return out


def _known_through(tracked: TrackedPattern) -> dt.date:
    """How far the structure was measured to run when it was first detected.

    Taken from the history's first entry rather than from ``current``, because a
    pattern is often first persisted after several sessions of tracking and
    ``current`` is by then a later measurement. Falls back to the current
    geometry only when there is no history to read, which is a pattern being
    saved on its own birth session — where the two are the same value.
    """
    for transition in tracked.history:
        if transition.structure_end is not None:
            return transition.structure_end
    return tracked.current.geometry.end_date


def _as_decimal(value: float | None) -> Decimal | None:
    """Quantise to the schema's scale rather than letting the driver round.

    A price stored at full float precision and read back as Numeric(18, 6)
    round-trips to a different number, which turns an idempotent re-save into a
    spurious change.
    """
    return None if value is None else Decimal(f"{value:.6f}")
