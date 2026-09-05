"""Portfolio mandates: three horizons that must never share a sample.

TradeIt runs **three portfolio mandates at materially different horizons** —
Day, Swing and Retirement — and the architecture's first claim is that they are
three separate statistical populations, not one strategy with three holding
periods. A 5-minute bull flag for the day portfolio and a Daily bull flag for
the swing portfolio are different objects that happen to share a name.

This module makes that claim **checkable instead of aspirational**. It supplies
the mandate table from ``docs/MULTI_TIMEFRAME_MANDATES.md`` §4 as data, a
refusal for timeframes a mandate does not admit, and a test for the separation
rule stated in that section:

    No learning, backtesting, calibration or scoring population may contain
    observations from more than one mandate, or from more than one timeframe,
    unless the combination is itself the declared unit of study.

**Refuse, rather than compute-and-hope.** The detector registry already works
this way one level down: ``patterns.registry.SUPPORTED_TIMEFRAMES`` records
where each family's definition is *meaningful* and the scanner declines the
rest. A mandate's eligible-timeframe set is the same discipline one layer up,
and :func:`require_timeframe` raises rather than returning a number nobody
should trust.

Two things this deliberately does **not** do, both because the roadmap places
them later and asserting them here would be inventing evidence:

* **No coordinator.** Nothing here decides what a Monthly uptrend plus a Daily
  retest *means*, and nothing computes a cross-timeframe score. A security has
  no global state; four observations at four timeframes are four independent
  causal observations, and collapsing them is the error the design exists to
  prevent.
* **No survivorship claim.** Survivorship posture belongs to a corpus's own
  contract, not to the mandate that reads it — writing the posture down twice
  is how the two copies come to disagree. :class:`Corpus` names which corpus a
  mandate draws on and stops there. ``intraday-01`` is expected to be
  survivorship-biased and must say so itself (§6.4).

``4h`` appears in no mandate. That is not an oversight: §3.2 defines its
semantics and withholds its adoption, so a detector family may support ``4h``
while no mandate admits it. :func:`admits` returns ``False`` for it everywhere,
and a test pins that so the exclusion cannot decay into an accident.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from tradeit.core.enums import Bartimeframe

__all__ = [
    "MANDATES",
    "Corpus",
    "Finding",
    "Mandate",
    "MandateSpec",
    "Observation",
    "PopulationNotAdmissible",
    "PopulationReport",
    "PopulationViolation",
    "Role",
    "TimeframeHierarchy",
    "TimeframeNotEligible",
    "UnitOfStudy",
    "admits",
    "check_population",
    "eligible_timeframes",
    "require_population",
    "require_timeframe",
    "spec",
]


class Mandate(StrEnum):
    """The three horizons. There is no fourth, and no aggregate of them."""

    DAY = "day"
    SWING = "swing"
    RETIREMENT = "retirement"


class Corpus(StrEnum):
    """Named corpora a mandate may draw on.

    Each carries its own data contract; this enum is a reference to one, never
    a summary of it.
    """

    RESEARCH_01 = "research-01"
    INTRADAY_01 = "intraday-01"


class Role(StrEnum):
    """What a timeframe is being used *for* within a mandate.

    The same bar size plays different roles in different mandates — ``1d`` is
    Swing's setup and Retirement's trigger — so a timeframe alone never
    identifies a role.
    """

    CONTEXT = "context"
    SETUP = "setup"
    TRIGGER = "trigger"


class TimeframeNotEligible(ValueError):
    """A mandate was asked to work at a timeframe it does not admit."""

    def __init__(self, mandate: Mandate, timeframe: Bartimeframe) -> None:
        eligible = ", ".join(tf.value for tf in eligible_timeframes(mandate))
        super().__init__(
            f"the {mandate.value} mandate does not admit {timeframe.value}; "
            f"eligible timeframes are {eligible}"
        )
        self.mandate = mandate
        self.timeframe = timeframe


@dataclass(frozen=True, slots=True)
class TimeframeHierarchy:
    """Context, setup and trigger — coarse to fine, per mandate.

    Ordered coarsest-first within each role, matching how the table reads in
    §4 and how a human describes the hierarchy out loud.
    """

    context: tuple[Bartimeframe, ...]
    setup: tuple[Bartimeframe, ...]
    trigger: tuple[Bartimeframe, ...]

    def roles_of(self, timeframe: Bartimeframe) -> tuple[Role, ...]:
        """Every role this timeframe plays here — plural, and sometimes empty.

        A timeframe can hold two roles in one mandate: Swing's ``1d`` is both
        its context's fine end and its setup's coarse end. Returning a single
        role would force a choice the hierarchy does not make.
        """
        return tuple(
            role
            for role, frames in (
                (Role.CONTEXT, self.context),
                (Role.SETUP, self.setup),
                (Role.TRIGGER, self.trigger),
            )
            if timeframe in frames
        )

    def all_timeframes(self) -> tuple[Bartimeframe, ...]:
        """Every timeframe named by the hierarchy, deduplicated, coarse first."""
        seen: dict[Bartimeframe, None] = {}
        for frames in (self.context, self.setup, self.trigger):
            for frame in frames:
                seen[frame] = None
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class MandateSpec:
    """One mandate's separately-owned properties.

    Everything here is per-mandate and none of it is global: the eligible
    timeframe set, the hierarchy, the holding horizon and the corpus. Risk
    model, stop methodology, sizing, backtest, KPIs and capital-graduation
    criteria are equally per-mandate and live in their own layers — they are
    named in §4 and deliberately not duplicated here.
    """

    mandate: Mandate
    hierarchy: TimeframeHierarchy
    eligible: tuple[Bartimeframe, ...]
    holding_horizon: str
    objective: str
    primary_corpus: Corpus
    supporting_corpora: tuple[Corpus, ...] = ()

    def admits(self, timeframe: Bartimeframe) -> bool:
        return timeframe in self.eligible


#: Day: intraday moves, hours. Draws on the intraday corpus, whose survivorship
#: posture is declared by that corpus and is expected to be biased (§6.4).
_DAY = MandateSpec(
    mandate=Mandate.DAY,
    hierarchy=TimeframeHierarchy(
        context=(Bartimeframe.D1, Bartimeframe.H1),
        setup=(Bartimeframe.H1, Bartimeframe.M15),
        trigger=(Bartimeframe.M5, Bartimeframe.M1),
    ),
    eligible=(
        Bartimeframe.M1,
        Bartimeframe.M5,
        Bartimeframe.M15,
        Bartimeframe.M30,
        Bartimeframe.H1,
        Bartimeframe.D1,
    ),
    holding_horizon="hours",
    objective="intraday moves",
    primary_corpus=Corpus.INTRADAY_01,
)

#: Swing: days to roughly three months. EOD is primary; intraday supports
#: entries only, which is why ``intraday-01`` is a supporting corpus rather
#: than a second primary one.
_SWING = MandateSpec(
    mandate=Mandate.SWING,
    hierarchy=TimeframeHierarchy(
        context=(Bartimeframe.W1, Bartimeframe.D1),
        setup=(Bartimeframe.D1, Bartimeframe.H1),
        trigger=(Bartimeframe.H1, Bartimeframe.M15),
    ),
    eligible=(
        Bartimeframe.M15,
        Bartimeframe.M30,
        Bartimeframe.H1,
        Bartimeframe.D1,
        Bartimeframe.W1,
    ),
    holding_horizon="days to ~3 months",
    objective="swing moves within a trend",
    primary_corpus=Corpus.RESEARCH_01,
    supporting_corpora=(Corpus.INTRADAY_01,),
)

#: Retirement: multi-year trends and compounding. ``1d`` is admitted and used
#: tactically, but intraday resolution does not drive this mandate — stated as
#: an eligible-timeframe set rather than as guidance, so it can be enforced.
_RETIREMENT = MandateSpec(
    mandate=Mandate.RETIREMENT,
    hierarchy=TimeframeHierarchy(
        context=(Bartimeframe.MN1, Bartimeframe.W1),
        setup=(Bartimeframe.W1, Bartimeframe.D1),
        trigger=(Bartimeframe.D1,),
    ),
    eligible=(Bartimeframe.D1, Bartimeframe.W1, Bartimeframe.MN1),
    holding_horizon="years",
    objective="multi-year trends, compounding",
    primary_corpus=Corpus.RESEARCH_01,
)

#: The mandate table from §4, as data rather than as prose.
MANDATES: Mapping[Mandate, MandateSpec] = {
    Mandate.DAY: _DAY,
    Mandate.SWING: _SWING,
    Mandate.RETIREMENT: _RETIREMENT,
}


def spec(mandate: Mandate) -> MandateSpec:
    return MANDATES[mandate]


def eligible_timeframes(mandate: Mandate) -> tuple[Bartimeframe, ...]:
    return MANDATES[mandate].eligible


def admits(mandate: Mandate, timeframe: Bartimeframe) -> bool:
    return MANDATES[mandate].admits(timeframe)


def require_timeframe(mandate: Mandate, timeframe: Bartimeframe) -> Bartimeframe:
    """Return ``timeframe`` if the mandate admits it, else refuse.

    The refusal is the point. A Retirement decision computed on 5-minute bars
    is not a worse Retirement decision, it is a different question answered
    under the wrong name, and returning it with a caveat attached invites it to
    be acted on anyway.
    """
    if not admits(mandate, timeframe):
        raise TimeframeNotEligible(mandate, timeframe)
    return timeframe


# --------------------------------------------------------------------------
# The separation rule
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One row of a population, carrying the two facts purity is defined over.

    Nothing else about the observation matters here — this is a test of *which
    populations may be pooled*, not of what any observation says.
    """

    mandate: Mandate
    timeframe: Bartimeframe


