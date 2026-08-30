"""Data eligibility versus feature readiness.

Two different questions that are easy to conflate and expensive to conflate:

**DATA_ELIGIBLE** — does this security have enough clean history to exist in the
system at all? Can we store it, chart it, include it in a breadth roster?

**FEATURE_READY** — has every feature *this particular strategy* needs finished
warming up? A 200-day average of 40 bars is not a 200-day average, and a screen
that ranks on one is ranking on a different quantity than it believes.

The distinction matters because they diverge, and the divergence is not an edge
case. A security that IPO'd 100 sessions ago is genuinely in the market: it has
a price, it trades, it belongs in an advance/decline count. It is simply not yet
scannable by a strategy that needs a 52-week range. Treating those as one
condition forces a choice between excluding real market activity from breadth
and ranking on features that are still converging.

**Readiness is per strategy, not global.** A mean-reversion strategy needing a
20-day Bollinger band is ready in 20 sessions; a relative-strength strategy
needing a 250-session lookback is not ready for a year. Nothing here assumes
252; it asks the strategy's declared feature set what it needs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from tradeit.analytics.registry import FeatureRegistry, FeatureSpec
from tradeit.errors import ConfigError


class EligibilityState(StrEnum):
    """How far through the pipeline a security may travel.

    Ordered from least to most eligible. The ordering is meaningful:
    ``FEATURE_READY`` implies ``DATA_ELIGIBLE``.
    """

    #: Fails a hard data requirement — too few sessions, failed quality checks,
    #: not in the universe on this date. Not usable for anything.
    INELIGIBLE = "ineligible"

    #: Enough clean history to exist in the system: storable, chartable, and a
    #: legitimate member of a breadth or dispersion roster. NOT scannable.
    DATA_ELIGIBLE = "data_eligible"

    #: Every feature the strategy declared has finished warming up. Only these
    #: may be screened, scored, or ranked by that strategy.
    FEATURE_READY = "feature_ready"

    @property
    def can_be_stored(self) -> bool:
        return self is not EligibilityState.INELIGIBLE

    @property
    def can_join_breadth(self) -> bool:
        """Breadth counts market participation, not scan candidacy.

        Excluding a genuinely trading security from an advance/decline count
        because a strategy's 250-day lookback has not warmed up would misreport
        the market.
        """
        return self is not EligibilityState.INELIGIBLE

    @property
    def can_be_scanned(self) -> bool:
        return self is EligibilityState.FEATURE_READY


@dataclass(frozen=True, slots=True)
class EligibilityAssessment:
    """Why a security is or is not eligible, with the arithmetic exposed.

    ``missing_features`` and ``sessions_until_ready`` are the fields that make
    this actionable. "Not eligible" is a dead end; "not eligible for 12 more
    sessions because sma_200 and rolling_high_252 are still warming" is a
    diagnosis.
    """

    instrument_id: int
    state: EligibilityState
    sessions_available: int
    sessions_required_for_data: int
    sessions_required_for_features: int
    missing_features: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def sessions_until_ready(self) -> int | None:
        """Sessions until FEATURE_READY, or ``None`` if blocked by something else.

        Returns ``None`` rather than a number when the blocker is not time --
        a delisted instrument never becomes ready no matter how long you wait,
        and reporting "3 sessions" for it would be misleading.
        """
        if self.state is EligibilityState.FEATURE_READY:
            return 0
        if self.state is EligibilityState.INELIGIBLE and self.sessions_available >= (
            self.sessions_required_for_data
        ):
            return None  # blocked by something other than history length
        return max(0, self.sessions_required_for_features - self.sessions_available)

    def explain(self) -> str:
        if self.state is EligibilityState.FEATURE_READY:
            return f"ready: {self.sessions_available} sessions available"
        parts = [f"{self.state}: {self.sessions_available} sessions available"]
        parts.extend(self.reasons)
        if self.missing_features:
            shown = ", ".join(self.missing_features[:5])
            more = (
                f" (+{len(self.missing_features) - 5} more)"
                if len(self.missing_features) > 5
                else ""
            )
            parts.append(f"still warming: {shown}{more}")
        return "; ".join(parts)


class EligibilityPolicy:
    """Decides eligibility from history length and a declared feature set.

    Constructed per strategy. The feature set is the strategy's *declared*
    requirement (see :mod:`tradeit.analytics.feature_sets`), not the whole
    registry — a strategy that uses six features should not wait for the
    warm-up of a feature it never reads.
    """

    def __init__(
        self,
        *,
        min_data_sessions: int,
        required_features: Sequence[FeatureSpec] = (),
        name: str = "default",
    ) -> None:
        if min_data_sessions < 0:
            raise ConfigError("min_data_sessions cannot be negative")
        self.name = name
        self.min_data_sessions = min_data_sessions
        self.required_features = tuple(required_features)

    @classmethod
    def from_registry(
        cls,
        *,
        min_data_sessions: int,
        registry: FeatureRegistry,
        feature_names: Sequence[str] | None = None,
        name: str = "default",
    ) -> EligibilityPolicy:
        """Build from a registry, optionally restricted to declared features.

        Passing ``feature_names=None`` uses the whole active registry, which is
        the conservative default and almost never what a strategy wants -- see
        ADR-0013 on why registry membership is not a licence to consume.
        """
        specs = (
            [registry.get(n) for n in feature_names]
            if feature_names is not None
            else registry.active()
        )
        return cls(min_data_sessions=min_data_sessions, required_features=specs, name=name)

    @property
    def feature_warmup(self) -> int:
        """Longest warm-up among the declared features."""
        return max((s.warmup_periods for s in self.required_features), default=0)

    @property
    def min_feature_ready_sessions(self) -> int:
        """History needed before this strategy may scan a security.

        The larger of the data floor and the feature warm-up: a strategy whose
        features warm in 20 sessions still respects a 252-session data floor if
        one is configured, because that floor exists for reasons beyond warm-up
        (liquidity history, listing seasoning).
        """
        return max(self.min_data_sessions, self.feature_warmup)

    def assess(
        self,
        instrument_id: int,
        sessions_available: int,
        *,
        in_universe: bool = True,
        quality_blocked: bool = False,
        listing_active: bool = True,
    ) -> EligibilityAssessment:
        """Classify one security.

        ``sessions_available`` is the count of *clean, visible* sessions as of
        the evaluation clock -- the caller derives it from a clock-gated read,
        so this function cannot see the future by construction.
        """
        reasons: list[str] = []

        if not in_universe:
            reasons.append("not in the universe on this date")
        if quality_blocked:
            reasons.append("blocked by an unresolved data-quality issue")
        if not listing_active:
            reasons.append("not actively listed on this date")
        if sessions_available < self.min_data_sessions:
            reasons.append(
                f"only {sessions_available} of {self.min_data_sessions} required sessions"
            )

        if reasons:
            return EligibilityAssessment(
                instrument_id=instrument_id,
                state=EligibilityState.INELIGIBLE,
                sessions_available=sessions_available,
                sessions_required_for_data=self.min_data_sessions,
                sessions_required_for_features=self.min_feature_ready_sessions,
                reasons=tuple(reasons),
            )

        missing = tuple(
            sorted(
                spec.name
                for spec in self.required_features
                if not spec.is_available(sessions_available)
            )
        )
        state = EligibilityState.FEATURE_READY if not missing else EligibilityState.DATA_ELIGIBLE
        return EligibilityAssessment(
            instrument_id=instrument_id,
            state=state,
            sessions_available=sessions_available,
            sessions_required_for_data=self.min_data_sessions,
            sessions_required_for_features=self.min_feature_ready_sessions,
            missing_features=missing,
        )

    def partition(
        self, sessions_by_instrument: dict[int, int], **kwargs: object
    ) -> EligibilityReport:
        """Assess a whole universe at once."""
        assessments = [
            self.assess(instrument_id, sessions, **kwargs)  # type: ignore[arg-type]
            for instrument_id, sessions in sorted(sessions_by_instrument.items())
        ]
        return EligibilityReport(policy_name=self.name, assessments=tuple(assessments))


@dataclass(frozen=True, slots=True)
class EligibilityReport:
    """The eligibility split across a universe on one date."""

    policy_name: str
    assessments: tuple[EligibilityAssessment, ...] = field(default=())

    def _of(self, state: EligibilityState) -> list[EligibilityAssessment]:
        return [a for a in self.assessments if a.state is state]

    @property
    def scannable(self) -> list[int]:
        """Instruments a strategy may screen and rank."""
        return [a.instrument_id for a in self._of(EligibilityState.FEATURE_READY)]

    @property
    def breadth_roster(self) -> list[int]:
        """Instruments that count toward breadth and dispersion.

        Deliberately wider than :attr:`scannable`. A security warming up is
        still trading, and omitting it from an advance/decline count would
        misreport market participation.
        """
        return [a.instrument_id for a in self.assessments if a.state.can_join_breadth]

    @property
    def warming_up(self) -> list[int]:
        return [a.instrument_id for a in self._of(EligibilityState.DATA_ELIGIBLE)]

    @property
    def ineligible(self) -> list[int]:
        return [a.instrument_id for a in self._of(EligibilityState.INELIGIBLE)]

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.assessments),
            "feature_ready": len(self.scannable),
            "data_eligible": len(self.warming_up),
            "ineligible": len(self.ineligible),
            "breadth_roster": len(self.breadth_roster),
        }

    def blocking_features(self) -> dict[str, int]:
        """Which features are gating the most instruments.

        The diagnostic that answers "why is the scan universe so small?" --
        usually one long-lookback feature holding back a third of the roster.
        """
        counts: dict[str, int] = {}
        for assessment in self._of(EligibilityState.DATA_ELIGIBLE):
            for name in assessment.missing_features:
                counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
