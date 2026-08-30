"""The breakout characterisation harness.

Characterise, do not optimise. Everything in ``docs/BREAKOUT_VALIDATION.md`` is
produced by this module, and the discipline it enforces is the same one Phase 4
established:

* **These are not trading success rates.** "62% of clean synthetic breakouts
  reached CONFIRMED" is a statement about the engine and a synthetic corpus. It
  says nothing about how often real breakouts work, and any sentence in the
  report that could be read that way is a bug in the report.
* **Nothing here is tuned against outcomes.** The harness reports distributions.
  It does not search thresholds, and it has no access to a return series to
  search them against.
* **Adversarial scenarios are characterised, not eliminated.** A confirmation
  rate above zero on random level crosses is a fact to be published, not a
  reason to move a threshold until it goes away.

The one shared helper worth noting is :func:`run_scenario`. Every test and every
report row goes through it, so the corpus is evaluated one way rather than
several subtly different ways — the failure mode where a test passes because it
warms up the boundary differently from the report.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from tradeit.breakouts.base import BreakoutEvent, ConfirmationPath
from tradeit.breakouts.boundary import BreakoutBoundary, tolerance_for
from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.context import BreakoutContext
from tradeit.breakouts.engine import BreakoutEngine, SessionInputs
from tradeit.breakouts.lifecycle import BreakoutState
from tradeit.breakouts.measures import average_true_range
from tradeit.breakouts.synthetic import (
    BreakoutGenerator,
    GeneratedBreakout,
    Scenario,
    all_scenarios,
)
from tradeit.core.enums import Bartimeframe

UTC = dt.UTC

#: Quality thresholds the report tabulates, cumulatively.
QUALITY_THRESHOLDS: tuple[float, ...] = (50.0, 60.0, 70.0, 80.0, 90.0)

#: Sessions of the series left before the event opens, so the engine sees the
#: approach rather than being introduced to the boundary at the breakout. Long
#: enough for ATR to have warmed up several times over.
WARMUP_SESSIONS = 45


@dataclass(frozen=True, slots=True)
class BoundarySpec:
    """How the harness attaches a boundary to a scenario.

    Two profiles, and the difference is the point of the
    ``poor_confidence_boundary`` scenario: identical price action against a
    five-touch, high-confidence, pattern-attached level and against a
    single-extreme, low-confidence, unattached one should produce a similar
    *quality* and a visibly different *confidence*.
    """

    confidence: float = 70.0
    touches: int = 4
    method: str = "swing_highs"
    pattern_key: str = "pattern-1"
    pattern_type: str = "flat_base"
    pattern_quality: float = 78.0
    detector_name: str = "flat_base"

    @classmethod
    def weak(cls) -> BoundarySpec:
        return cls(
            confidence=15.0,
            touches=1,
            method="single_extreme",
            pattern_key="",
            pattern_type="",
            pattern_quality=0.0,
            detector_name="",
        )


def build_boundary(
    scenario: GeneratedBreakout,
    *,
    config: BreakoutEngineConfig,
    spec: BoundarySpec,
    warmup_index: int,
) -> BreakoutBoundary:
    """Freeze a boundary from the bars available at ``warmup_index``.

    The ATR comes from that prefix and nothing later, which is what makes every
    ATR-relative figure on the resulting event a statement about conditions
    before the breakout rather than around it.
    """
    bars = scenario.bars[: warmup_index + 1]
    atr = average_true_range(bars, config.atr_period)
    return BreakoutBoundary(
        nominal=scenario.level,
        anchor_date=bars[-1].session_date,
        tolerance_pct=tolerance_for(
            level=scenario.level,
            atr=atr,
            confidence=spec.confidence,
            config=config.tolerance,
        ),
        confidence=spec.confidence,
        method=spec.method,
        touch_count=spec.touches,
        atr_at_open=atr,
        pattern_key=spec.pattern_key,
        pattern_type=spec.pattern_type,
        pattern_quality=spec.pattern_quality,
    )


def replay_scenario(
    scenario: GeneratedBreakout,
    *,
    config: BreakoutEngineConfig | None = None,
    profile: str | None = None,
    spec: BoundarySpec | None = None,
    context: BreakoutContext | None = None,
    invalidate_at: int | None = None,
    stop_at: int | None = None,
    warmup: int = WARMUP_SESSIONS,
    reopen: bool = True,
) -> list[BreakoutEvent]:
    """Replay one scenario session by session, returning every attempt.

    ``reopen`` mirrors what :class:`~tradeit.breakouts.monitor.BreakoutMonitor`
    does, and turning it off changes the meaning of every number computed from
    the result. A base that oscillates up to its level routinely turns one
    attempt back before the real break — that is the *point* of a resolved
    REJECTED state — so a harness that stopped at the first rejection would be
    characterising first attempts only, and would report a clean breakout series
    as having no breakout in it about a third of the time.

    Attempts are re-opened after REJECTED and EXPIRED, where nothing decisive
    happened, and **not** after FAILED_BREAKOUT: an event that broke out and
    gave it back is the outcome, and re-opening on the same series would let one
    scenario contribute several attempts to the failure statistics.

    ``stop_at`` truncates the replay, which is how the prefix-consistency tests
    compare "evaluated through session k" against "evaluated to the end and read
    back at session k". The truncation happens at the *loop*, not by slicing the
    output, so the engine genuinely never sees the later bars.
    """
    engine_config = config or BreakoutEngineConfig()
    engine = BreakoutEngine(engine_config, profile=profile)
    boundary_spec = spec or BoundarySpec()

    bars = list(scenario.bars)
    warmup_index = min(warmup, len(bars) - 2)
    boundary = build_boundary(
        scenario, config=engine_config, spec=boundary_spec, warmup_index=warmup_index
    )

    def open_attempt(number: int, session: dt.date) -> BreakoutEvent:
        return engine.open_event(
            instrument_id=bars[0].instrument_id,
            timeframe=Bartimeframe.D1,
            boundary=boundary,
            session=session,
            attempt_number=number,
            pattern_detector_name=boundary_spec.detector_name,
            pattern_detector_version=1,
        )

    attempts: list[BreakoutEvent] = []
    event = open_attempt(1, bars[warmup_index].session_date)
    rejections = 0

    last = len(bars) - 1 if stop_at is None else min(stop_at, len(bars) - 1)
    for index in range(warmup_index + 1, last + 1):
        session_bars = bars[: index + 1]
        event = engine.advance(
            event,
            SessionInputs(
                bars=session_bars,
                as_of_session=bars[index].session_date,
                knowledge_time=dt.datetime.combine(
                    bars[index].session_date, dt.time(22, 0), tzinfo=UTC
                ),
                context=_aligned_context(context, len(session_bars)),
                pattern_invalidated=invalidate_at is not None and index == invalidate_at,
            ),
        )
        if not event.state.is_resolved:
            continue

        attempts.append(event)
        if event.state is BreakoutState.REJECTED:
            rejections += 1
        reopenable = event.state in (BreakoutState.REJECTED, BreakoutState.EXPIRED)
        if (
            not reopen
            or not reopenable
            or rejections >= engine_config.rejection.max_rejections
            or index >= last
        ):
            break
        event = open_attempt(len(attempts) + 1, bars[index].session_date)

    if not attempts or attempts[-1] is not event:
        attempts.append(event)
    return attempts


def run_scenario(
    scenario: GeneratedBreakout,
    *,
    config: BreakoutEngineConfig | None = None,
    profile: str | None = None,
    spec: BoundarySpec | None = None,
    context: BreakoutContext | None = None,
    invalidate_at: int | None = None,
    stop_at: int | None = None,
    warmup: int = WARMUP_SESSIONS,
    reopen: bool = True,
) -> BreakoutEvent:
    """The last attempt from a replay: the one that resolved the scenario."""
    return replay_scenario(
        scenario,
        config=config,
        profile=profile,
        spec=spec,
        context=context,
        invalidate_at=invalidate_at,
        stop_at=stop_at,
        warmup=warmup,
        reopen=reopen,
    )[-1]


def _aligned_context(context: BreakoutContext | None, length: int) -> BreakoutContext | None:
    """Truncate a context's series to the visible window.

    Truncating from the *front* would misalign the benchmark against the price
    series; truncating from the back keeps session ``i`` matched to session
    ``i``. The engine also refuses a misaligned context outright, so a mistake
    here surfaces as an unavailable component rather than as a plausible wrong
    number.
    """
    if context is None:
        return None
    from dataclasses import replace

    return replace(
        context,
        benchmark_closes=(
            None if context.benchmark_closes is None else context.benchmark_closes[:length]
        ),
        sector_closes=(None if context.sector_closes is None else context.sector_closes[:length]),
    )


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """One replay's result, flattened for aggregation."""

    scenario: str
    seed: int
    adversarial: bool
    final_state: BreakoutState
    breakout_quality: float
    confirmation_score: float
    evidence_coverage: float
    confidence: float
    path: ConfirmationPath
    sessions: int
    attempts: int
    reached_close_above: bool
    ever_confirmed: bool
    ever_retested: bool

    @classmethod
    def of(
        cls, scenario: Scenario, seed: int, attempts: Sequence[BreakoutEvent]
    ) -> ScenarioOutcome:
        """Summarise a whole replay.

        The rates are taken over *all* attempts rather than the last, because
        "did this series produce a breakout?" is a question about the series. The
        scores come from the attempt that actually broke out, or from the last
        one when none did.
        """
        final = attempts[-1]
        broke_out = [event for event in attempts if event.has_broken_out]
        scored = broke_out[-1] if broke_out else final
        states = {observation.state for event in attempts for observation in event.observations}
        return cls(
            scenario=scenario.name,
            seed=seed,
            adversarial=scenario.adversarial,
            final_state=final.state,
            breakout_quality=scored.breakout_quality,
            confirmation_score=scored.confirmation_score,
            evidence_coverage=scored.evidence_coverage,
            confidence=scored.confidence,
            path=scored.confirmed_path,
            sessions=sum(event.sessions_observed for event in attempts),
            attempts=len(attempts),
            reached_close_above=bool(broke_out),
            ever_confirmed=BreakoutState.CONFIRMED in states,
            ever_retested=BreakoutState.RETEST_HOLDING in states,
        )


