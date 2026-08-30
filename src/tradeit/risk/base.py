"""Interfaces for portfolio risk control.

Risk rules are the last gate before an order exists. They are separated from
sizing for a specific reason: a sizer answers "how big?", a risk rule answers
"at all?", and merging the two produces a system where a limit breach quietly
becomes a smaller position instead of a refusal.

Three properties every implementation must honour:

**Rules veto; they do not negotiate.** A rule returns ``REJECT``,
``ALLOW_REDUCED`` with a maximum, or ``ALLOW``. The aggregate is the *most*
restrictive answer across all rules — never an average, never a majority vote.
One rule saying "no" is sufficient.

**Rules are evaluated against a snapshot.** All rules see the same
``PortfolioState``, so a rule cannot pass because it happened to read the
database a moment before another position opened.

**Every decision is recorded, including the allowances.** A rejected trade is a
decision, and post-trade analysis needs to know what the system declined to buy.
Recording only what was traded makes the strategy look better than it is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradeit.core.enums import RiskDecision, RiskLimitType
from tradeit.portfolio.base import PortfolioState, SizingDecision


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """One rule's verdict on one proposed trade.

    ``measured`` and ``limit`` are kept alongside the verdict so a rejection is
    self-explaining: "portfolio heat 6.4% exceeds limit 6.0%" needs no
    follow-up query, and the numbers make it obvious when a limit is
    mis-calibrated rather than the trade being wrong.
    """

    rule_name: str
    limit_type: RiskLimitType
    decision: RiskDecision
    reason: str
    measured: float | None = None
    limit: float | None = None
    max_allowed_quantity: Decimal | None = None

    def __post_init__(self) -> None:
        if self.decision is RiskDecision.ALLOW_REDUCED and self.max_allowed_quantity is None:
            raise ValueError(
                f"{self.rule_name} returned ALLOW_REDUCED without a maximum quantity; "
                "a reduction with no bound is a rejection wearing a permissive label"
            )

    @property
    def blocks(self) -> bool:
        return self.decision is RiskDecision.REJECT


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    """The aggregate of every rule's assessment of one proposed trade."""

    decision: RiskDecision
    assessments: tuple[RiskAssessment, ...]
    final_quantity: Decimal

    @property
    def blocking_rules(self) -> list[str]:
        return [a.rule_name for a in self.assessments if a.blocks]

    @property
    def binding_rule(self) -> str | None:
        """Which rule actually determined the outcome.

        The one piece of information that turns "the system isn't buying
        anything" into an actionable answer.
        """
        for assessment in self.assessments:
            if assessment.blocks:
                return assessment.rule_name
        reducers = [
            a
            for a in self.assessments
            if a.decision is RiskDecision.ALLOW_REDUCED and a.max_allowed_quantity is not None
        ]
        if not reducers:
            return None
        return min(reducers, key=lambda a: a.max_allowed_quantity or Decimal(0)).rule_name

    def explain(self) -> str:
        if self.decision is RiskDecision.REJECT:
            blocking = [a for a in self.assessments if a.blocks]
            return "; ".join(f"{a.rule_name}: {a.reason}" for a in blocking)
        if self.decision is RiskDecision.ALLOW_REDUCED:
            return f"reduced to {self.final_quantity} by {self.binding_rule}"
        return "within all limits"


@runtime_checkable
class RiskRule(Protocol):
    """A single constraint on what the portfolio may do.

    Rules are pure functions of (proposal, state, config). They do not read the
    database, do not consult wall-clock time, and do not mutate anything —
    which is what lets a backtest evaluate the exact rules that would run live.
    """

    name: str
    limit_type: RiskLimitType

    @property
    def parameters(self) -> dict[str, object]: ...

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment: ...


@runtime_checkable
class RiskEngine(Protocol):
    """Aggregates rules into one verdict.

    The aggregation is intentionally unsophisticated: take the most restrictive
    answer. Anything cleverer — weighting rules, allowing a strong signal to
    override a limit — is a mechanism for talking yourself into a trade the
    system already declined.
    """

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskVerdict: ...

    @property
    def rules(self) -> Sequence[RiskRule]: ...


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """Portfolio-level risk metrics at one instant, persisted daily.

    Recorded whether or not anything traded. The value of this table is in the
    quiet periods: it is what lets you answer "was the drawdown a risk-control
    failure or a run of ordinary losing trades?" months later.
    """

    portfolio_id: int
    as_of: str
    equity: Decimal
    cash: Decimal
    open_risk: Decimal
    heat: Decimal
    position_count: int
    largest_position_pct: Decimal
    gross_exposure_pct: Decimal
    sector_exposures: dict[str, float] = field(default_factory=dict)
    max_pairwise_correlation: float | None = None
    drawdown_from_peak: Decimal = Decimal(0)
    limit_breaches: tuple[str, ...] = ()
