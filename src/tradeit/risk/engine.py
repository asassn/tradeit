"""Aggregation of risk rules into one verdict.

The aggregation is deliberately unsophisticated, as the ``RiskEngine`` protocol
says: **take the most restrictive answer.** No weighting, no majority, no
override for a strong signal. Every cleverer scheme is a mechanism for talking
yourself into a trade the system already declined, and it will be reached for
precisely when the limit is most needed.

Two behaviours worth stating because the obvious implementation gets them
wrong:

**Every rule is evaluated, even after one has rejected.** Short-circuiting on
the first ``REJECT`` would be faster and would produce a verdict recording one
breach where there were four. The protocol requires that decisions be recorded
including the allowances, and "how close were the other limits?" is the
question that distinguishes a mis-calibrated limit from a bad trade.

**A proposal the sizer already refused is not re-adjudicated.** The engine
reports the sizer's own reason rather than an empty ``ALLOW``, because a
proposal of zero shares passes every limit trivially and would otherwise be
recorded as permitted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from tradeit.core.enums import RiskDecision, RiskLimitType
from tradeit.portfolio.base import PortfolioState, SizingDecision
from tradeit.risk.base import RiskAssessment, RiskRule, RiskVerdict
from tradeit.strategy.config import SizingConfig

__all__ = ["SIZER_RULE_NAME", "MostRestrictiveEngine"]

#: Name carried by the synthetic assessment describing a sizer refusal, so a
#: verdict's ``binding_rule`` is never ``None`` when nothing may be bought.
SIZER_RULE_NAME = "sizer"


@dataclass(frozen=True, slots=True)
class MostRestrictiveEngine:
    """Runs every rule and returns the tightest answer any of them gave."""

    rule_set: tuple[RiskRule, ...]
    sizing: SizingConfig

    def __post_init__(self) -> None:
        names = [rule.name for rule in self.rule_set]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            # ``binding_rule`` returns a name; two rules sharing one make the
            # answer ambiguous exactly when it is being read under pressure.
            raise ValueError(f"duplicate risk rule names: {', '.join(duplicates)}")

    @property
    def rules(self) -> Sequence[RiskRule]:
        return self.rule_set

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskVerdict:
        if proposal.rejected:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                assessments=(
                    RiskAssessment(
                        rule_name=SIZER_RULE_NAME,
                        limit_type=RiskLimitType.POSITION_RISK,
                        decision=RiskDecision.REJECT,
                        reason=proposal.rejection_reason or "sizer refused the proposal",
                    ),
                ),
                final_quantity=Decimal(0),
            )

        assessments = tuple(rule.evaluate(proposal, portfolio) for rule in self.rule_set)

        quantity = proposal.quantity
        for assessment in assessments:
            if (
                assessment.decision is RiskDecision.ALLOW_REDUCED
                and assessment.max_allowed_quantity is not None
            ):
                quantity = min(quantity, assessment.max_allowed_quantity)
        if not self.sizing.allow_fractional_shares:
            quantity = quantity.to_integral_value(rounding=ROUND_DOWN)

        if any(assessment.blocks for assessment in assessments) or quantity <= 0:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                assessments=assessments,
                final_quantity=Decimal(0),
            )
        if quantity < proposal.quantity:
            return RiskVerdict(
                decision=RiskDecision.ALLOW_REDUCED,
                assessments=assessments,
                final_quantity=quantity,
            )
        return RiskVerdict(
            decision=RiskDecision.ALLOW,
            assessments=assessments,
            final_quantity=quantity,
        )