@dataclass(frozen=True, slots=True)
class ScenarioStatistics:
    """Distributions for one scenario across many seeds.

    Six rates rather than one accuracy, for the same reason Phase 4 reported
    four: candidate, confirmed, rejected, failed and expired are different
    outcomes with different meanings, and a single "success rate" would have to
    pick which of them counts as success — a decision Phase 5 is not entitled to
    make.
    """

    scenario: str
    intent: str
    adversarial: bool
    outcomes: tuple[ScenarioOutcome, ...] = field(default_factory=tuple)

    @property
    def trials(self) -> int:
        return len(self.outcomes)

    def _rate(self, predicate: object) -> float:
        assert callable(predicate)
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if predicate(o)) / self.trials

    @property
    def breakout_rate(self) -> float:
        """Fraction that produced a qualifying close. Not a success rate."""
        return self._rate(lambda o: o.reached_close_above)

    @property
    def confirmed_rate(self) -> float:
        return self._rate(lambda o: o.ever_confirmed)

    @property
    def rejected_rate(self) -> float:
        return self._rate(lambda o: o.final_state is BreakoutState.REJECTED)

    @property
    def failed_rate(self) -> float:
        return self._rate(lambda o: o.final_state is BreakoutState.FAILED_BREAKOUT)

    @property
    def expired_rate(self) -> float:
        return self._rate(lambda o: o.final_state is BreakoutState.EXPIRED)

    @property
    def retest_rate(self) -> float:
        return self._rate(lambda o: o.ever_retested)

    def high_quality_rate(self, threshold: float = 70.0) -> float:
        """Fraction whose *breakout* reached this quality, zeros included.

        Series that never broke out count as zero rather than being excluded.
        Excluding them would describe the subset that happened to fire, which is
        a different and flattering question.
        """
        return self._rate(lambda o: o.breakout_quality >= threshold)

    def _values(self, name: str) -> list[float]:
        return [float(getattr(o, name)) for o in self.outcomes]

    def percentile(self, q: float, field_name: str = "breakout_quality") -> float:
        values = self._values(field_name)
        return float(np.percentile(values, q)) if values else 0.0

    @property
    def median_quality(self) -> float:
        return self.percentile(50)

    @property
    def median_confirmation(self) -> float:
        return self.percentile(50, "confirmation_score")

    @property
    def median_coverage(self) -> float:
        return self.percentile(50, "evidence_coverage")

    @property
    def median_confidence(self) -> float:
        return self.percentile(50, "confidence")

    @property
    def mean_attempts(self) -> float:
        values = self._values("attempts")
        return float(np.mean(values)) if values else 0.0

    def state_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[str(outcome.final_state)] = counts.get(str(outcome.final_state), 0) + 1
        return dict(sorted(counts.items()))

    def path_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            if outcome.path is ConfirmationPath.NONE:
                continue
            counts[str(outcome.path)] = counts.get(str(outcome.path), 0) + 1
        return dict(sorted(counts.items()))

    def summary(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "adversarial": self.adversarial,
            "trials": self.trials,
            "breakout_rate": round(self.breakout_rate, 6),
            "confirmed_rate": round(self.confirmed_rate, 6),
            "rejected_rate": round(self.rejected_rate, 6),
            "failed_rate": round(self.failed_rate, 6),
            "expired_rate": round(self.expired_rate, 6),
            "retest_rate": round(self.retest_rate, 6),
            "median_quality": round(self.median_quality, 4),
            "median_confirmation": round(self.median_confirmation, 4),
            "median_coverage": round(self.median_coverage, 4),
            "median_confidence": round(self.median_confidence, 4),
            "mean_attempts": round(self.mean_attempts, 4),
            "quality_thresholds": {
                f"ge_{int(t)}": round(self.high_quality_rate(t), 6) for t in QUALITY_THRESHOLDS
            },
            "states": self.state_counts(),
            "paths": self.path_counts(),
        }


