"""``DRAFT → VALIDATED``: does everything this strategy references exist?

``STRATEGY_BUILDER.md`` §5 defines the first gate in one line — *validated
against the registries: does everything it references exist?* — and §2 explains
why it is the first: a strategy definition is **a reference into an existing
engine, never a reimplementation**, so a reference that resolves to nothing is
the one failure that makes every later stage meaningless. A backtest of a
strategy naming a detector nobody wrote does not fail; it silently tests a
smaller strategy.

Three registries answer three questions:

* :class:`~tradeit.core.enums.PatternType` — is this a pattern family the
  platform has a vocabulary for?
* :class:`~tradeit.patterns.registry.DetectorRegistry` — can anything actually
  *produce* it? The vocabulary is deliberately wider than the detector set:
  ``descending_triangle``, ``symmetrical_triangle`` and ``consolidation`` are
  storable pattern types with no detector, because the enum is a storage
  vocabulary that must stay able to interpret an old row. Naming one in a
  strategy is still a defect — the strategy would simply never fire.
* :mod:`tradeit.portfolio.mandate` — §7: *a strategy may select within its
  mandate's eligible hierarchy; it may not select outside it.*

Decision timeframes and construction inputs are different questions
---------------------------------------------------------------------

``TimeframeConfig`` names four things, and only two of them are selections.
``enabled`` and ``intraday_enabled`` are timeframes the strategy makes
decisions on, and the mandate governs them. ``base_timeframe`` and
``intraday_base`` are what higher timeframes are *aggregated from* — a Swing
strategy building 15-minute bars out of 1-minute bars is not making a
1-minute decision, and a check that conflated the two would forbid building
15-minute bars correctly. They are checked for existence and for being no
coarser than what they must build, and not against the mandate.

**A defect is not a crash.** :func:`validate` returns every finding rather than
raising on the first, because a strategy author fixing one reference at a time
through six round trips is how the gate becomes something people route around.
:func:`require_valid` is the raising form.

*Not included:* any judgement about whether a valid strategy is a good one. §6
is explicit that no comparison metric is computed yet, and a validator that
scored a definition would be inventing one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tradeit.core.enums import Bartimeframe, PatternType
from tradeit.patterns.registry import DetectorRegistry
from tradeit.portfolio.mandate import Mandate, eligible_timeframes
from tradeit.strategy.config import StrategyConfig
from tradeit.strategy.lifecycle import StrategyState, StrategyVersion

__all__ = [
    "Defect",
    "Finding",
    "StrategyInvalid",
    "ValidationReport",
    "producible_patterns",
    "require_valid",
    "validate",
    "validated",
]


class Defect(StrEnum):
    """What a reference failed to resolve to."""

    #: Not a member of the platform's pattern vocabulary at all.
    UNKNOWN_PATTERN = "unknown_pattern"
    #: A real pattern type that no detector emits. The strategy would never
    #: fire on it, which is worse than an error because it looks like a result.
    UNPRODUCIBLE_PATTERN = "unproducible_pattern"
    #: Not a bar size the platform understands.
    UNKNOWN_TIMEFRAME = "unknown_timeframe"
    #: A real timeframe the mandate does not admit (§7).
    TIMEFRAME_OUTSIDE_MANDATE = "timeframe_outside_mandate"
    #: A construction input coarser than something it must aggregate into.
    BASE_COARSER_THAN_TARGET = "base_coarser_than_target"
    #: Nothing enabled. Valid by every field constraint and unable to trade.
    NOTHING_ENABLED = "nothing_enabled"


@dataclass(frozen=True, slots=True)
class Finding:
    """One unresolved reference, and enough to fix it."""

    defect: Defect
    subject: str
    detail: str

    def describe(self) -> str:
        return f"{self.defect.value}: {self.subject} — {self.detail}"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    strategy_name: str
    mandate: Mandate
    findings: tuple[Finding, ...]

    @property
    def valid(self) -> bool:
        return not self.findings

    def describe(self) -> str:
        if self.valid:
            return f"{self.strategy_name} validates against the {self.mandate.value} mandate"
        return f"{self.strategy_name}: " + "; ".join(f.describe() for f in self.findings)


class StrategyInvalid(ValueError):
    """A strategy references something that does not exist."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__(report.describe())
        self.report = report


def producible_patterns() -> frozenset[PatternType]:
    """Pattern families some detector actually emits.

    Read from the registry rather than listed, so adding a detector does not
    require remembering to update a second place.
    """
    return frozenset(
        entry.pattern_type for entry in DetectorRegistry.from_config().entries.values()
    )


_ORDER: dict[Bartimeframe, int] = {tf: i for i, tf in enumerate(Bartimeframe)}


