"""Stability of the breakout engine under small changes to its inputs.

The property under test is not "the score never moves". It is that **a small,
structurally irrelevant change produces a small change, and a large change is
explained by something structural having actually changed.**

Two categories of movement, and the module's job is to keep them apart:

* **Noise.** 0.1% on every price, 5% on every volume, a tick on the boundary
  level. None of these alters what the structure is, so a large score move is a
  defect worth finding.
* **Threshold crossings.** Enough noise, or a big enough shift in the level, and
  price that closed above the zone now closes inside it. The event is genuinely
  a different event: it has a different state, or a different breakout bar, or
  no breakout at all. A large move there is correct behaviour, and reporting it
  as instability would train a reader to ignore the metric.

:class:`Perturbation` therefore records the state and the breakout session
either side, and :attr:`Perturbation.structurally_explained` is true when either
changed. The report's headline number is how many large jumps are **not** so
explained — a figure that should be zero, and which is published either way.

The spec-driven perturbations (gap size, close location, retest depth) rebuild
the scenario with one field changed rather than editing bars afterwards. Editing
bars would break the OHLC relationships and produce a series no market could
print, which is a poor thing to measure stability against.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

import numpy as np

from tradeit.breakouts.base import BreakoutEvent
from tradeit.breakouts.config import BreakoutEngineConfig
from tradeit.breakouts.synthetic import BreakoutSpec, GeneratedBreakout
from tradeit.breakouts.validation import BoundarySpec, run_scenario
from tradeit.core.models import OhlcvBar

#: A move larger than this is "large" and wants an explanation.
LARGE_JUMP = 10.0


class PerturbationKind(StrEnum):
    """What was perturbed. Each is one of the eight item 35 lists."""

    PRICE_NOISE = "price_noise"
    VOLUME_NOISE = "volume_noise"
    LEVEL_SHIFT = "level_shift"
    ATR_SHIFT = "atr_shift"
    GAP_SIZE = "gap_size"
    CLOSE_LOCATION = "close_location"
    RETEST_DEPTH = "retest_depth"
    TIMING = "timing"

    @property
    def is_noise(self) -> bool:
        """Whether the perturbation is supposed to be structurally irrelevant.

        The four that are not — level, gap, close location, retest depth — are
        *definitional* inputs. Moving them is meant to move the score, and a
        stability threshold applied to them would be testing that the engine
        ignores its own definition.
        """
        return self in (
            PerturbationKind.PRICE_NOISE,
            PerturbationKind.VOLUME_NOISE,
            PerturbationKind.TIMING,
        )


@dataclass(frozen=True, slots=True)
class Perturbation:
    """One before/after comparison."""

    scenario: str
    kind: PerturbationKind
    magnitude: float
    seed: int

    baseline_quality: float
    perturbed_quality: float
    baseline_confirmation: float
    perturbed_confirmation: float
    baseline_state: str
    perturbed_state: str
    baseline_breakout_session: str | None
    perturbed_breakout_session: str | None

    @property
    def delta(self) -> float:
        return abs(self.perturbed_quality - self.baseline_quality)

    @property
    def confirmation_delta(self) -> float:
        return abs(self.perturbed_confirmation - self.baseline_confirmation)

    @property
    def state_changed(self) -> bool:
        return self.baseline_state != self.perturbed_state

    @property
    def breakout_moved(self) -> bool:
        return self.baseline_breakout_session != self.perturbed_breakout_session

    @property
    def structurally_explained(self) -> bool:
        """Whether a large move has a structural cause.

        Either the event ended somewhere else, or a different bar became the
        breakout. Both mean the two runs are describing different events, and
        comparing their scores is comparing two answers to two questions.
        """
        return self.state_changed or self.breakout_moved

    def cause(self) -> str:
        if self.breakout_moved:
            return "breakout bar moved"
        if self.state_changed:
            return "terminal state changed"
        return "continuous"


@dataclass(frozen=True, slots=True)
class StabilityReport:
    """Every perturbation of one scenario under one kind."""

    scenario: str
    kind: PerturbationKind
    samples: tuple[Perturbation, ...] = field(default_factory=tuple)

    @property
    def tested(self) -> int:
        return len(self.samples)

    @property
    def deltas(self) -> list[float]:
        return [sample.delta for sample in self.samples]

    @property
    def median_delta(self) -> float:
        return float(np.median(self.deltas)) if self.samples else 0.0

    def percentile_delta(self, q: float) -> float:
        return float(np.percentile(self.deltas, q)) if self.samples else 0.0

    @property
    def largest(self) -> Perturbation | None:
        return max(self.samples, key=lambda s: s.delta, default=None)

    @property
    def unexplained_jumps(self) -> list[Perturbation]:
        return [
            sample
            for sample in self.samples
            if sample.delta > LARGE_JUMP and not sample.structurally_explained
        ]

    def by_magnitude(self) -> dict[float, float]:
        """Median delta at each magnitude, for reading the response as a curve.

        The distinction a single "max delta" cannot make: a response that grows
        in proportion to the perturbation is a slope, and a response that is
        flat and then leaps is a cliff. Only the second is a stability problem,
        and only this breakdown separates them.
        """
        buckets: dict[float, list[float]] = {}
        for sample in self.samples:
            buckets.setdefault(sample.magnitude, []).append(sample.delta)
        return {
            magnitude: float(np.median(values)) for magnitude, values in sorted(buckets.items())
        }

    @property
    def is_proportional(self) -> bool:
        """Whether the median response grows monotonically with magnitude.

        Computed over absolute magnitudes so a symmetric sweep (-2, -1, +1, +2)
        is read as two arms of one curve rather than as a jagged line.
        """
        by_size: dict[float, list[float]] = {}
        for magnitude, delta in self.by_magnitude().items():
            by_size.setdefault(abs(magnitude), []).append(delta)
        curve = [float(np.mean(v)) for _, v in sorted(by_size.items())]
        return len(curve) < 2 or all(b >= a - 0.5 for a, b in pairwise(curve))

    def summary(self) -> dict[str, object]:
        largest = self.largest
        return {
            "scenario": self.scenario,
            "kind": str(self.kind),
            "tested": self.tested,
            "median_delta": round(self.median_delta, 4),
            "p90_delta": round(self.percentile_delta(90), 4),
            "max_delta": round(largest.delta, 4) if largest else 0.0,
            "max_cause": largest.cause() if largest else "n/a",
            "unexplained_jumps": len(self.unexplained_jumps),
            "state_changes": sum(1 for s in self.samples if s.state_changed),
            "proportional": self.is_proportional,
            "by_magnitude": {
                str(magnitude): round(delta, 4) for magnitude, delta in self.by_magnitude().items()
            },
        }

    def format_row(self) -> str:
        data = self.summary()
        return (
            f"{data['scenario']:<26} {data['kind']!s:<16} "
            f"n={data['tested']:<4} median={data['median_delta']:>6.2f} "
            f"p90={data['p90_delta']:>6.2f} max={data['max_delta']:>6.2f} "
            f"({data['max_cause']})  unexplained={data['unexplained_jumps']}"
        )


# ---------------------------------------------------------------------------
# Bar-level perturbations
# ---------------------------------------------------------------------------


def _scaled(bar: OhlcvBar, factor: float) -> OhlcvBar:
    """Scale a bar's four prices by one factor, preserving OHLC ordering."""
    return bar.model_copy(
        update={
            "open": _price(bar.open, factor),
            "high": _price(bar.high, factor),
            "low": _price(bar.low, factor),
            "close": _price(bar.close, factor),
        }
    )