def characterise(
    scenario: Scenario,
    *,
    trials: int = 50,
    config: BreakoutEngineConfig | None = None,
    profile: str | None = None,
    seed_offset: int = 0,
    generator: BreakoutGenerator | None = None,
) -> ScenarioStatistics:
    """Replay one scenario across ``trials`` seeds.

    The two scenarios whose adversarial content lives in the caller's inputs
    rather than in the price series are handled here by their directives: one
    attaches a deliberately weak boundary, the other declares the pattern
    invalidated part-way through. Handling them anywhere else would mean the
    report and the tests disagreed about what the scenario is.
    """
    del generator  # scenarios carry their own builders
    outcomes: list[ScenarioOutcome] = []
    for trial in range(trials):
        seed = seed_offset + trial
        series = scenario.build(seed)
        spec = BoundarySpec.weak() if "weak_boundary" in scenario.directives else BoundarySpec()
        invalidate_at = None
        if "invalidate_pattern" in scenario.directives and series.breakout_index is not None:
            invalidate_at = min(series.breakout_index + 2, len(series.bars) - 1)
        attempts = replay_scenario(
            series,
            config=config,
            profile=profile,
            spec=spec,
            invalidate_at=invalidate_at,
            reopen="single_attempt" not in scenario.directives,
        )
        outcomes.append(ScenarioOutcome.of(scenario, seed, attempts))
    return ScenarioStatistics(
        scenario=scenario.name,
        intent=scenario.intent,
        adversarial=scenario.adversarial,
        outcomes=tuple(outcomes),
    )


