"""Interfaces for screening, pattern detection, breakout confirmation, scoring.

The pipeline these describe is deliberately staged, and the stages are not
interchangeable:

    universe → filters → patterns → breakout monitor → scorer → candidates

Each stage narrows. Filters are cheap and run over thousands of instruments;
pattern detection is expensive and runs over hundreds; breakout monitoring runs
over the handful that are actually close to a trigger. Ordering them this way is
a performance decision, but it is also a correctness one: a scorer that sees
every instrument in the universe will find something to like in all of them.

Nothing here is implemented. These are the contracts Phase 4 and Phase 5 build
against.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradeit.analytics.base import FeatureVector
from tradeit.breakouts.base import BreakoutEvent
from tradeit.core.enums import PatternStatus, PatternType, SignalDirection
from tradeit.core.models import OhlcvBar


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Whether one instrument passed one filter, and why not if it failed.

    The reason is mandatory rather than optional. A screen that returns 40 names
    from 4,000 is useless for debugging unless you can ask why a specific
    expected name is missing, and reconstructing that after the fact means
    re-running the whole chain.
    """

    instrument_id: int
    passed: bool
    reason: str | None = None
    measured_value: float | None = None
    threshold: float | None = None


@runtime_checkable
class ScreenFilter(Protocol):
    """One predicate in the screening chain.

    Filters are pure: same inputs, same result. They receive a
    :class:`FeatureVector` rather than raw bars so that the expensive analytics
    are computed once per instrument per date, not once per filter.
    """

    name: str
    stage: str  # "liquidity" | "quality" | "fundamental" | "technical"

    @property
    def parameters(self) -> dict[str, object]: ...

    def evaluate(self, features: FeatureVector) -> FilterResult: ...


@dataclass(frozen=True, slots=True)
class DetectedPattern:
    """A chart formation found in a bar series.

    ``pivot_price`` is the level a breakout must clear and is the single most
    consequential number the detector produces: it determines the entry, and
    together with ``stop_price`` it determines position size. A detector that is
    vague about the pivot produces a strategy that cannot be sized.

    ``quality`` is the detector's own confidence in the formation, distinct from
    the opportunity score. A textbook base on a mediocre company and a sloppy
    base on an excellent one are different problems, and collapsing them into
    one number early makes both invisible.
    """

    instrument_id: int
    pattern_type: PatternType
    status: PatternStatus
    start_date: dt.date
    end_date: dt.date
    detected_on: dt.date
    pivot_price: Decimal | None
    stop_price: Decimal | None
    depth_pct: float | None
    length_sessions: int
    quality: float
    attributes: dict[str, float] = field(default_factory=dict)

    @property
    def risk_per_share(self) -> Decimal | None:
        """Distance from entry to stop. The unit position sizing is built on."""
        if self.pivot_price is None or self.stop_price is None:
            return None
        return self.pivot_price - self.stop_price

    @property
    def actionable(self) -> bool:
        return self.status in (PatternStatus.COMPLETE, PatternStatus.TRIGGERED)


@runtime_checkable
class PatternDetector(Protocol):
    """Finds one family of formations in a bar series.

    One detector per pattern type rather than one omniscient detector: they have
    genuinely different parameters, they are tuned and validated separately, and
    a single class that finds nine patterns is a class nobody will refactor.

    Detectors see only bars up to and including ``as_of_session`` — the caller
    guarantees this by passing a clock-gated series, and detectors must not
    reach for more.
    """

    name: str
    pattern_type: PatternType

    @property
    def minimum_bars(self) -> int: ...

    @property
    def parameters(self) -> dict[str, object]: ...

    def detect(self, bars: Sequence[OhlcvBar], as_of_session: dt.date) -> list[DetectedPattern]: ...


# Phase 2 declared placeholder ``BreakoutEvent`` and ``BreakoutMonitor`` types
# here. Phase 5 implements both, in :mod:`tradeit.breakouts`, and the real ones
# differ from the sketches in ways that matter: an event is one *attempt* with an
# identity and an append-only observation history rather than a status snapshot,
# and there are four scores rather than one status.
#
# The placeholders are removed rather than left alongside. The draft
# ``BreakoutEvent`` carried an ``is_tradable`` property, which Phase 5 must not
# express at all — whether a confirmed breakout is worth trading is decided
# several stages downstream, and a tradability flag on a breakout object is the
# exact conflation the phase separation exists to prevent.


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """One factor's contribution to an opportunity score."""

    name: str
    raw_value: float | None
    normalised: float
    weight: float

    @property
    def contribution(self) -> float:
        return self.normalised * self.weight


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    """A ranked opportunity with its reasoning attached.

    The component breakdown is not a debugging aid bolted on afterwards; it is
    the deliverable. The brief requires every recommendation to be explainable,
    and an explanation reconstructed later from a total is a rationalisation.
    Components are stored with the score.
    """

    instrument_id: int
    session_date: dt.date
    direction: SignalDirection
    total: float
    components: tuple[ScoreComponent, ...]
    feature_set_digest: str
    strategy_config_digest: str

    def explain(self, top_n: int = 5) -> list[tuple[str, float]]:
        """The largest contributors, positive or negative, most influential first."""
        ranked = sorted(self.components, key=lambda c: abs(c.contribution), reverse=True)
        return [(c.name, c.contribution) for c in ranked[:top_n]]

    @property
    def reconciles(self) -> bool:
        """Whether the components actually sum to the total.

        Guards a specific failure: a scorer that applies a late adjustment
        without recording it as a component, producing an explanation that does
        not add up to the number it explains.
        """
        return abs(sum(c.contribution for c in self.components) - self.total) < 1e-9


@runtime_checkable
class OpportunityScorer(Protocol):
    """Combines evidence into a ranked score.

    Rule-based in the near term. The interface accommodates a learned model
    later, but only against a rule-based baseline that already works — a model
    introduced before that mostly launders look-ahead bias into a plausible
    number.
    """

    name: str

    @property
    def parameters(self) -> dict[str, object]: ...

    def score(
        self,
        features: FeatureVector,
        pattern: DetectedPattern | None,
        breakout: BreakoutEvent | None,
    ) -> OpportunityScore: ...
