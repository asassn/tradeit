"""The breakout monitor: which boundaries to watch, and when to open an attempt.

The engine evaluates one event against one session. The monitor decides which
events should exist at all — and that decision is where two of the brief's
requirements live.

**Active-pattern filtering (item 45).** Evaluating every pattern ever detected
against today's bar is waste, but the runtime is the smaller half of the reason.
A long-expired structure's resistance is not a level anyone is trading against;
scoring a cross of it does not find a breakout, it *manufactures* one, and a
dataset full of manufactured events will show a false-breakout rate that
describes the monitor rather than the market. So the monitor watches the Phase 4
lifecycle states that mean "this structure is live" — MATURE, NEAR_BREAKOUT,
BROKEN_OUT_UNCONFIRMED — and drops boundaries whose pattern has gone stale.

**Attempt numbering (item 26).** A pattern may have three goes at the same
level. The first is rejected, the second closes above and fails the next day,
the third confirms. Those are three records, none of which overwrites another,
and the monitor is what mints the second and third: a resolved (REJECTED)
attempt is never revived, so a later qualifying close opens attempt N+1 under a
new event id. After ``rejection.max_rejections`` attempts the monitor stops:
the level has defeated the thesis, which it records by failing the last attempt
with ``REPEATED_REJECTION`` rather than by silently going quiet.

**Breakouts are timeframe-specific (item 39).** A daily bull flag breakout and a
weekly VCP breakout are different events with different boundaries and different
clocks. The monitor keys events by (instrument, timeframe, pattern, attempt) and
never merges them; whether the two agree is a later phase's question, and it can
only be asked if both truths were recorded.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tradeit.breakouts.base import BreakoutEvent
from tradeit.breakouts.boundary import BreakoutBoundary, boundary_from_pattern
from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.context import BreakoutContext
from tradeit.breakouts.engine import BreakoutEngine, SessionInputs
from tradeit.breakouts.lifecycle import BreakoutState, TransitionReason
from tradeit.breakouts.measures import average_true_range
from tradeit.core.enums import Bartimeframe
from tradeit.core.models import OhlcvBar
from tradeit.patterns.base import PatternInstance, PatternState
from tradeit.patterns.tracking import TrackedPattern


@dataclass(frozen=True, slots=True)
class MonitoredBoundary:
    """One live boundary the monitor is watching, with its provenance."""

    boundary: BreakoutBoundary
    pattern_key: str
    pattern_state: PatternState
    detector_name: str
    detector_version: int
    config_digest: str
    pattern_length: int
    last_observed_session: dt.date


@dataclass(frozen=True, slots=True)
class SkippedPattern:
    """A pattern the monitor declined to watch, and why.

    Recorded rather than silently dropped, for the same reason the scanner
    records skipped detectors: "no breakout was found" and "the boundary was
    never watched" are different facts, and a dataset that cannot separate them
    cannot support any statement about how often breakouts occur.
    """

    pattern_key: str
    reason: str


@dataclass(slots=True)
class MonitorResult:
    """What one session's monitoring produced for one instrument."""

    instrument_id: int
    timeframe: Bartimeframe
    as_of_session: dt.date
    opened: list[BreakoutEvent] = field(default_factory=list)
    updated: list[BreakoutEvent] = field(default_factory=list)
    closed: list[BreakoutEvent] = field(default_factory=list)
    skipped: list[SkippedPattern] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def active(self) -> list[BreakoutEvent]:
        return [e for e in self.updated if e.is_active]

    def summary(self) -> dict[str, object]:
        return {
            "instrument_id": self.instrument_id,
            "timeframe": str(self.timeframe),
            "as_of_session": self.as_of_session.isoformat(),
            "opened": len(self.opened),
            "updated": len(self.updated),
            "closed": len(self.closed),
            "skipped": {s.pattern_key: s.reason for s in self.skipped},
            "states": _state_counts(self.updated),
        }