def characterise_all(
    *,
    trials: int = 50,
    config: BreakoutEngineConfig | None = None,
    profile: str | None = None,
    scenarios: Sequence[Scenario] | None = None,
    progress: object = None,
) -> list[ScenarioStatistics]:
    """Characterise the whole corpus."""
    chosen = list(scenarios if scenarios is not None else all_scenarios())
    out: list[ScenarioStatistics] = []
    for scenario in chosen:
        if callable(progress):
            progress(scenario.name)
        out.append(characterise(scenario, trials=trials, config=config, profile=profile))
    return out


def separation(results: Sequence[ScenarioStatistics], threshold: float = 70.0) -> float:
    """Controlled high-quality rate minus adversarial high-quality rate.

    A blunt single number, published alongside the distributions rather than
    instead of them. No cost is attached to either error — attaching one is a
    later phase's decision, and doing it here would smuggle a trading preference
    into a characterisation.
    """
    controlled = [r for r in results if not r.adversarial]
    adversarial = [r for r in results if r.adversarial]
    if not controlled or not adversarial:
        return 0.0
    good = float(np.mean([r.high_quality_rate(threshold) for r in controlled]))
    bad = float(np.mean([r.high_quality_rate(threshold) for r in adversarial]))
    return good - bad


def profile_comparison(
    *,
    trials: int = 25,
    config: BreakoutEngineConfig | None = None,
    scenarios: Sequence[Scenario] | None = None,
) -> dict[str, dict[str, float]]:
    """Confirmation rates under each profile, controlled vs adversarial.

    The table that shows what the profiles actually buy. If CONSERVATIVE does
    not confirm materially less on the adversarial corpus than AGGRESSIVE, the
    profiles are not doing the work their names claim.
    """
    engine_config = config or BreakoutEngineConfig()
    out: dict[str, dict[str, float]] = {}
    for name in sorted(engine_config.profiles):
        results = characterise_all(
            trials=trials, config=engine_config, profile=name, scenarios=scenarios
        )
        controlled = [r for r in results if not r.adversarial]
        adversarial = [r for r in results if r.adversarial]
        out[name] = {
            "controlled_confirmed_rate": float(np.mean([r.confirmed_rate for r in controlled])),
            "adversarial_confirmed_rate": float(np.mean([r.confirmed_rate for r in adversarial])),
            "controlled_failed_rate": float(np.mean([r.failed_rate for r in controlled])),
        }
    return out


__all__ = [
    "QUALITY_THRESHOLDS",
    "WARMUP_SESSIONS",
    "BoundarySpec",
    "ScenarioOutcome",
    "ScenarioStatistics",
    "build_boundary",
    "characterise",
    "characterise_all",
    "profile_comparison",
    "replay_scenario",
    "run_scenario",
    "separation",
]
