"""The invariant: **breakout state is not trade eligibility.**

`CONFIRMED` means one thing and one thing only:

> The configured confirmation pathway has been satisfied.

It does **not** mean high-quality trade, good entry, or anything a position
could be opened on. Phase 5's own characterisation established that this is not
a theoretical worry: a slow drift above an arbitrary level satisfies the
acceptance path 96% of the time on the adversarial corpus, at a median breakout
quality of 49 against 93 for a clean break. A downstream consumer reading
`state == CONFIRMED` and nothing else would treat those two events identically.
One reading the full evidence object would not.

So this module does two things and refuses to do a third.

**It assembles the evidence.** :class:`EvidenceBundle` carries every field a
downstream stage needs in order to decide anything, across both layers, plus
explicit slots for the two Phase 5 cannot fill. Assembling it is not optional
politeness — it is the only supported way to consume a breakout, and the
constructor refuses a bundle whose pattern and breakout disagree about which
instrument they describe.

**It records where the boundary came from.** A crossing of a level somebody
typed into a research notebook and a breakout of a causally-derived Phase 4
structure are both real observations and are not the same object.
:class:`BoundaryKind` tags them, the production monitor accepts only the
structural kind by default, and the tag travels into storage so a query can
separate the populations.

**It does not compute eligibility.** There is no ``is_tradable``, no ``score``,
no ``rank`` and no ``recommendation`` here, and adding one would put a Phase 7
decision inside a Phase 5 module — which is the exact conflation the phase
separation exists to prevent. What the bundle offers instead is
:meth:`EvidenceBundle.missing_for_decision`, which names the evidence a
consumer does *not* yet have, so a stage that decides anyway is doing so
knowingly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tradeit.breakouts.base import BreakoutEvent
from tradeit.breakouts.boundary import BoundaryKind
from tradeit.breakouts.lifecycle import BreakoutState
from tradeit.core.enums import MarketRegime
from tradeit.errors import ConfigError
from tradeit.patterns.base import PatternInstance, PatternState

#: Pattern states whose boundary the production monitor may watch. Duplicated
#: from the engine config deliberately: the config value is operator-tunable,
#: this set is the *architectural* floor, and
#: :func:`check_monitor_eligibility` enforces that a config cannot widen past
#: it. An operator can monitor fewer states; nobody can quietly add FORMING.
PRODUCTION_PATTERN_STATES: frozenset[PatternState] = frozenset(
    {
        PatternState.MATURE,
        PatternState.NEAR_BREAKOUT,
        PatternState.BROKEN_OUT_UNCONFIRMED,
    }
)


class EligibilityError(ConfigError):
    """A configuration or call that would violate the invariant."""


def check_monitor_eligibility(
    states: Sequence[str],
    *,
    context: str = "monitored_states",
) -> None:
    """Refuse a monitored-state set that widens past the architectural floor.

    ``FORMING`` is the one that matters. Its boundary is still resolving, so a
    "breakout" of it is a breakout of a guess — and once such events are in the
    dataset, every rate computed from it describes the monitor rather than the
    market.
    """
    allowed = {str(state) for state in PRODUCTION_PATTERN_STATES}
    unknown = sorted(set(states) - allowed)
    if unknown:
        raise EligibilityError(
            f"{context} contains {unknown}, which is outside the production floor "
            f"{sorted(allowed)}. A pattern outside these states does not carry a "
            "boundary anyone is trading against; monitoring it manufactures events "
            "rather than finding them. Research use is supported through an "
            "explicitly tagged non-structural boundary instead."
        )


@dataclass(frozen=True, slots=True)
class MissingEvidence:
    """One thing a consumer does not have, and what it would need to get it."""

    field_name: str
    reason: str
    supplied_by: str

    def __str__(self) -> str:
        return f"{self.field_name}: {self.reason} (supplied by {self.supplied_by})"


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Everything a downstream stage needs in order to decide anything.

    Thirteen fields, of which Phase 5 can fill eleven. The two it cannot —
    fundamental and portfolio-fit scores — are present as explicit ``None``
    slots rather than absent, so a consumer reaching for them gets a named gap
    instead of an ``AttributeError`` and a report can count how many bundles
    were decided on without them.

    **There is deliberately no aggregate.** No total, no rank, no verdict. A
    bundle is evidence; turning evidence into a decision is Phase 7's, and a
    convenience method here that summed the fields would become the decision by
    default.
    """

    instrument_id: int
    as_of_session: dt.date

    # -- Phase 4: the structure --------------------------------------------
    pattern_type: str
    pattern_state: PatternState
    pattern_quality: float
    pattern_evidence_coverage: float

    # -- the boundary -------------------------------------------------------
    boundary_kind: BoundaryKind
    boundary_confidence: float

    # -- Phase 5: the breakout ---------------------------------------------
    breakout_state: BreakoutState
    breakout_quality: float
    breakout_confirmation_score: float
    breakout_evidence_coverage: float
    breakout_confidence: float

    # -- context, never a veto ---------------------------------------------
    relative_strength: float | None = None
    sector_strength: float | None = None
    market_regime: MarketRegime = MarketRegime.UNKNOWN

    # -- slots Phase 5 cannot fill -----------------------------------------
    #: Phase 6. ``None`` until fundamentals exist.
    fundamental_score: float | None = None
    #: Phase 7. ``None`` until portfolio construction exists.
    portfolio_fit_score: float | None = None

    # -- provenance ---------------------------------------------------------
    pattern_key: str = ""
    breakout_event_key: str = ""
    pattern_detector_name: str = ""
    pattern_detector_version: int = 0
    breakout_scorer_version: int = 0
    breakout_profile: str = ""
    measurements: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("pattern_quality", self.pattern_quality),
            ("pattern_evidence_coverage", self.pattern_evidence_coverage),
            ("boundary_confidence", self.boundary_confidence),
            ("breakout_quality", self.breakout_quality),
            ("breakout_confirmation_score", self.breakout_confirmation_score),
            ("breakout_evidence_coverage", self.breakout_evidence_coverage),
            ("breakout_confidence", self.breakout_confidence),
        ):
            if not 0.0 <= value <= 100.0:
                raise ConfigError(f"{name} {value} outside [0, 100]")

    @classmethod
    def of(
        cls,
        event: BreakoutEvent,
        pattern: PatternInstance | None = None,
        *,
        relative_strength: float | None = None,
        sector_strength: float | None = None,
        market_regime: MarketRegime = MarketRegime.UNKNOWN,
        boundary_kind: BoundaryKind | None = None,
    ) -> EvidenceBundle:
        """Assemble a bundle from a breakout event and its pattern.

        The pattern is optional because an event against an unattached level has
        none — and that case is exactly where the bundle earns its keep: pattern
        quality and coverage come back as zero with the boundary kind saying
        why, rather than as plausible-looking numbers from nowhere.
        """
        if pattern is not None and pattern.instrument_id != event.instrument_id:
            raise ConfigError(
                f"pattern is for instrument {pattern.instrument_id} and the breakout "
                f"for {event.instrument_id}; a bundle describing two instruments "
                "would attribute one's structure to the other's price action"
            )
        kind = boundary_kind or (
            BoundaryKind.STRUCTURAL_PATTERN_BOUNDARY
            if event.boundary.is_attached
            else BoundaryKind.OTHER
        )
        return cls(
            instrument_id=event.instrument_id,
            as_of_session=event.last_session,
            pattern_type=(
                str(pattern.pattern_type) if pattern is not None else event.boundary.pattern_type
            ),
            pattern_state=pattern.state if pattern is not None else PatternState.EXPIRED,
            pattern_quality=(
                pattern.quality if pattern is not None else event.boundary.pattern_quality
            ),
            pattern_evidence_coverage=(pattern.evidence_coverage if pattern is not None else 0.0),
            boundary_kind=kind,
            boundary_confidence=event.boundary.confidence,
            breakout_state=event.state,
            breakout_quality=event.breakout_quality,
            breakout_confirmation_score=event.confirmation_score,
            breakout_evidence_coverage=event.evidence_coverage,
            breakout_confidence=event.confidence,
            relative_strength=relative_strength,
            sector_strength=sector_strength,
            market_regime=market_regime,
            pattern_key=event.boundary.pattern_key,
            breakout_event_key=event.event_key,
            pattern_detector_name=event.pattern_detector_name,
            pattern_detector_version=event.pattern_detector_version,
            breakout_scorer_version=event.scorer_version,
            breakout_profile=event.profile_name,
            measurements=(dict(event.observations[-1].measurements) if event.observations else {}),
        )

    # -- what a consumer does not have --------------------------------------

    def missing_for_decision(self) -> list[MissingEvidence]:
        """Evidence this bundle lacks, named rather than implied.

        A stage may legitimately decide without some of it. What it must not do
        is decide without *knowing* what it is missing, and a list of named gaps
        is the difference.
        """
        gaps: list[MissingEvidence] = []
        if self.fundamental_score is None:
            gaps.append(
                MissingEvidence(
                    "fundamental_score",
                    "no fundamental assessment exists yet",
                    "Phase 6",
                )
            )
        if self.portfolio_fit_score is None:
            gaps.append(
                MissingEvidence(
                    "portfolio_fit_score",
                    "no portfolio construction exists yet",
                    "Phase 7",
                )
            )
        if self.relative_strength is None:
            gaps.append(
                MissingEvidence(
                    "relative_strength",
                    "no benchmark series was supplied",
                    "the caller, from Phase 3 analytics",
                )
            )
        if self.sector_strength is None:
            gaps.append(
                MissingEvidence(
                    "sector_strength",
                    "no sector data was supplied",
                    "the caller, from Phase 3 analytics",
                )
            )
        if self.market_regime is MarketRegime.UNKNOWN:
            gaps.append(
                MissingEvidence(
                    "market_regime",
                    "no regime state was supplied",
                    "the caller, from Phase 3 analytics",
                )
            )
        if self.boundary_kind.is_research_only:
            gaps.append(
                MissingEvidence(
                    "structural provenance",
                    f"the boundary is {self.boundary_kind}, not a detected structure",
                    "Phase 4, if this level is meant to be one",
                )
            )
        return gaps

    @property
    def is_structurally_attached(self) -> bool:
        return self.boundary_kind.is_production_eligible and bool(self.pattern_key)

    @property
    def weakest_score(self) -> tuple[str, float]:
        """The lowest of the six 0-100 numbers, and which one it is.

        A diagnostic, not a decision. Its use is in a review queue — "this
        confirmed event's weakest number is boundary_confidence at 31" is a
        sentence a human can act on, and it is deliberately not combined with
        anything.
        """
        scores = {
            "pattern_quality": self.pattern_quality,
            "pattern_evidence_coverage": self.pattern_evidence_coverage,
            "boundary_confidence": self.boundary_confidence,
            "breakout_quality": self.breakout_quality,
            "breakout_evidence_coverage": self.breakout_evidence_coverage,
            "breakout_confidence": self.breakout_confidence,
        }
        name = min(scores, key=lambda key: scores[key])
        return name, scores[name]

    def explain(self) -> str:
        """A human-readable dump that stops where Phase 5 stops."""
        lines = [
            f"Instrument:              {self.instrument_id}",
            f"As of:                   {self.as_of_session.isoformat()}",
            "",
            f"Pattern:                 {self.pattern_type or '(none)'}",
            f"Pattern State:           {self.pattern_state}",
            f"Pattern Quality:         {self.pattern_quality:.0f}",
            f"Pattern Coverage:        {self.pattern_evidence_coverage:.0f}",
            "",
            f"Boundary Kind:           {self.boundary_kind}",
            f"Boundary Confidence:     {self.boundary_confidence:.0f}",
            "",
            f"Breakout State:          {self.breakout_state}",
            f"Breakout Quality:        {self.breakout_quality:.0f}",
            f"Confirmation Score:      {self.breakout_confirmation_score:.0f}",
            f"Breakout Coverage:       {self.breakout_evidence_coverage:.0f}",
            f"Breakout Confidence:     {self.breakout_confidence:.0f}",
            "",
            f"Relative Strength:       {_show(self.relative_strength)}",
            f"Sector Strength:         {_show(self.sector_strength)}",
            f"Market Regime:           {self.market_regime}",
            f"Fundamental Score:       {_show(self.fundamental_score)}",
            f"Portfolio Fit Score:     {_show(self.portfolio_fit_score)}",
            "",
            "Evidence not available:",
        ]
        gaps = self.missing_for_decision()
        lines.extend(f"  * {gap}" for gap in gaps)
        if not gaps:
            lines.append("  (none)")
        lines.append("")
        lines.append(
            "This object is evidence, not a recommendation. A breakout state "
            "does not authorise a trade, a position or an allocation."
        )
        return "\n".join(lines)

    def to_payload(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "as_of_session": self.as_of_session.isoformat(),
            "pattern_type": self.pattern_type,
            "pattern_state": str(self.pattern_state),
            "pattern_quality": round(self.pattern_quality, 6),
            "pattern_evidence_coverage": round(self.pattern_evidence_coverage, 6),
            "boundary_kind": str(self.boundary_kind),
            "boundary_confidence": round(self.boundary_confidence, 6),
            "breakout_state": str(self.breakout_state),
            "breakout_quality": round(self.breakout_quality, 6),
            "breakout_confirmation_score": round(self.breakout_confirmation_score, 6),
            "breakout_evidence_coverage": round(self.breakout_evidence_coverage, 6),
            "breakout_confidence": round(self.breakout_confidence, 6),
            "relative_strength": self.relative_strength,
            "sector_strength": self.sector_strength,
            "market_regime": str(self.market_regime),
            "fundamental_score": self.fundamental_score,
            "portfolio_fit_score": self.portfolio_fit_score,
            "pattern_key": self.pattern_key,
            "breakout_event_key": self.breakout_event_key,
            "detector": {
                "name": self.pattern_detector_name,
                "version": self.pattern_detector_version,
            },
            "breakout_scorer_version": self.breakout_scorer_version,
            "breakout_profile": self.breakout_profile,
            "missing": [gap.field_name for gap in self.missing_for_decision()],
        }