@dataclass(frozen=True, slots=True)
class UnitOfStudy:
    """What a population claims to be studying, declared before it is checked.

    The rule permits a mixed population when the mixture is itself the unit of
    study. That escape is only honest if the mixture is *declared in advance*
    and visible afterwards, so :attr:`combined` stays true for such a unit and
    no report of it can be read as a single-mandate result.
    """

    mandates: frozenset[Mandate]
    timeframes: frozenset[Bartimeframe]

    @property
    def combined(self) -> bool:
        """Is this population inherently cross-mandate or cross-timeframe?"""
        return len(self.mandates) > 1 or len(self.timeframes) > 1

    @staticmethod
    def single(mandate: Mandate, timeframe: Bartimeframe) -> UnitOfStudy:
        """The ordinary case: one mandate, one timeframe."""
        require_timeframe(mandate, timeframe)
        return UnitOfStudy(mandates=frozenset({mandate}), timeframes=frozenset({timeframe}))


class PopulationViolation(StrEnum):
    """Why a population is not admissible evidence for its declared unit."""

    #: An observation's mandate is outside the declared unit.
    UNDECLARED_MANDATE = "undeclared_mandate"
    #: An observation's timeframe is outside the declared unit.
    UNDECLARED_TIMEFRAME = "undeclared_timeframe"
    #: The pairing is inside the declaration but the mandate does not admit the
    #: timeframe — the declaration itself was wrong.
    INELIGIBLE_TIMEFRAME = "ineligible_timeframe"
    #: A unit of study that names no mandate or no timeframe. Nothing is under
    #: study, so nothing can be concluded.
    EMPTY_DECLARATION = "empty_declaration"
    #: No observations. Not a purity failure, and refused anyway: a population
    #: that passes because it is empty is the vacuous result this codebase has
    #: already produced twice by promoting away the last instance of a shape.
    EMPTY_POPULATION = "empty_population"