def _state_counts(events: Sequence[BreakoutEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[str(event.state)] = counts.get(str(event.state), 0) + 1
    return counts


class BreakoutMonitor:
    """Keeps a set of breakout events alive across sessions.

    Holds the events, so identities persist. A monitor without state would
    re-mint an event every session — the same failure the pattern tracker exists
    to prevent, one layer up, and with the additional damage that attempt
    numbering would become meaningless.
    """

    def __init__(
        self,
        config: BreakoutEngineConfig | None = None,
        *,
        engine: BreakoutEngine | None = None,
        profile: str | None = None,
    ) -> None:
        self.config = config or BreakoutEngineConfig()
        self.engine = engine or BreakoutEngine(self.config, profile=profile)
        #: Keyed by event id. Terminal events stay here so history survives; the
        #: active set is derived rather than maintained separately, which
        #: removes the possibility of the two disagreeing.
        self.events: dict[str, BreakoutEvent] = {}

    # -- boundary selection --------------------------------------------------

    def monitorable(
        self,
        patterns: Sequence[PatternInstance | TrackedPattern],
        *,
        as_of_session: dt.date,
        bars: Sequence[OhlcvBar],
    ) -> tuple[list[MonitoredBoundary], list[SkippedPattern]]:
        """Filter patterns down to the boundaries worth watching.

        The ATR is computed once here and frozen onto every boundary opened from
        it, so an event's ATR-relative measurements never shift under it. A
        breakout that closed 0.8 ATR through the level closed 0.8 ATR through
        the level, whatever volatility did next.
        """
        atr = average_true_range(bars, self.config.atr_period)
        allowed = {str(s) for s in self.config.monitored_states}

        live: list[MonitoredBoundary] = []
        skipped: list[SkippedPattern] = []
        for item in patterns:
            instance = item.current if isinstance(item, TrackedPattern) else item
            key = instance.identity_key

            if str(instance.state) not in allowed:
                skipped.append(
                    SkippedPattern(
                        key,
                        f"pattern is {instance.state}; only "
                        f"{sorted(allowed)} carry a boundary worth watching",
                    )
                )
                continue
            resistance = instance.geometry.resistance
            if resistance is None:
                skipped.append(SkippedPattern(key, "pattern has no resistance boundary"))
                continue
            staleness = (as_of_session - instance.as_of_session).days
            if staleness > self.config.max_pattern_staleness:
                skipped.append(
                    SkippedPattern(
                        key,
                        f"pattern last observed {staleness} days ago, beyond the "
                        f"{self.config.max_pattern_staleness}-session staleness limit",
                    )
                )
                continue

            live.append(
                MonitoredBoundary(
                    boundary=boundary_from_pattern(
                        resistance,
                        atr=atr,
                        config=self.config.tolerance,
                        pattern_key=key,
                        pattern_type=str(instance.pattern_type),
                        pattern_quality=instance.quality,
                    ),
                    pattern_key=key,
                    pattern_state=instance.state,
                    detector_name=instance.detector_name,
                    detector_version=instance.detector_version,
                    config_digest=instance.config_digest,
                    pattern_length=instance.session_count,
                    last_observed_session=instance.as_of_session,
                )
            )
        return live, skipped

    # -- the session pass ----------------------------------------------------

    def observe(
        self,
        instrument_id: int,
        timeframe: Bartimeframe,
        patterns: Sequence[PatternInstance | TrackedPattern],
        bars: Sequence[OhlcvBar],
        as_of_session: dt.date,
        *,
        knowledge_time: dt.datetime | None = None,
        context: BreakoutContext | None = None,
        invalidated_patterns: Sequence[str] = (),
        segments: Mapping[str, tuple[dt.date, dt.date]] | None = None,
        is_partial: bool = False,
        observed_at: dt.time | None = None,
    ) -> MonitorResult:
        """Advance every live event and open new ones where warranted.

        Order matters: existing events are advanced *before* new ones are
        considered, so that an attempt resolving today cannot also open its own
        successor on the same session. A rejection and its next attempt on the
        same bar would be one bar doing two contradictory things.
        """
        import time

        started = time.perf_counter()
        clock = knowledge_time or dt.datetime.combine(as_of_session, dt.time(23, 59), tzinfo=dt.UTC)
        result = MonitorResult(
            instrument_id=instrument_id, timeframe=timeframe, as_of_session=as_of_session
        )
        live, skipped = self.monitorable(patterns, as_of_session=as_of_session, bars=bars)
        result.skipped.extend(skipped)
        by_pattern = {item.pattern_key: item for item in live}
        invalidated = set(invalidated_patterns)

        consolidation, impulse = _legs(bars, segments)

        # 1. advance what already exists
        for key, event in list(self.events.items()):
            if not event.is_active or event.instrument_id != instrument_id:
                continue
            if event.timeframe is not timeframe:
                continue
            watched = by_pattern.get(event.boundary.pattern_key)
            pattern_invalidated = event.boundary.pattern_key in invalidated
            if watched is None and not pattern_invalidated:
                closed = self.engine.expire(
                    event,
                    as_of_session,
                    clock,
                    reason=TransitionReason.TIMED_OUT,
                    note="the underlying pattern is no longer monitorable",
                )
                self.events[key] = closed
                result.closed.append(closed)
                continue

            inputs = SessionInputs(
                bars=bars,
                as_of_session=as_of_session,
                knowledge_time=clock,
                context=context,
                pattern_invalidated=pattern_invalidated,
                pattern_length=watched.pattern_length if watched else None,
                consolidation_bars=consolidation,
                impulse_bars=impulse,
                is_partial=is_partial,
                observed_at=observed_at,
            )
            advanced = self.engine.advance(event, inputs)
            self.events[key] = advanced
            result.updated.append(advanced)
            if advanced.state.is_terminal:
                result.closed.append(advanced)

        # 2. open attempts for boundaries with no live event
        for item in live:
            existing = self._attempts_for(instrument_id, timeframe, item.pattern_key)
            if any(e.is_active for e in existing):
                continue
            attempt = len(existing) + 1
            if self._boundary_defeated(existing, item):
                result.skipped.append(
                    SkippedPattern(
                        item.pattern_key,
                        f"{len(existing)} attempts at this boundary have been turned "
                        "back; no further attempt is opened",
                    )
                )
                continue

            event = self.engine.open_event(
                instrument_id=instrument_id,
                timeframe=timeframe,
                boundary=item.boundary,
                session=as_of_session,
                attempt_number=attempt,
                pattern_detector_name=item.detector_name,
                pattern_detector_version=item.detector_version,
                pattern_config_digest=item.config_digest,
                data_snapshot_digest=context.data_snapshot_digest if context else "",
            )
            inputs = SessionInputs(
                bars=bars,
                as_of_session=as_of_session,
                knowledge_time=clock,
                context=context,
                pattern_length=item.pattern_length,
                consolidation_bars=consolidation,
                impulse_bars=impulse,
                is_partial=is_partial,
                observed_at=observed_at,
            )
            advanced = self.engine.advance(event, inputs)
            self.events[advanced.event_key] = advanced
            result.opened.append(advanced)
            result.updated.append(advanced)

        result.elapsed = time.perf_counter() - started
        return result

    def _attempts_for(
        self, instrument_id: int, timeframe: Bartimeframe, pattern_key: str
    ) -> list[BreakoutEvent]:
        return sorted(
            (
                event
                for event in self.events.values()
                if event.instrument_id == instrument_id
                and event.timeframe is timeframe
                and event.boundary.pattern_key == pattern_key
            ),
            key=lambda e: e.attempt_number,
        )

    def _boundary_defeated(
        self, existing: Sequence[BreakoutEvent], item: MonitoredBoundary
    ) -> bool:
        """Whether the level has turned back enough attempts to stop trying.

        Counts rejections, not attempts: an attempt that closed above and then
        failed is a different fact from one the level never let through, and only
        the second says the boundary is winning.
        """
        rejected = [e for e in existing if e.state is BreakoutState.REJECTED]
        return len(rejected) >= self.config.rejection.max_rejections

    def close_defeated_boundaries(
        self, as_of_session: dt.date, knowledge_time: dt.datetime | None = None
    ) -> list[BreakoutEvent]:
        """Fail the last rejected attempt at any boundary that has won.

        Called by the caller's end-of-session sweep rather than inside
        ``observe``, so that the decision is visible in the schedule rather than
        buried in a loop.
        """
        clock = knowledge_time or dt.datetime.combine(as_of_session, dt.time(23, 59), tzinfo=dt.UTC)
        closed: list[BreakoutEvent] = []
        groups: dict[tuple[int, str, str], list[BreakoutEvent]] = {}
        for event in self.events.values():
            key = (event.instrument_id, str(event.timeframe), event.boundary.pattern_key)
            groups.setdefault(key, []).append(event)

        for members in groups.values():
            rejected = [e for e in members if e.state is BreakoutState.REJECTED]
            if len(rejected) < self.config.rejection.max_rejections:
                continue
            last = max(rejected, key=lambda e: e.attempt_number)
            failed = self.engine.fail_repeated_rejection(last, as_of_session, clock)
            self.events[failed.event_key] = failed
            closed.append(failed)
        return closed

    # -- views ---------------------------------------------------------------

    def active_events(self) -> list[BreakoutEvent]:
        return [e for e in self.events.values() if e.is_active]

    def events_for(self, instrument_id: int) -> list[BreakoutEvent]:
        return [e for e in self.events.values() if e.instrument_id == instrument_id]

    def summary(self) -> dict[str, int]:
        counts = _state_counts(list(self.events.values()))
        counts["total"] = len(self.events)
        return counts


def _legs(
    bars: Sequence[OhlcvBar],
    segments: Mapping[str, tuple[dt.date, dt.date]] | None,
) -> tuple[list[OhlcvBar], list[OhlcvBar]]:
    """Slice the pattern's consolidation and impulse legs out of the series.

    The segment names are the detectors' own vocabulary, which differs by
    family: a flag has a ``flagpole`` and a ``consolidation``, a cup has a
    ``cup`` and a ``handle``. The mapping below is a translation table rather
    than a guess — an unrecognised family yields empty legs, which shows up as
    two absent volume comparisons rather than as a comparison against the wrong
    bars.
    """
    if not segments:
        return [], []
    consolidation_names = ("consolidation", "handle", "base", "flag", "second_base")
    impulse_names = ("flagpole", "impulse", "advance", "prior_advance", "left_side")

    def slice_for(names: Sequence[str]) -> list[OhlcvBar]:
        for name in names:
            span = segments.get(name)
            if span is None:
                continue
            start, end = span
            return [bar for bar in bars if start <= bar.session_date <= end]
        return []

    return slice_for(consolidation_names), slice_for(impulse_names)


__all__ = [
    "BreakoutMonitor",
    "MonitorResult",
    "MonitoredBoundary",
    "SkippedPattern",
]