def _price(value: Decimal, factor: float) -> Decimal:
    return Decimal(f"{float(value) * factor:.4f}")


def add_price_noise(
    scenario: GeneratedBreakout, magnitude: float, *, seed: int
) -> GeneratedBreakout:
    """Multiply every bar by an independent draw around 1.

    One factor per bar rather than per price, so the bar's internal geometry —
    close location, wick fractions, body — is exactly preserved. Perturbing the
    four prices independently would change the shape of the candle, which is a
    structural change dressed up as noise.
    """
    rng = np.random.default_rng(seed + 4_100)
    bars = tuple(_scaled(bar, 1.0 + float(rng.normal(0.0, magnitude))) for bar in scenario.bars)
    return replace(scenario, bars=bars)


def add_volume_noise(
    scenario: GeneratedBreakout, magnitude: float, *, seed: int
) -> GeneratedBreakout:
    rng = np.random.default_rng(seed + 4_200)
    bars = tuple(
        bar.model_copy(
            update={
                "volume": Decimal(
                    f"{max(1.0, float(bar.volume) * (1.0 + float(rng.normal(0.0, magnitude)))):.0f}"
                )
            }
        )
        for bar in scenario.bars
    )
    return replace(scenario, bars=bars)


def shift_timing(scenario: GeneratedBreakout, sessions: int) -> GeneratedBreakout:
    """Drop the first ``sessions`` bars.

    Changes where every window falls without changing the structure. Anything
    that depends on the alignment of a fixed-length lookback rather than on the
    geometry will show up here.
    """
    if sessions <= 0 or sessions >= len(scenario.bars) - 60:
        return scenario
    return replace(
        scenario,
        bars=scenario.bars[sessions:],
        breakout_index=(
            None if scenario.breakout_index is None else max(0, scenario.breakout_index - sessions)
        ),
    )


# ---------------------------------------------------------------------------
# Running one comparison
# ---------------------------------------------------------------------------


def _summarise(event: BreakoutEvent) -> tuple[float, float, str, str | None]:
    session = event.first_qualifying_close_session
    return (
        event.breakout_quality,
        event.confirmation_score,
        str(event.state),
        session.isoformat() if session else None,
    )


