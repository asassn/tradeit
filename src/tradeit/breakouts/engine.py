"""The breakout engine: one event, advanced one session at a time.

Everything the engine does is a function of (the event as it stood yesterday,
the bars up to and including today). That signature is the causality
architecture: there is no argument through which a future bar could arrive, and
the incremental path is not an optimisation of a batch path — it *is* the path,
with full replay defined as calling it repeatedly.

**What the engine does not do**, stated here because this is the layer where
scope creep would be easiest and most damaging:

* It does not decide whether to buy anything, and holds no field that could
  express one.
* It does not size, stop, or target.
* It does not consult fundamentals.
* It does not rank events against each other.
* It does not reject a breakout because of the market regime. Regime is stored
  as context and weighted at 6% of a *characterisation* score; whether a bear
  market disqualifies a setup is Phase 7's judgement to make with the evidence
  Phase 5 hands it.

**Three orderings inside ``advance`` are deliberate and load-bearing:**

1. **Failure is checked before confirmation.** An event that closed decisively
   back inside the pattern today does not get to confirm on the strength of
   yesterday's follow-through.
2. **Confirmation is checked before expiration.** An event that satisfies its
   profile on the last session of its window has confirmed, not expired.
3. **Quality is computed once, on the session of the first qualifying close,
   and never again.** Every later observation carries the same number. This is
   what makes hindsight structurally impossible rather than merely tested
   against.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from tradeit.breakouts.base import (
    BreakoutEvent,
    BreakoutObservation,
    ConfirmationPath,
    RetestRecord,
    event_identity,
    merge_evidence,
)
from tradeit.breakouts.boundary import BreakoutBoundary
from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.context import BreakoutContext, measure_relative_strength
from tradeit.breakouts.lifecycle import (
    BreakoutState,
    TransitionReason,
    terminal_state_for_invalidation,
)
from tradeit.breakouts.measures import (
    AcceptanceReading,
    ApproachReading,
    CandleReading,
    FollowThroughReading,
    PenetrationReading,
    boundary_quality_score,
    measure_acceptance,
    measure_approach,
    measure_candle,
    measure_follow_through,
    measure_penetration,
    measure_rejection,
)
from tradeit.breakouts.profiles import PathEvidence, evaluate_profile
from tradeit.breakouts.retest import build_retest, is_pullback, retest_failed
from tradeit.breakouts.retest import measurements as retest_measurements
from tradeit.breakouts.scoring import (
    BREAKOUT_SCORER_VERSION,
    ConfirmationInputs,
    QualityInputs,
    ScoreResult,
    breakout_confidence,
    score_breakout_quality,
    score_confirmation,
)
from tradeit.breakouts.volume import (
    IntradayVolumeCurve,
    VolumeScores,
    measure_volume,
    score_volume,
)
from tradeit.core.enums import Bartimeframe
from tradeit.core.models import OhlcvBar
from tradeit.errors import ConfigError
from tradeit.patterns.base import ComponentScore, Evidence, EvidenceKind
from tradeit.patterns.scoring import ramp_score


@dataclass(frozen=True, slots=True)
class SessionInputs:
    """One session's worth of everything the engine may read.

    A single record rather than a dozen keyword arguments so that "what can the
    engine see?" is answerable by reading one type. Every optional field has a
    defined absent behaviour that reduces coverage rather than inventing a
    value.
    """

    bars: Sequence[OhlcvBar]
    as_of_session: dt.date
    knowledge_time: dt.datetime
    context: BreakoutContext | None = None
    #: Whether the underlying pattern was structurally invalidated this session.
    #: Supplied by the caller because only the pattern layer can know it.
    pattern_invalidated: bool = False
    #: Sessions the underlying pattern spans, for the length-relative clock.
    pattern_length: int | None = None
    #: Bars of the pattern's consolidation and impulse legs, for the volume
    #: comparisons that survive an instrument whose whole profile shifted.
    consolidation_bars: Sequence[OhlcvBar] = ()
    impulse_bars: Sequence[OhlcvBar] = ()
    #: True when the final bar has not closed. Everything volume-related then
    #: routes through the projection path, labelled as such.
    is_partial: bool = False
    observed_at: dt.time | None = None
    intraday_curve: IntradayVolumeCurve | None = None

    def __post_init__(self) -> None:
        if not self.bars:
            raise ConfigError("a session evaluation needs at least one bar")
        if self.bars[-1].session_date != self.as_of_session:
            raise ConfigError(
                f"bars end on {self.bars[-1].session_date} but the evaluation "
                f"session is {self.as_of_session}: the engine evaluates the last "
                "bar it is given, so a mismatch means it would score the wrong day"
            )
        if any(bar.session_date > self.as_of_session for bar in self.bars):
            raise ConfigError("the supplied series contains bars after the evaluation session")


#: Measurement keys that belong to the penetration component.
_PENETRATION_KEYS = ("close_above", "high_above", "penetration", "extension", "open_gap")


@dataclass(slots=True)
class _Working:
    """Mutable scratch for one call to ``advance``. Never escapes the method."""

    state: BreakoutState
    reason: TransitionReason
    path: ConfirmationPath = ConfirmationPath.NONE
    note: str = ""
    updates: dict[str, object] = field(default_factory=dict)


class BreakoutEngine:
    """Evaluates one breakout event against one session at a time."""

    def __init__(
        self,
        config: BreakoutEngineConfig | None = None,
        *,
        profile: str | None = None,
    ) -> None:
        self.config = config or BreakoutEngineConfig()
        self.profile_name = profile or self.config.active_profile
        # Validates the name now rather than on the first confirmation.
        self.config.profile(self.profile_name)

    # -- opening -------------------------------------------------------------

    def open_event(
        self,
        *,
        instrument_id: int,
        timeframe: Bartimeframe,
        boundary: BreakoutBoundary,
        session: dt.date,
        attempt_number: int = 1,
        pattern_detector_name: str = "",
        pattern_detector_version: int = 0,
        pattern_config_digest: str = "",
        data_snapshot_digest: str = "",
    ) -> BreakoutEvent:
        """Mint an event for one attempt at one boundary.

        The event opens in ``NOT_APPROACHING``; the first call to ``advance``
        classifies where price actually is. Opening in a state derived from the
        current bar would mean the event's first recorded state depended on when
        the monitor happened to notice it, which is a fact about the scheduler
        rather than about the market.
        """
        key = event_identity(
            instrument_id=instrument_id,
            timeframe=timeframe,
            pattern_key=boundary.pattern_key,
            boundary=boundary,
            attempt_number=attempt_number,
        )
        return BreakoutEvent(
            event_key=key,
            instrument_id=instrument_id,
            timeframe=timeframe,
            boundary=boundary,
            attempt_number=attempt_number,
            opened_session=session,
            state=BreakoutState.NOT_APPROACHING,
            pattern_detector_name=pattern_detector_name,
            pattern_detector_version=pattern_detector_version,
            pattern_config_digest=pattern_config_digest,
            breakout_config_digest=self.config_digest(),
            scorer_version=BREAKOUT_SCORER_VERSION,
            data_snapshot_digest=data_snapshot_digest,
            profile_name=self.profile_name,
        )

    def config_digest(self) -> str:
        from tradeit.reproducibility.versioning import content_hash

        return content_hash(self.config.model_dump(mode="json"))[:32]

    # -- advancing -----------------------------------------------------------

    def advance(self, event: BreakoutEvent, inputs: SessionInputs) -> BreakoutEvent:
        """Evaluate one session and return the event with it appended.

        Idempotent in the sense that matters: calling it twice for the same
        session raises rather than double-appending, because a history with a
        duplicated session cannot be replayed against the prefix that produced
        it.
        """
        if event.state.is_terminal:
            return event
        if event.observations and inputs.as_of_session <= event.last_session:
            raise ConfigError(
                f"event {event.event_key} already has an observation on or after "
                f"{inputs.as_of_session}; appending would break prefix replay"
            )

        bars = inputs.bars
        bar = bars[-1]
        previous_close = float(bars[-2].close) if len(bars) > 1 else None
        offset = _sessions_from_anchor(bars, event.boundary.anchor_date)

        penetration = measure_penetration(
            bar,
            previous_close,
            event.boundary,
            config=self.config.penetration,
            sessions_from_anchor=offset,
        )
        approach = measure_approach(
            bars, event.boundary, config=self.config.approach, sessions_from_anchor=offset
        )
        candle = measure_candle(
            bar,
            previous_close,
            atr=event.boundary.atr_at_open,
            config=self.config.candle,
        )
        volume_reading = measure_volume(
            bars,
            config=self.config.volume,
            intraday_config=self.config.intraday,
            is_partial=inputs.is_partial,
            observed_at=inputs.observed_at,
            curve=inputs.intraday_curve,
            consolidation_bars=inputs.consolidation_bars,
            impulse_bars=inputs.impulse_bars,
        )
        volume_scores = score_volume(
            volume_reading,
            config=self.config.volume,
            family=event.pattern_detector_name or None,
        )

        working = _Working(state=event.state, reason=TransitionReason.HELD)
        measurements: dict[str, float] = {}
        measurements.update(approach.to_measurements())
        measurements.update(penetration.to_measurements())
        measurements.update(candle.to_measurements())
        measurements.update(volume_scores.measurements)

        # --- pattern invalidation short-circuits everything ------------------
        if inputs.pattern_invalidated:
            return self._finish(
                event,
                inputs,
                working=_Working(
                    state=terminal_state_for_invalidation(event.state),
                    reason=TransitionReason.PATTERN_INVALIDATED,
                    note=(
                        "the underlying pattern was structurally invalidated; the "
                        "breakout stays attached to it rather than being detached"
                    ),
                    updates={"terminal_reason": TransitionReason.PATTERN_INVALIDATED},
                ),
                measurements=measurements,
                penetration=penetration,
                volume_scores=volume_scores,
            )

        if event.state.has_broken_out:
            self._advance_post_breakout(event, inputs, working, measurements, offset)
        else:
            self._advance_pre_breakout(
                event,
                inputs,
                working,
                measurements,
                offset,
                penetration=penetration,
                approach=approach,
                candle=candle,
            )

        return self._finish(
            event,
            inputs,
            working=working,
            measurements=measurements,
            penetration=penetration,
            volume_scores=volume_scores,
        )

    # -- pre-breakout --------------------------------------------------------

    def _advance_pre_breakout(
        self,
        event: BreakoutEvent,
        inputs: SessionInputs,
        working: _Working,
        measurements: dict[str, float],
        offset: int,
        *,
        penetration: PenetrationReading,
        approach: ApproachReading,
        candle: CandleReading,
    ) -> None:
        bar = inputs.bars[-1]
        relative_volume = measurements.get("relative_volume") or measurements.get(
            "projected_relative_volume"
        )

        if penetration.closed_above:
            working.state = BreakoutState.CLOSED_ABOVE
            working.reason = TransitionReason.QUALIFYING_CLOSE
            working.updates.update(
                {
                    "first_qualifying_close_session": inputs.as_of_session,
                    "breakout_close": float(bar.close),
                    "breakout_low": float(bar.low),
                    "breakout_volume": float(bar.volume),
                    "peak_close_since_breakout": float(bar.close),
                    # Deliberately *not* seeded with the breakout bar's own low.
                    # Adverse excursion means how far price went against the
                    # breakout after it; seeding it here would charge the event
                    # for the lower wick of the very bar that made it, and any
                    # wide-range breakout would fail itself on the next session.
                    "lowest_low_since_breakout": None,
                    "qualifying_closes": 1,
                    "consecutive_closes_above": 1,
                    "gap_class": penetration.gap_class,
                }
            )
            if event.first_penetration_session is None:
                working.updates["first_penetration_session"] = inputs.as_of_session
            if event.first_approach_session is None:
                working.updates["first_approach_session"] = inputs.as_of_session
            return

        rejection = measure_rejection(
            bar,
            event.boundary,
            relative_volume=relative_volume,
            config_wick=self.config.rejection.wick_fraction,
            config_close_ceiling=self.config.rejection.close_location_ceiling,
            heavy_volume=self.config.rejection.heavy_volume,
            sessions_from_anchor=offset,
        )
        measurements.update(rejection.to_measurements())

        if rejection.rejected:
            working.state = BreakoutState.REJECTED
            working.reason = TransitionReason.REJECTED_AT_BOUNDARY
            working.note = (
                "price cleared the boundary intraday and closed back below it; the "
                "attempt is resolved and a later qualifying close opens a new one"
            )
            working.updates["rejection_count"] = event.rejection_count + 1
            if event.first_penetration_session is None:
                working.updates["first_penetration_session"] = inputs.as_of_session
            return

        if penetration.penetrated_intraday:
            working.state = BreakoutState.INTRADAY_BREAK
            working.reason = TransitionReason.PENETRATED_INTRADAY
            if event.first_penetration_session is None:
                working.updates["first_penetration_session"] = inputs.as_of_session
        elif approach.is_testing:
            working.state = BreakoutState.TESTING_RESISTANCE
            working.reason = TransitionReason.TOUCHED_BOUNDARY
        elif approach.is_approaching:
            working.state = BreakoutState.APPROACHING
            working.reason = (
                TransitionReason.ENTERED_APPROACH
                if event.state is not BreakoutState.APPROACHING
                else TransitionReason.HELD
            )
            if event.first_approach_session is None:
                working.updates["first_approach_session"] = inputs.as_of_session
        else:
            working.state = BreakoutState.NOT_APPROACHING
            working.reason = (
                TransitionReason.LEFT_APPROACH
                if event.state is not BreakoutState.NOT_APPROACHING
                else TransitionReason.HELD
            )

        # Expiration is checked last so that a session which produced a real
        # transition is recorded as that transition, not as a timeout.
        elapsed = _sessions_between(inputs.bars, event.opened_session, inputs.as_of_session)
        if elapsed > self.config.expiration.max_sessions_approaching:
            working.state = BreakoutState.EXPIRED
            working.reason = TransitionReason.TIMED_OUT
            working.note = (
                f"{elapsed} sessions without a qualifying close; nothing happened, "
                "which is not the same as a failure"
            )
            working.updates["terminal_reason"] = TransitionReason.TIMED_OUT

    # -- post-breakout -------------------------------------------------------

    def _advance_post_breakout(
        self,
        event: BreakoutEvent,
        inputs: SessionInputs,
        working: _Working,
        measurements: dict[str, float],
        offset: int,
    ) -> None:
        bar = inputs.bars[-1]
        close, low = float(bar.close), float(bar.low)
        boundary = event.boundary
        atr = boundary.atr_at_open
        level = boundary.level_on(offset)
        floor = boundary.floor(offset)
        threshold = boundary.threshold(offset)

        peak = max(event.peak_close_since_breakout or close, close)
        lowest = min(event.lowest_low_since_breakout or low, low)
        working.updates["peak_close_since_breakout"] = peak
        working.updates["lowest_low_since_breakout"] = lowest

        if close > threshold:
            working.updates["qualifying_closes"] = event.qualifying_closes + 1
            working.updates["consecutive_closes_above"] = event.consecutive_closes_above + 1
        else:
            working.updates["consecutive_closes_above"] = 0

        def fail(outcome: tuple[TransitionReason, str] | None) -> bool:
            if outcome is None:
                return False
            reason, note = outcome
            working.state = BreakoutState.FAILED_BREAKOUT
            working.reason = reason
            working.note = note
            working.updates["terminal_reason"] = reason
            working.updates["retest_active"] = False
            return True

        # --- unconditional failures ------------------------------------------
        # A close far below the level, or an adverse excursion past the cap, is
        # decisive whatever else is happening. Both thresholds sit above the
        # retest tolerances, which the engine config validates, so neither can
        # fire on behaviour a legitimate retest is allowed to produce.
        if fail(
            self._check_absolute_failure(event, close=close, level=level, atr=atr, lowest=lowest)
        ):
            return

        # --- retest ----------------------------------------------------------
        # Run before the structural failure rules, because deciding whether a
        # return to the level is a test or a break is precisely what the retest
        # engine is for. A generic "two closes back inside" rule evaluated first
        # would pre-empt that judgement and fail every retest that dipped for
        # two sessions, which is most of them.
        retest = self._update_retest(event, inputs, working, measurements, offset)
        if working.state.is_terminal:
            return

        retest_running = bool(
            working.updates.get("retest_active", event.retest_active)
        ) or working.state in (BreakoutState.RETEST_PENDING, BreakoutState.RETEST_HOLDING)
        if not retest_running and fail(
            self._check_structural_failure(event, inputs, close=close, low=low, floor=floor)
        ):
            return

        # --- confirmation ----------------------------------------------------
        breakout_bars = _bars_from(inputs.bars, event.first_qualifying_close_session)
        acceptance = measure_acceptance(
            breakout_bars,
            boundary,
            target_closes=self.config.profile(self.profile_name).acceptance_closes,
        )
        measurements.update(acceptance.to_measurements())
        follow_through = measure_follow_through(
            breakout_bars[1:],
            breakout_close=event.breakout_close or close,
            breakout_volume=event.breakout_volume or 0.0,
            atr=atr,
            config=self.config.follow_through,
        )
        measurements.update(follow_through.to_measurements())

        confirmation = self._confirmation_result(
            event, inputs, acceptance, follow_through, retest, measurements
        )
        working.updates["_confirmation"] = confirmation

        profile = self.config.profile(self.profile_name)
        evidence = PathEvidence(
            qualifying_closes=_as_int(
                working.updates, "qualifying_closes", event.qualifying_closes
            ),
            consecutive_closes=_as_int(
                working.updates, "consecutive_closes_above", event.consecutive_closes_above
            ),
            breakout_quality=event.breakout_quality,
            # The *breakout bar's* relative volume, read from the frozen
            # component rather than from today's measurements. Using today's
            # would let an event whose breakout came on half its average volume
            # satisfy a volume requirement days later on an unrelated busy
            # session, which is not what "the breakout had volume" means.
            relative_volume=_frozen_relative_volume(event),
            acceptance=acceptance,
            follow_through=follow_through,
            retest=retest,
            evidence_coverage=_merged_coverage(event.quality_components, confirmation),
            sessions_since_breakout=max(0, len(breakout_bars) - 1),
        )
        decision = evaluate_profile(evidence, profile)

        # A retest in progress owns the state machine: an event cannot be both
        # holding a retest and confirming by momentum in the same session.
        if working.state in (BreakoutState.RETEST_PENDING, BreakoutState.RETEST_HOLDING):
            self._check_expiration(event, inputs, working, breakout_bars)
            return

        if working.state is BreakoutState.RETEST_CONFIRMED:
            # The session on which the retest resolves records RETEST_CONFIRMED
            # and nothing else. Collapsing that into CONFIRMED on the same bar
            # would erase the state from every history, and "how many breakouts
            # were confirmed by a retest that visibly held?" would have no row
            # to count.
            if event.state is BreakoutState.RETEST_CONFIRMED:
                if decision.confirmed:
                    working.state = BreakoutState.CONFIRMED
                    working.reason = TransitionReason.CONFIRMED_BY_PATH
                    working.path = decision.path
                    working.updates["confirmed_path"] = decision.path
                    working.updates["confirmed_session"] = inputs.as_of_session
                else:
                    working.state = BreakoutState.CONFIRMATION_PENDING
                    working.reason = TransitionReason.AWAITING_EVIDENCE
                    working.note = decision.reason()
            self._check_expiration(event, inputs, working, breakout_bars)
            return

        if decision.confirmed and event.state is not BreakoutState.CONFIRMED:
            working.state = BreakoutState.CONFIRMED
            working.reason = TransitionReason.CONFIRMED_BY_PATH
            working.path = decision.path
            working.updates["confirmed_path"] = decision.path
            working.updates["confirmed_session"] = inputs.as_of_session
        elif event.state is BreakoutState.CONFIRMED:
            working.state = BreakoutState.CONFIRMED
            working.reason = TransitionReason.HELD
        else:
            working.state = BreakoutState.CONFIRMATION_PENDING
            working.reason = TransitionReason.AWAITING_EVIDENCE
            working.note = decision.reason()

        self._check_expiration(event, inputs, working, breakout_bars)

    def _check_absolute_failure(
        self,
        event: BreakoutEvent,
        *,
        close: float,
        level: float,
        atr: float | None,
        lowest: float,
    ) -> tuple[TransitionReason, str] | None:
        """Failures that hold regardless of what else the event is doing.

        Both thresholds are configured above the retest tolerances, so neither
        can fire on behaviour a legitimate retest is permitted to produce —
        :class:`~tradeit.breakouts.config.BreakoutEngineConfig` validates the
        ordering rather than leaving it to two numbers agreeing by luck.
        """
        config = self.config.failure
        if atr and (level - close) / atr >= config.decisive_close_atr:
            return (
                TransitionReason.CLOSED_BACK_INSIDE,
                f"closed {(level - close) / atr:.2f} ATR below the boundary, past the "
                f"{config.decisive_close_atr:.2f} ATR decisive threshold",
            )
        if atr and event.breakout_close is not None:
            adverse = (event.breakout_close - lowest) / atr
            if adverse >= config.max_adverse_atr:
                return (
                    TransitionReason.ADVERSE_EXCURSION,
                    f"adverse excursion of {adverse:.2f} ATR from the breakout close",
                )
        return None

    def _check_structural_failure(
        self,
        event: BreakoutEvent,
        inputs: SessionInputs,
        *,
        close: float,
        low: float,
        floor: float,
    ) -> tuple[TransitionReason, str] | None:
        """Failures that apply only when no retest is running.

        Both rules describe price settling back inside the pattern. Inside a
        retest that is precisely the behaviour under adjudication, and the
        retest engine — which is volatility-aware and counts sessions below the
        level — is the thing qualified to adjudicate it.
        """
        config = self.config.failure
        recent = _bars_from(inputs.bars, event.first_qualifying_close_session)
        below = 0
        for candidate in reversed(recent):
            if float(candidate.close) < floor:
                below += 1
            else:
                break
        if below >= config.closes_back_inside:
            return (
                TransitionReason.CLOSED_BACK_INSIDE,
                f"{below} consecutive closes back inside the pattern",
            )

        if (
            event.breakout_low is not None
            and low < event.breakout_low
            and (not config.require_below_zone_with_bar_low or close < floor)
        ):
            return (
                TransitionReason.BROKE_BREAKOUT_LOW,
                "traded below the breakout bar's low and closed back inside the zone",
            )
        return None

    def _update_retest(
        self,
        event: BreakoutEvent,
        inputs: SessionInputs,
        working: _Working,
        measurements: dict[str, float],
        offset: int,
    ) -> RetestRecord | None:
        """Start, continue or resolve a retest, and set the state accordingly.

        The three retest states mean three different things and the engine keeps
        them that way:

        * **PENDING** — price has pulled back materially from the breakout close
          but has not reached the level. Nothing has been tested yet.
        * **HOLDING** — price has traded into the tolerance zone and has not
          broken it. This is the test.
        * **CONFIRMED** — after reaching the zone, price closed back above the
          level.

        A pullback that turns back up before ever reaching the zone is
        *abandoned*, not confirmed. Recording it as a successful retest would
        credit the level with holding a test it never received, and since
        shallow pullbacks are far more common than deep ones, that single
        shortcut would make the retest path the dominant route to confirmation
        for reasons that have nothing to do with the market.
        """
        bar = inputs.bars[-1]
        boundary = event.boundary
        config = self.config.retest
        atr = boundary.atr_at_open
        breakout_close = event.breakout_close or float(bar.close)
        zone_high = boundary.zone(offset)[1]

        if event.state is BreakoutState.RETEST_CONFIRMED:
            # A resolved retest hands the event back to the confirmation track;
            # a fresh pullback is a new episode and can only begin from there.
            working.state = BreakoutState.RETEST_CONFIRMED
            working.reason = TransitionReason.HELD
            return event.retest

        active = event.retest_active
        started = event.retest.started_session if (event.retest and active) else None

        if not active:
            if not is_pullback(bar, breakout_close=breakout_close, atr=atr, config=config):
                return event.retest
            started = inputs.as_of_session
            working.updates["retest_active"] = True

        assert started is not None
        window = _bars_from(inputs.bars, started)
        record = build_retest(
            window,
            boundary,
            started_session=started,
            breakout_volume=event.breakout_volume or 0.0,
            config=config,
        )
        measurements.update(retest_measurements(record))
        working.updates["retest"] = record

        if retest_failed(record, config=config):
            working.state = BreakoutState.FAILED_BREAKOUT
            working.reason = TransitionReason.CLOSED_BACK_INSIDE
            working.note = (
                f"retest undercut the level by {record.max_undercut_atr:.2f} ATR over "
                f"{record.sessions_below_level} session(s) below it"
            )
            working.updates["terminal_reason"] = TransitionReason.CLOSED_BACK_INSIDE
            working.updates["retest_active"] = False
            return record

        reached_zone = record.low <= zone_high
        previous = event.state

        if previous is BreakoutState.RETEST_HOLDING:
            if record.held:
                working.state = BreakoutState.RETEST_CONFIRMED
                working.reason = TransitionReason.RETEST_RECOVERED
                working.updates["retest_active"] = False
            else:
                working.state = BreakoutState.RETEST_HOLDING
                working.reason = TransitionReason.HELD
            return record

        if reached_zone:
            working.state = BreakoutState.RETEST_HOLDING
            working.reason = TransitionReason.RETEST_REACHED_LEVEL
            return record

        if previous is BreakoutState.RETEST_PENDING and float(bar.close) >= breakout_close:
            # The pullback reversed without the level ever being tested. Hand
            # the event back to the confirmation track rather than crediting the
            # level with holding a test it never received.
            working.updates["retest_active"] = False
            working.state = BreakoutState.CONFIRMATION_PENDING
            working.note = "pullback reversed before reaching the boundary; no retest occurred"
            return record

        working.state = BreakoutState.RETEST_PENDING
        working.reason = (
            TransitionReason.HELD
            if previous is BreakoutState.RETEST_PENDING
            else TransitionReason.PULLBACK_STARTED
        )
        return record

    def _check_expiration(
        self,
        event: BreakoutEvent,
        inputs: SessionInputs,
        working: _Working,
        breakout_bars: Sequence[OhlcvBar],
    ) -> None:
        """Time limits, applied after every other classification.

        Expiration never overrides a failure or a confirmation reached this
        session: an event that confirmed on the final session of its window
        confirmed, and one that failed on it failed. Only a session that
        produced nothing decisive can time out.
        """
        if working.state.is_terminal:
            return
        config = self.config.expiration
        since_breakout = max(0, len(breakout_bars) - 1)

        limit: int | None = None
        note = ""
        if working.state is BreakoutState.CONFIRMATION_PENDING:
            limit, note = config.max_sessions_pending, "confirmation window elapsed"
        elif working.state is BreakoutState.CONFIRMED:
            limit, note = (
                config.max_sessions_confirmed,
                "monitored to the end of its window; resolved rather than failed",
            )
        elif (
            working.state.is_retesting
            and event.retest is not None
            and event.retest.sessions > config.max_sessions_retesting
        ):
            working.state = BreakoutState.EXPIRED
            working.reason = TransitionReason.TIMED_OUT
            working.note = (
                f"{event.retest.sessions} sessions of pullback; this has become a "
                "new consolidation rather than a retest of the breakout"
            )
            working.updates["terminal_reason"] = TransitionReason.TIMED_OUT
            return

        if inputs.pattern_length:
            length_cap = int(inputs.pattern_length * config.pattern_length_multiple)
            limit = length_cap if limit is None else min(limit, length_cap)
            note = note or "elapsed beyond the underlying pattern's own length"

        if limit is not None and since_breakout > limit:
            working.state = BreakoutState.EXPIRED
            working.reason = (
                TransitionReason.RESOLVED
                if event.state is BreakoutState.CONFIRMED
                else TransitionReason.TIMED_OUT
            )
            working.note = note
            working.updates["terminal_reason"] = working.reason

    # -- scoring -------------------------------------------------------------

    def _quality_result(
        self,
        event: BreakoutEvent,
        inputs: SessionInputs,
        measurements: dict[str, float],
        volume_scores: VolumeScores,
    ) -> ScoreResult:
        """The frozen quality composite, computed on the breakout session only."""
        boundary_score, boundary_measurements = boundary_quality_score(event.boundary)
        context = inputs.context or BreakoutContext()

        rs = measure_relative_strength(
            [float(b.close) for b in inputs.bars],
            context.benchmark_closes,
            universe_percentile=context.rs_universe_percentile,
        )
        rs_score = rs.score()
        market_score = context.market.score()
        sector_score = context.sector.score()

        inputs_ = QualityInputs(
            boundary_quality=boundary_score,
            boundary_measurements=boundary_measurements,
            penetration_score=measurements.get("penetration_score", 0.0),
            extension_score=measurements.get("extension_score", 100.0),
            penetration_measurements={
                k: v
                for k, v in measurements.items()
                if k.startswith(
                    ("close_above", "high_above", "penetration", "extension", "open_gap")
                )
            },
            candle_score=measurements.get("breakout_candle_score", 0.0),
            candle_measurements={
                k: v
                for k, v in measurements.items()
                if k
                in (
                    "close_location",
                    "body_fraction",
                    "upper_wick_fraction",
                    "lower_wick_fraction",
                    "range_over_atr",
                    "close_strength_score",
                    "breakout_candle_score",
                )
            },
            volume_score=volume_scores.confirmation,
            volume_measurements=dict(volume_scores.measurements),
            volume_unavailable_reason=volume_scores.unavailable_reason,
            rs_score=rs_score,
            rs_measurements=rs.to_measurements(),
            rs_unavailable_reason=rs.unavailable_reason,
            market_score=market_score,
            market_measurements=context.market.to_measurements(),
            market_unavailable_reason=(
                context.market.unavailable_reason or "no market regime state supplied"
            ),
            sector_score=sector_score,
            sector_measurements=context.sector.to_measurements(),
            sector_unavailable_reason=(
                context.sector.unavailable_reason or "no sector data supplied"
            ),
            volume_from_projection=volume_scores.measurements.get("volume_from_projection") == 1.0,
        )
        return score_breakout_quality(inputs_, weights=self.config.quality_weights)

    def _confirmation_result(
        self,
        event: BreakoutEvent,
        inputs: SessionInputs,
        acceptance: AcceptanceReading,
        follow_through: FollowThroughReading,
        retest: RetestRecord | None,
        measurements: dict[str, float],
    ) -> ScoreResult:
        context = inputs.context or BreakoutContext()

        breakout_bars = _bars_from(inputs.bars, event.first_qualifying_close_session)
        # Post-breakout volume against the *pre-breakout* baseline. Dividing by
        # the breakout bar's own volume — the obvious implementation — rewards a
        # low-volume breakout for having had little volume to fall from, which
        # inverts the measurement it is supposed to make.
        volume_persistence = None
        volume_measurements: dict[str, float] = {}
        before = [
            bar
            for bar in inputs.bars
            if event.first_qualifying_close_session is not None
            and bar.session_date < event.first_qualifying_close_session
        ][-self.config.volume.average_period :]
        if len(breakout_bars) > 1 and before:
            baseline = float(np.mean([float(b.volume) for b in before]))
            after = breakout_bars[1:]
            if baseline > 0:
                ratio = float(np.mean([float(b.volume) for b in after])) / baseline
                volume_persistence = ramp_score(
                    ratio,
                    zero_at=self.config.volume.persistence_zero,
                    full_at=self.config.volume.persistence_full,
                )
                volume_measurements = {
                    "post_breakout_volume_ratio": ratio,
                    "pre_breakout_average_volume": baseline,
                    "volume_persistence_score": volume_persistence,
                }

        rs_after_score = None
        rs_after_measurements: dict[str, float] = {}
        rs_reason = "no benchmark series supplied"
        if context.benchmark_closes is not None:
            breakout_index = _index_of(inputs.bars, event.first_qualifying_close_session)
            rs = measure_relative_strength(
                [float(b.close) for b in inputs.bars],
                context.benchmark_closes,
                breakout_index=breakout_index,
                universe_percentile=context.rs_universe_percentile,
            )
            rs_after_score = rs.score()
            rs_after_measurements = rs.to_measurements()
            rs_reason = rs.unavailable_reason

        result = score_confirmation(
            ConfirmationInputs(
                acceptance_score=acceptance.score,
                acceptance_measurements=acceptance.to_measurements(),
                follow_through_score=(follow_through.score if follow_through.available else None),
                follow_through_measurements=follow_through.to_measurements(),
                volume_persistence=volume_persistence,
                volume_measurements=volume_measurements,
                retest_score=None if retest is None else retest.quality,
                retest_measurements=None if retest is None else retest_measurements(retest),
                rs_after_score=rs_after_score,
                rs_after_measurements=rs_after_measurements,
                rs_after_unavailable_reason=rs_reason,
            ),
            weights=self.config.confirmation_weights,
        )
        measurements["confirmation_score"] = result.score
        return result

    # -- assembly ------------------------------------------------------------

    def _finish(
        self,
        event: BreakoutEvent,
        inputs: SessionInputs,
        *,
        working: _Working,
        measurements: dict[str, float],
        penetration: PenetrationReading,
        volume_scores: VolumeScores,
    ) -> BreakoutEvent:
        bar = inputs.bars[-1]
        updates = dict(working.updates)
        confirmation_result = updates.pop("_confirmation", None)

        quality_components = event.quality_components
        breakout_quality = event.breakout_quality
        if (
            working.state is BreakoutState.CLOSED_ABOVE
            and event.first_qualifying_close_session is None
        ):
            # The one session on which quality is computed. Every later
            # observation carries this same number.
            result = self._quality_result(event, inputs, measurements, volume_scores)
            quality_components = result.components
            breakout_quality = result.score
            updates["quality_components"] = result.components
            context = inputs.context or BreakoutContext()
            previous = inputs.bars[-2].session_date if len(inputs.bars) > 1 else None
            updates["earnings_context"] = context.earnings_context_for(
                inputs.as_of_session, previous
            )

        confirmation_components: tuple[ComponentScore, ...] = event.confirmation_components
        confirmation_score = event.confirmation_score
        if isinstance(confirmation_result, ScoreResult):
            confirmation_components = confirmation_result.components
            confirmation_score = confirmation_result.score
            updates["confirmation_components"] = confirmation_result.components
        elif not working.state.has_broken_out:
            confirmation_score = 0.0

        # Pre-breakout there are no components, so coverage is 0: no evidence
        # has been gathered, which is literally true and matches what
        # ``BreakoutEvent.evidence_coverage`` reports for the same event. The
        # tempting alternative — reporting 100 because nothing is due yet —
        # would make the observation and the event disagree, and a report table
        # mixing the two conventions is unreadable.
        coverage = _merged_coverage_from(quality_components, confirmation_components)

        confidence = breakout_confidence(
            boundary_confidence=event.boundary.confidence,
            boundary_touches=event.boundary.touch_count,
            pattern_quality=event.boundary.pattern_quality,
            is_attached=event.boundary.is_attached,
            quality_coverage=_coverage_of(quality_components) if quality_components else 100.0,
            close_above_threshold=penetration.closed_above,
            penetration_atr=penetration.penetration_atr,
            component_scores=[c.score for c in quality_components if not c.unavailable],
        )

        supporting, contradicting = merge_evidence((*quality_components, *confirmation_components))
        if working.state is BreakoutState.CONFIRMATION_PENDING and working.note:
            contradicting = (
                *contradicting,
                Evidence(working.note, EvidenceKind.STRUCTURAL),
            )

        observation = BreakoutObservation(
            session_date=inputs.as_of_session,
            knowledge_time=inputs.knowledge_time,
            state=working.state,
            reason=working.reason,
            breakout_quality=breakout_quality,
            confirmation_score=confirmation_score,
            evidence_coverage=coverage,
            confidence=confidence,
            close=float(bar.close),
            high=float(bar.high),
            low=float(bar.low),
            distance_pct=event.boundary.distance_pct(float(bar.close)),
            distance_atr=event.boundary.distance_atr(float(bar.close)),
            measurements=measurements,
            supporting=supporting,
            contradicting=contradicting,
            path=working.path,
            note=working.note,
        )
        updates["supporting_evidence"] = supporting
        updates["contradicting_evidence"] = contradicting
        return event.advanced(observation, **updates)

    # -- explicit closures ---------------------------------------------------

    def expire(
        self,
        event: BreakoutEvent,
        session: dt.date,
        knowledge_time: dt.datetime,
        *,
        reason: TransitionReason = TransitionReason.TIMED_OUT,
        note: str = "",
    ) -> BreakoutEvent:
        """Close an event the monitor is no longer tracking.

        Used for events whose pattern has gone stale and for resolved rejections
        the monitor has stopped following. Recorded as an observation like every
        other state change, so an expired event's history still ends with a row
        saying when and why.
        """
        if event.state.is_terminal:
            return event
        last = event.observations[-1] if event.observations else None
        observation = BreakoutObservation(
            session_date=session,
            knowledge_time=knowledge_time,
            state=BreakoutState.EXPIRED,
            reason=reason,
            breakout_quality=event.breakout_quality,
            confirmation_score=event.confirmation_score,
            evidence_coverage=last.evidence_coverage if last else 100.0,
            confidence=event.confidence,
            close=last.close if last else event.boundary.nominal,
            high=last.high if last else event.boundary.nominal,
            low=last.low if last else event.boundary.nominal,
            distance_pct=last.distance_pct if last else 0.0,
            distance_atr=last.distance_atr if last else None,
            note=note,
        )
        return event.advanced(observation, terminal_reason=reason)

    def fail_repeated_rejection(
        self,
        event: BreakoutEvent,
        session: dt.date,
        knowledge_time: dt.datetime,
    ) -> BreakoutEvent:
        """Close a rejected attempt as failed once the boundary has won.

        Reached when a boundary has turned back ``max_rejections`` attempts. The
        reading is that the level defeated the thesis rather than that nothing
        happened, which is why this ends as FAILED_BREAKOUT and not EXPIRED.
        """
        if event.state is not BreakoutState.REJECTED:
            raise ConfigError(
                f"event {event.event_key} is {event.state}, not rejected; "
                "repeated-rejection failure applies to a resolved rejection"
            )
        last = event.observations[-1] if event.observations else None
        observation = BreakoutObservation(
            session_date=session,
            knowledge_time=knowledge_time,
            state=BreakoutState.FAILED_BREAKOUT,
            reason=TransitionReason.REPEATED_REJECTION,
            breakout_quality=event.breakout_quality,
            confirmation_score=event.confirmation_score,
            evidence_coverage=last.evidence_coverage if last else 100.0,
            confidence=event.confidence,
            close=last.close if last else event.boundary.nominal,
            high=last.high if last else event.boundary.nominal,
            low=last.low if last else event.boundary.nominal,
            distance_pct=last.distance_pct if last else 0.0,
            distance_atr=last.distance_atr if last else None,
            note=(
                f"{event.attempt_number} attempts at this boundary have been turned "
                "back; the level has defeated the thesis rather than nothing having "
                "happened"
            ),
        )
        return event.advanced(observation, terminal_reason=TransitionReason.REPEATED_REJECTION)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frozen_relative_volume(event: BreakoutEvent) -> float | None:
    """The relative volume recorded on the breakout bar, or ``None``.

    Read from the frozen quality component, which is the only copy that cannot
    have moved since the breakout.
    """
    component = event.component("volume_confirmation")
    if component is None:
        return None
    value = component.measurements.get("relative_volume")
    if value is None:
        value = component.measurements.get("projected_relative_volume")
    return None if value is None else float(value)


def _as_int(updates: Mapping[str, object], key: str, fallback: int) -> int:
    """Read a pending integer update, falling back to the event's own value."""
    value = updates.get(key, fallback)
    return value if isinstance(value, int) else fallback