def _timeframe(raw: str) -> Bartimeframe | None:
    try:
        return Bartimeframe(raw)
    except ValueError:
        return None


def validate(config: StrategyConfig, mandate: Mandate) -> ValidationReport:
    """Resolve every reference in ``config`` against the registries."""
    findings: list[Finding] = []
    vocabulary = {p.value for p in PatternType}
    producible = {p.value for p in producible_patterns()}

    if not config.patterns.enabled_patterns:
        findings.append(
            Finding(
                Defect.NOTHING_ENABLED,
                "patterns.enabled_patterns",
                "no pattern family is enabled, so nothing can ever be detected",
            )
        )
    for name in config.patterns.enabled_patterns:
        if name not in vocabulary:
            findings.append(
                Finding(
                    Defect.UNKNOWN_PATTERN,
                    f"patterns.enabled_patterns/{name}",
                    f"not a PatternType; known families are {sorted(vocabulary)}",
                )
            )
        elif name not in producible:
            findings.append(
                Finding(
                    Defect.UNPRODUCIBLE_PATTERN,
                    f"patterns.enabled_patterns/{name}",
                    "a storable pattern type that no detector emits; the strategy "
                    "would never fire on it",
                )
            )

    admitted = eligible_timeframes(mandate)
    # Each base aggregates into its own group only: base_timeframe builds the
    # daily-and-above series, intraday_base builds the intraday one. Pooling
    # them would report a daily base as unable to build 15-minute bars, which
    # is not its job and not true.
    decisions: dict[str, list[Bartimeframe]] = {
        "timeframes.enabled": [],
        "timeframes.intraday_enabled": [],
    }
    for field, raw_frames in (
        ("timeframes.enabled", config.timeframes.enabled),
        ("timeframes.intraday_enabled", config.timeframes.intraday_enabled),
    ):
        if not raw_frames and field == "timeframes.enabled":
            findings.append(
                Finding(
                    Defect.NOTHING_ENABLED,
                    field,
                    "no timeframe is enabled, so nothing can ever be computed",
                )
            )
        for raw in raw_frames:
            frame = _timeframe(raw)
            if frame is None:
                findings.append(
                    Finding(
                        Defect.UNKNOWN_TIMEFRAME,
                        f"{field}/{raw}",
                        f"not a Bartimeframe; known sizes are {[t.value for t in Bartimeframe]}",
                    )
                )
                continue
            decisions[field].append(frame)
            if frame not in admitted:
                findings.append(
                    Finding(
                        Defect.TIMEFRAME_OUTSIDE_MANDATE,
                        f"{field}/{raw}",
                        f"the {mandate.value} mandate admits "
                        f"{[t.value for t in admitted]}; a strategy may select within "
                        "its mandate's eligible hierarchy, not outside it",
                    )
                )

    # Construction inputs: existence and direction only, never the mandate.
    for field, raw, builds in (
        ("timeframes.base_timeframe", config.timeframes.base_timeframe, "timeframes.enabled"),
        (
            "timeframes.intraday_base",
            config.timeframes.intraday_base,
            "timeframes.intraday_enabled",
        ),
    ):
        frame = _timeframe(raw)
        if frame is None:
            findings.append(
                Finding(
                    Defect.UNKNOWN_TIMEFRAME,
                    f"{field}/{raw}",
                    f"not a Bartimeframe; known sizes are {[t.value for t in Bartimeframe]}",
                )
            )
            continue
        finer_targets = [d for d in decisions[builds] if _ORDER[d] < _ORDER[frame]]
        if finer_targets:
            findings.append(
                Finding(
                    Defect.BASE_COARSER_THAN_TARGET,
                    f"{field}/{raw}",
                    f"{builds} needs "
                    f"{sorted({t.value for t in finer_targets})}, which are finer "
                    "than the bars it would be aggregated from",
                )
            )

    return ValidationReport(strategy_name=config.name, mandate=mandate, findings=tuple(findings))


def require_valid(config: StrategyConfig, mandate: Mandate) -> ValidationReport:
    """:func:`validate`, refusing rather than reporting."""
    report = validate(config, mandate)
    if not report.valid:
        raise StrategyInvalid(report)
    return report


def validated(
    version: StrategyVersion, config: StrategyConfig, *, citation: str = ""
) -> StrategyVersion:
    """Perform ``DRAFT → VALIDATED``, and only if every reference resolves.

    The mandate comes from the *version*, not from the caller: §7 makes a
    mandate part of the definition, and letting a caller pass a different one
    would make the check answer a question nobody asked.
    """
    report = require_valid(config, version.mandate)
    return version.transition(
        StrategyState.VALIDATED,
        citation=citation or f"registry validation: {report.describe()}",
    )
