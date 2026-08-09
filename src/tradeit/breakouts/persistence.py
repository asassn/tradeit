"""Writing breakout events to the database without forgetting anything.

Same rule as the pattern repository, one layer up: **advancing an event must
never destroy what the system believed yesterday.**

That is why the schema splits into a mutable current-state row and an
append-only observation log. The event row answers "where does this attempt
stand?" cheaply. The observation log answers "what did we think on 14 March?",
which item 15 of the brief requires explicitly — an event that was CONFIRMED on
Tuesday and FAILED_BREAKOUT on Thursday must still show Tuesday.

Three things are deliberately absent:

* **No update path for an observation.** The unique constraint on
  ``(event_id, session_date)`` makes a second write for a session a conflict,
  and this module skips rather than overwrites. Overwriting would let a re-run
  silently change what the system claims it believed.
* **No delete path for terminal events.** Failed breakouts are the evidence a
  false-breakout rate is computed from; a system that prunes its failures
  cannot measure itself.
* **No path that detaches an event from its pattern.** Item 38 requires that an
  invalidated pattern's breakout stays attached to it, so ``pattern_key``
  survives even if the pattern row is removed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.breakouts.base import BreakoutEvent, BreakoutObservation
from tradeit.core.clock import utcnow
from tradeit.errors import DataError
from tradeit.storage import tables

#: The five edges item 40 allows between breakout events. Small on purpose: the
#: brief warns against excessive ontology, and every edge here has a question
#: someone will actually ask of it.
STORED_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        #: Two events at levels close enough to be the same structural region.
        "same_region",
        #: The same instrument's boundary on two different timeframes.
        "cross_timeframe",
        #: A breakout of a pattern nested inside the other's pattern.
        "nested_breakout",
        #: A later event whose boundary is the earlier one's breakout level.
        "retest_of",
        #: The next attempt at the same boundary after this one resolved.
        "later_attempt",
    }
)


class BreakoutRepository:
    """Persists breakout events and reads them back."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- writes --------------------------------------------------------------

    def save(self, event: BreakoutEvent, *, pattern_id: int | None = None) -> tables.BreakoutEvent:
        """Insert or advance one event, appending any new observations.

        Idempotent by session: re-running a day writes the event row's current
        values again but adds no duplicate observations.
        """
        if not event.observations:
            raise DataError(
                f"event {event.event_key} has no observations; an event without a "
                "history is the object this design exists to avoid storing"
            )

        row = self._find(event.event_key)
        if row is None:
            row = tables.BreakoutEvent(
                event_key=event.event_key,
                instrument_id=event.instrument_id,
                pattern_id=pattern_id,
                pattern_key=event.boundary.pattern_key,
                timeframe=str(event.timeframe),
                attempt_number=event.attempt_number,
                pattern_detector_name=event.pattern_detector_name,
                pattern_detector_version=event.pattern_detector_version,
                pattern_config_digest=event.pattern_config_digest,
                breakout_config_digest=event.breakout_config_digest,
                scorer_version=event.scorer_version,
                data_snapshot_digest=event.data_snapshot_digest,
                profile=event.profile_name,
                opened_session=event.opened_session,
                boundary_level=_decimal(event.boundary.nominal),
                boundary_anchor_date=event.boundary.anchor_date,
                boundary_tolerance_pct=event.boundary.tolerance_pct,
                boundary_confidence=event.boundary.confidence,
                boundary_method=event.boundary.method,
                boundary_touches=event.boundary.touch_count,
                boundary_slope=event.boundary.slope_per_session,
                atr_at_open=event.boundary.atr_at_open,
                pattern_type=event.boundary.pattern_type,
                pattern_quality=event.boundary.pattern_quality,
                state=str(event.state),
                last_observed_session=event.last_session,
            )
            self.session.add(row)
            self.session.flush()

        self._advance(row, event, pattern_id=pattern_id)
        self._append_observations(row, event)
        self.session.flush()
        return row

    def _advance(
        self, row: tables.BreakoutEvent, event: BreakoutEvent, *, pattern_id: int | None
    ) -> None:
        """Update the mutable current-state columns.

        The boundary columns are *not* among them. A frozen boundary that could
        be rewritten on a later save would defeat the whole point of freezing it
        — an event would end up judged against a level that had moved to
        accommodate the breakout.
        """
        if row.state != str(event.state):
            row.previous_state = row.state
        row.state = str(event.state)
        row.last_observed_session = event.last_session
        row.breakout_quality = event.breakout_quality
        row.confirmation_score = event.confirmation_score
        row.evidence_coverage = event.evidence_coverage
        row.confidence = event.confidence
        row.qualifying_closes = event.qualifying_closes
        row.rejection_count = event.rejection_count
        row.confirmed_path = str(event.confirmed_path)
        row.confirmed_session = event.confirmed_session
        row.first_approach_session = event.first_approach_session
        row.first_penetration_session = event.first_penetration_session
        row.first_qualifying_close_session = event.first_qualifying_close_session
        row.terminal_reason = (
            str(event.terminal_reason) if event.terminal_reason is not None else None
        )
        row.gap_class = str(event.gap_class)
        row.earnings_context = str(event.earnings_context)
        row.breakout_close = _decimal(event.breakout_close)
        row.breakout_low = _decimal(event.breakout_low)
        row.breakout_volume = event.breakout_volume
        row.retest = event.retest.to_payload() if event.retest else None
        payload = event.to_payload()
        row.quality_components = {"components": payload["quality_components"]}
        row.confirmation_components = {"components": payload["confirmation_components"]}
        if pattern_id is not None:
            row.pattern_id = pattern_id

    def _append_observations(self, row: tables.BreakoutEvent, event: BreakoutEvent) -> None:
        existing = {
            date
            for (date,) in self.session.execute(
                select(tables.BreakoutObservation.session_date).where(
                    tables.BreakoutObservation.event_id == row.id
                )
            )
        }
        now = utcnow()
        for index, observation in enumerate(event.observations):
            if observation.session_date in existing:
                continue
            previous = event.observations[index - 1].state if index else None
            self.session.add(self._observation_row(row, observation, previous, now))

    def _observation_row(
        self,
        row: tables.BreakoutEvent,
        observation: BreakoutObservation,
        previous_state: object,
        now: dt.datetime,
    ) -> tables.BreakoutObservation:
        return tables.BreakoutObservation(
            event_id=row.id,
            session_date=observation.session_date,
            observed_at=now,
            knowledge_time=observation.knowledge_time,
            from_state=str(previous_state) if previous_state is not None else None,
            to_state=str(observation.state),
            reason=str(observation.reason),
            path=str(observation.path),
            breakout_quality=observation.breakout_quality,
            confirmation_score=observation.confirmation_score,
            evidence_coverage=observation.evidence_coverage,
            confidence=observation.confidence,
            close=_decimal(observation.close),
            high=_decimal(observation.high),
            low=_decimal(observation.low),
            distance_pct=observation.distance_pct,
            distance_atr=observation.distance_atr,
            measurements={
                key: round(value, 8) for key, value in sorted(observation.measurements.items())
            },
            supporting_evidence={"items": [e.detail for e in observation.supporting]},
            contradicting_evidence={"items": [e.detail for e in observation.contradicting]},
            note=observation.note or None,
        )

    def relate(
        self,
        from_event: tables.BreakoutEvent,
        to_event: tables.BreakoutEvent,
        relationship: str,
        *,
        as_of_session: dt.date,
        note: str = "",
    ) -> tables.BreakoutRelationship | None:
        """Record an edge between two events, idempotently.

        Refuses an unknown relationship rather than storing it. A free-text
        edge type is how the small ontology item 40 asks for turns into a
        taxonomy nobody maintains.
        """
        if relationship not in STORED_RELATIONSHIPS:
            raise DataError(
                f"unknown breakout relationship {relationship!r}; "
                f"expected one of {sorted(STORED_RELATIONSHIPS)}"
            )
        if from_event.id == to_event.id:
            raise DataError("an event cannot relate to itself")

        existing = self.session.execute(
            select(tables.BreakoutRelationship).where(
                tables.BreakoutRelationship.from_event_id == from_event.id,
                tables.BreakoutRelationship.to_event_id == to_event.id,
                tables.BreakoutRelationship.relationship == relationship,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        edge = tables.BreakoutRelationship(
            from_event_id=from_event.id,
            to_event_id=to_event.id,
            relationship=relationship,
            as_of_session=as_of_session,
            note=note or None,
        )
        self.session.add(edge)
        self.session.flush()
        return edge

    # -- reads ---------------------------------------------------------------

    def _find(self, event_key: str) -> tables.BreakoutEvent | None:
        return self.session.execute(
            select(tables.BreakoutEvent).where(tables.BreakoutEvent.event_key == event_key)
        ).scalar_one_or_none()

    def active_for(self, instrument_id: int) -> list[tables.BreakoutEvent]:
        """Events still worth evaluating. Terminal ones are excluded, not deleted."""
        rows = self.session.execute(
            select(tables.BreakoutEvent)
            .where(tables.BreakoutEvent.instrument_id == instrument_id)
            .where(tables.BreakoutEvent.state.notin_(("failed_breakout", "expired", "rejected")))
            .order_by(tables.BreakoutEvent.opened_session)
        ).scalars()
        return list(rows)

    def attempts_for(self, pattern_key: str) -> list[tables.BreakoutEvent]:
        """Every attempt at one pattern's boundary, oldest first.

        The query item 26 exists for: three goes at a level are three rows, and
        a later phase asking whether repeated failure matters needs all of them.
        """
        rows = self.session.execute(
            select(tables.BreakoutEvent)
            .where(tables.BreakoutEvent.pattern_key == pattern_key)
            .order_by(tables.BreakoutEvent.attempt_number)
        ).scalars()
        return list(rows)

    def history(self, event_id: int) -> list[tables.BreakoutObservation]:
        rows = self.session.execute(
            select(tables.BreakoutObservation)
            .where(tables.BreakoutObservation.event_id == event_id)
            .order_by(tables.BreakoutObservation.session_date)
        ).scalars()
        return list(rows)

    def as_of(self, event_id: int, session: dt.date) -> tables.BreakoutObservation | None:
        """What the engine believed about this event on a session.

        The read the prefix-consistency tests compare against: if this ever
        disagrees with a fresh evaluation truncated at the same session, either
        the history is being rewritten or the evaluation is reading ahead.
        """
        return self.session.execute(
            select(tables.BreakoutObservation)
            .where(tables.BreakoutObservation.event_id == event_id)
            .where(tables.BreakoutObservation.session_date <= session)
            .order_by(tables.BreakoutObservation.session_date.desc())
            .limit(1)
        ).scalar_one_or_none()

    def by_state(self, state: str, *, since: dt.date | None = None) -> list[tables.BreakoutEvent]:
        query = select(tables.BreakoutEvent).where(tables.BreakoutEvent.state == state)
        if since is not None:
            query = query.where(tables.BreakoutEvent.last_observed_session >= since)
        return list(self.session.execute(query).scalars())

    def state_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.session.execute(select(tables.BreakoutEvent.state)).scalars():
            counts[row] = counts.get(row, 0) + 1
        return counts


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(f"{value:.6f}")


def save_all(
    session: Session,
    events: Sequence[BreakoutEvent],
    *,
    pattern_ids: dict[str, int] | None = None,
) -> list[tables.BreakoutEvent]:
    """Persist a monitor's output in one pass."""
    repository = BreakoutRepository(session)
    lookup = pattern_ids or {}
    return [
        repository.save(event, pattern_id=lookup.get(event.boundary.pattern_key))
        for event in events
    ]


__all__ = ["STORED_RELATIONSHIPS", "BreakoutRepository", "save_all"]