@dataclass(frozen=True, slots=True)
class Finding:
    """One violation, with the count of observations that raised it."""

    violation: PopulationViolation
    mandate: Mandate | None
    timeframe: Bartimeframe | None
    count: int

    def describe(self) -> str:
        where = " / ".join(
            part.value for part in (self.mandate, self.timeframe) if part is not None
        )
        return f"{self.violation.value}: {where or '-'} ({self.count:,})"


@dataclass(frozen=True, slots=True)
class PopulationReport:
    """What the population is, and whether it may be used as declared."""

    unit: UnitOfStudy
    total: int
    present: Mapping[tuple[Mandate, Bartimeframe], int]
    findings: tuple[Finding, ...]

    @property
    def admissible(self) -> bool:
        return not self.findings

    @property
    def combined(self) -> bool:
        """True when the declared unit is itself a mixture.

        Carried through from the declaration so a caller reading only the
        report still cannot mistake a cross-timeframe study for a clean one.
        """
        return self.unit.combined

    def describe(self) -> str:
        if self.admissible:
            shape = "combined" if self.combined else "single"
            return f"admissible ({shape}); {self.total:,} observations"
        return "not admissible; " + "; ".join(f.describe() for f in self.findings)


def check_population(observations: Iterable[Observation], unit: UnitOfStudy) -> PopulationReport:
    """Test a population against the unit of study it claims to be.

    Returns a report rather than raising, because the caller that assembles a
    population is usually the one that can narrow it, and a report says *which*
    observations do not belong. :func:`require_population` is the raising form
    for callers that cannot recover.
    """
    counts: Counter[tuple[Mandate, Bartimeframe]] = Counter()
    for observation in observations:
        counts[(observation.mandate, observation.timeframe)] += 1
    total = sum(counts.values())

    findings: list[Finding] = []
    if not unit.mandates or not unit.timeframes:
        findings.append(Finding(PopulationViolation.EMPTY_DECLARATION, None, None, 0))
    if total == 0:
        findings.append(Finding(PopulationViolation.EMPTY_POPULATION, None, None, 0))

    for (mandate, timeframe), count in sorted(counts.items(), key=lambda kv: kv[0]):
        if mandate not in unit.mandates:
            findings.append(
                Finding(PopulationViolation.UNDECLARED_MANDATE, mandate, timeframe, count)
            )
            continue
        if timeframe not in unit.timeframes:
            findings.append(
                Finding(PopulationViolation.UNDECLARED_TIMEFRAME, mandate, timeframe, count)
            )
            continue
        if not admits(mandate, timeframe):
            findings.append(
                Finding(PopulationViolation.INELIGIBLE_TIMEFRAME, mandate, timeframe, count)
            )

    return PopulationReport(unit=unit, total=total, present=dict(counts), findings=tuple(findings))


class PopulationNotAdmissible(ValueError):
    """A population mixes mandates or timeframes it did not declare."""

    def __init__(self, report: PopulationReport) -> None:
        super().__init__(report.describe())
        self.report = report


def require_population(observations: Iterable[Observation], unit: UnitOfStudy) -> PopulationReport:
    """:func:`check_population`, refusing rather than reporting."""
    report = check_population(observations, unit)
    if not report.admissible:
        raise PopulationNotAdmissible(report)
    return report