def perturb(
    scenario: GeneratedBreakout,
    kind: PerturbationKind,
    magnitude: float,
    *,
    seed: int,
    config: BreakoutEngineConfig | None = None,
    profile: str | None = None,
    builder: object = None,
) -> Perturbation:
    """Run one scenario twice — unchanged and perturbed — and compare.

    ``builder`` is needed only by the spec-driven kinds, which rebuild the
    series from a modified :class:`~tradeit.breakouts.synthetic.BreakoutSpec`
    rather than editing bars. It is the generator's ``build`` method; passing
    ``None`` for those kinds raises rather than silently falling back to a bar
    edit that would produce an impossible candle.
    """
    baseline = run_scenario(scenario, config=config, profile=profile, spec=BoundarySpec())

    if kind is PerturbationKind.PRICE_NOISE:
        altered = run_scenario(
            add_price_noise(scenario, magnitude, seed=seed), config=config, profile=profile
        )
    elif kind is PerturbationKind.VOLUME_NOISE:
        altered = run_scenario(
            add_volume_noise(scenario, magnitude, seed=seed), config=config, profile=profile
        )
    elif kind is PerturbationKind.TIMING:
        altered = run_scenario(
            shift_timing(scenario, int(magnitude)), config=config, profile=profile
        )
    elif kind is PerturbationKind.LEVEL_SHIFT:
        altered = run_scenario(
            replace(scenario, level=scenario.level * (1.0 + magnitude)),
            config=config,
            profile=profile,
        )
    elif kind is PerturbationKind.ATR_SHIFT:
        altered = run_scenario(
            scenario,
            config=_with_atr_period(config, magnitude),
            profile=profile,
        )
    else:
        altered = run_scenario(
            _respec(scenario, kind, magnitude, builder), config=config, profile=profile
        )

    base = _summarise(baseline)
    other = _summarise(altered)
    return Perturbation(
        scenario=scenario.name,
        kind=kind,
        magnitude=magnitude,
        seed=seed,
        baseline_quality=base[0],
        perturbed_quality=other[0],
        baseline_confirmation=base[1],
        perturbed_confirmation=other[1],
        baseline_state=base[2],
        perturbed_state=other[2],
        baseline_breakout_session=base[3],
        perturbed_breakout_session=other[3],
    )


def _with_atr_period(config: BreakoutEngineConfig | None, delta: float) -> BreakoutEngineConfig:
    """Change the ATR period, which changes every ATR-relative measurement.

    A blunter perturbation than the others and deliberately so: the ATR period
    is the single number the most thresholds are expressed against, and an
    engine whose conclusions swing on 14 versus 16 sessions is measuring its own
    smoothing.
    """
    base = config or BreakoutEngineConfig()
    period = max(2, min(100, base.atr_period + int(delta)))
    return base.model_copy(update={"atr_period": period})


def _respec(
    scenario: GeneratedBreakout,
    kind: PerturbationKind,
    magnitude: float,
    builder: object,
) -> GeneratedBreakout:
    if not callable(builder):
        raise ValueError(
            f"{kind} rebuilds the scenario from its spec and needs the generator's "
            "build method; editing bars directly would produce candles no market "
            "could print"
        )
    spec: BreakoutSpec = scenario.spec
    if kind is PerturbationKind.GAP_SIZE:
        spec = replace(spec, gap_pct=max(0.0, spec.gap_pct + magnitude))
    elif kind is PerturbationKind.CLOSE_LOCATION:
        spec = replace(
            spec,
            breakout_close_location=float(
                np.clip(spec.breakout_close_location + magnitude, 0.05, 0.99)
            ),
        )
    elif kind is PerturbationKind.RETEST_DEPTH:
        spec = replace(spec, retest_depth=max(0.0, spec.retest_depth + magnitude))
    built: GeneratedBreakout = builder(spec, name=scenario.name, intent=scenario.intent)
    return replace(built, level=scenario.level)


def sweep(
    scenario: GeneratedBreakout,
    kind: PerturbationKind,
    magnitudes: Sequence[float],
    *,
    trials: int = 5,
    config: BreakoutEngineConfig | None = None,
    profile: str | None = None,
    builder: object = None,
) -> StabilityReport:
    """Perturb one scenario across magnitudes and seeds."""
    samples = [
        perturb(
            scenario,
            kind,
            magnitude,
            seed=trial,
            config=config,
            profile=profile,
            builder=builder,
        )
        for magnitude in magnitudes
        for trial in range(trials)
    ]
    return StabilityReport(scenario=scenario.name, kind=kind, samples=tuple(samples))


def is_monotone_in(values: Sequence[float], *, increasing: bool, tolerance: float = 2.0) -> bool:
    """Whether a swept series moves the way its definition says it should.

    ``tolerance`` allows a small backward step, because several components are
    piecewise and a sweep can straddle a knee. It does not allow a reversal:
    the check is on consecutive pairs, so a genuine inversion fails however
    smooth the rest of the curve is.
    """
    pairs = list(pairwise(values))
    if increasing:
        return all(b >= a - tolerance for a, b in pairs)
    return all(b <= a + tolerance for a, b in pairs)


__all__ = [
    "LARGE_JUMP",
    "Perturbation",
    "PerturbationKind",
    "StabilityReport",
    "add_price_noise",
    "add_volume_noise",
    "is_monotone_in",
    "perturb",
    "shift_timing",
    "sweep",
]