def _show(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


#: The thirteen fields the gate requires downstream consumers to have access to.
#: Kept as data so a test can assert the bundle carries all of them, rather than
#: as prose that drifts from the dataclass.
REQUIRED_EVIDENCE_FIELDS: tuple[str, ...] = (
    "pattern_quality",
    "pattern_evidence_coverage",
    "pattern_state",
    "boundary_confidence",
    "breakout_quality",
    "breakout_confirmation_score",
    "breakout_evidence_coverage",
    "breakout_confidence",
    "breakout_state",
    "relative_strength",
    "sector_strength",
    "market_regime",
    "fundamental_score",
    "portfolio_fit_score",
)

#: Vocabulary that must not appear anywhere in the Phase 5 object graph. The
#: test that reads this walks the dataclass fields, the enum members and the
#: rendered explanation; it is cheap, and it is the kind of drift that arrives
#: one helpful convenience method at a time.
FORBIDDEN_DECISION_TERMS: tuple[str, ...] = (
    "buy",
    "sell",
    "tradable",
    "tradeable",
    "eligible",
    "recommend",
    "position_size",
    "allocation",
    "stop_loss",
    "target_price",
)


__all__ = [
    "FORBIDDEN_DECISION_TERMS",
    "PRODUCTION_PATTERN_STATES",
    "REQUIRED_EVIDENCE_FIELDS",
    "BoundaryKind",
    "EligibilityError",
    "EvidenceBundle",
    "MissingEvidence",
    "check_monitor_eligibility",
]