def _sessions_from_anchor(bars: Sequence[OhlcvBar], anchor: dt.date) -> int:
    """Trading sessions from the anchor to the last bar.

    Trading sessions rather than calendar days, for the reason ``Boundary``
    states: calendar arithmetic drifts across weekends and holidays, and a
    sloped boundary evaluated on drifted offsets is a different line.
    """
    index = None
    for position, bar in enumerate(bars):
        if bar.session_date <= anchor:
            index = position
        else:
            break
    if index is None:
        return 0
    return len(bars) - 1 - index


def _sessions_between(bars: Sequence[OhlcvBar], start: dt.date, end: dt.date) -> int:
    return sum(1 for bar in bars if start < bar.session_date <= end)


def _index_of(bars: Sequence[OhlcvBar], session: dt.date | None) -> int | None:
    if session is None:
        return None
    for position, bar in enumerate(bars):
        if bar.session_date == session:
            return position
    return None


def _bars_from(bars: Sequence[OhlcvBar], session: dt.date | None) -> list[OhlcvBar]:
    """Bars from ``session`` onward, inclusive. Empty when the date is unknown."""
    if session is None:
        return []
    return [bar for bar in bars if bar.session_date >= session]


def _coverage_of(components: Sequence[ComponentScore]) -> float:
    total = sum(c.weight for c in components)
    if total <= 0:
        return 0.0
    available = sum(c.weight for c in components if not c.unavailable)
    return available / total * 100.0


def _merged_coverage_from(
    quality: Sequence[ComponentScore], confirmation: Sequence[ComponentScore]
) -> float:
    merged = (*quality, *confirmation)
    return round(_coverage_of(merged), 6) if merged else 0.0


def _merged_coverage(quality: Sequence[ComponentScore], confirmation: ScoreResult | None) -> float:
    return _merged_coverage_from(
        quality, confirmation.components if confirmation is not None else ()
    )


__all__ = ["BreakoutEngine", "SessionInputs"]
