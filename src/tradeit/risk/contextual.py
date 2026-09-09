"""Risk rules that need evidence the portfolio snapshot does not carry.

A ``PortfolioState`` knows what is held, at what price, with what stop. It does
not know which sector a name belongs to, how two names move together, or what
the account was worth at its peak. Those rules take that evidence at
construction rather than reaching for it during evaluation, which keeps the
protocol's promise that a rule is a pure function and that every rule in one
pass sees the same world.

Each of these rules **fails closed**, and each says so differently:

* an unclassified instrument is pooled into a single ``UNCLASSIFIED`` bucket
  rather than given a sector of its own
* an unknown correlation blocks rather than permits
* a missing peak or session-open equity is not defaulted to today's equity,
  which would silently report a drawdown of zero

The rejected alternative in each case was the same shape — treat missing
evidence as permissive — and it fails identically: the limit appears enforced,
reports comfortable numbers, and constrains nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradeit.core.enums import RiskDecision, RiskLimitType
from tradeit.portfolio.base import PortfolioState, SizingDecision
from tradeit.risk.base import RiskAssessment
from tradeit.risk.rules import verdict_for_headroom
from tradeit.strategy.config import RiskConfig

__all__ = [
    "UNCLASSIFIED",
    "CorrelationRule",
    "CorrelationSource",
    "DailyLossRule",
    "DrawdownHaltRule",
    "MatrixCorrelationSource",
    "SectorExposureRule",
]

#: The bucket every instrument with no sector classification shares.
#:
#: Pooling them is the conservative reading: it can only refuse more than the
#: truth, never less. Giving each unknown its own sector would be the
#: permissive reading, under which a portfolio of twelve unclassified banks
#: passes a sector limit designed to prevent exactly that.
UNCLASSIFIED = "UNCLASSIFIED"


@runtime_checkable
class CorrelationSource(Protocol):
    """Pairwise correlation, or ``None`` when it is not known.

    ``None`` is a real answer and must not be conflated with zero. Zero says
    two names are independent; ``None`` says nobody has checked, and the two
    lead to opposite decisions.
    """

    def correlation(self, a: int, b: int) -> float | None: ...


@dataclass(frozen=True, slots=True)
class MatrixCorrelationSource:
    """A ``CorrelationSource`` backed by a mapping of unordered pairs."""

    values: Mapping[tuple[int, int], float]

    def correlation(self, a: int, b: int) -> float | None:
        if a == b:
            return 1.0
        key = (a, b) if a <= b else (b, a)
        return self.values.get(key, self.values.get((key[1], key[0])))


@dataclass(frozen=True, slots=True)
class SectorExposureRule:
    """Exposure to one sector against ``max_sector_exposure_pct``.

    Measured on the sector *after* the trade, and including every position
    already in that sector — the concentration a sector limit exists to catch
    is built one individually reasonable position at a time.
    """

    config: RiskConfig
    sectors: Mapping[int, str]
    name: str = "sector_exposure"
    limit_type: RiskLimitType = RiskLimitType.SECTOR_EXPOSURE

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "max_sector_exposure_pct": self.config.max_sector_exposure_pct,
            "classified_instruments": len(self.sectors),
        }

    def _sector_of(self, instrument_id: int) -> str:
        return self.sectors.get(instrument_id, UNCLASSIFIED)

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        limit = Decimal(str(self.config.max_sector_exposure_pct))
        if portfolio.equity <= 0 or proposal.entry_price <= 0:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason="equity or entry price is not positive; exposure is undefined",
                limit=float(limit),
            )
        sector = self._sector_of(proposal.instrument_id)
        held = sum(
            (
                p.market_value(portfolio.last_prices.get(p.instrument_id, p.average_entry_price))
                for p in portfolio.open_positions
                if self._sector_of(p.instrument_id) == sector
            ),
            Decimal(0),
        )
        combined = held + proposal.notional
        allowed = max(limit * portfolio.equity - held, Decimal(0)) / proposal.entry_price
        qualifier = " (unclassified names are pooled)" if sector == UNCLASSIFIED else ""
        return verdict_for_headroom(
            rule_name=self.name,
            limit_type=self.limit_type,
            allowed=allowed,
            proposal=proposal,
            reason=(
                f"{sector} exposure would be {combined / portfolio.equity:.2%}, "
                f"above the {limit:.2%} limit{qualifier}"
            ),
            measured=float(combined / portfolio.equity),
            limit=float(limit),
        )


@dataclass(frozen=True, slots=True)
class CorrelationRule:
    """Refuses a name that moves with something already held.

    Twelve positions that all move together are one position with twelve
    commission charges, and every other limit in the system is measuring the
    twelve.

    **An unknown correlation blocks.** Installing this rule with a source that
    does not cover the universe will therefore refuse every entry after the
    first, which is the intended failure mode: it is loud, it names the pair it
    could not check, and it cannot be mistaken for diversification. The
    alternative — treating an unmeasured pair as uncorrelated — produces a
    portfolio that passes the correlation limit precisely because nobody
    measured it.
    """

    config: RiskConfig
    source: CorrelationSource
    name: str = "correlation_cluster"
    limit_type: RiskLimitType = RiskLimitType.CORRELATION_CLUSTER

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "max_correlation_for_new_position": self.config.max_correlation_for_new_position,
            "correlation_lookback_sessions": self.config.correlation_lookback_sessions,
        }

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        limit = self.config.max_correlation_for_new_position
        others = [p for p in portfolio.open_positions if p.instrument_id != proposal.instrument_id]
        if not others:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.ALLOW,
                reason="nothing else held; no pair to measure",
                limit=limit,
            )
        worst: float | None = None
        for position in others:
            value = self.source.correlation(proposal.instrument_id, position.instrument_id)
            if value is None:
                return RiskAssessment(
                    rule_name=self.name,
                    limit_type=self.limit_type,
                    decision=RiskDecision.REJECT,
                    reason=(
                        f"correlation between {proposal.instrument_id} and "
                        f"{position.instrument_id} is UNRESOLVED; the position cannot be "
                        "shown to be a separate bet"
                    ),
                    limit=limit,
                )
            worst = value if worst is None else max(worst, value)
        assert worst is not None
        if worst > limit:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason=f"correlation {worst:.2f} with a held name exceeds {limit:.2f}",
                measured=worst,
                limit=limit,
            )
        return RiskAssessment(
            rule_name=self.name,
            limit_type=self.limit_type,
            decision=RiskDecision.ALLOW,
            reason="within limit",
            measured=worst,
            limit=limit,
        )


@dataclass(frozen=True, slots=True)
class DrawdownHaltRule:
    """Stops *new* positions once the account is far enough below its peak.

    It does not touch open positions. ``RiskConfig`` says why: a drawdown halt
    that also abandons risk management makes the drawdown worse. The rule only
    ever sees proposals to buy, so the restriction is structural rather than
    remembered.
    """

    config: RiskConfig
    peak_equity: Decimal
    name: str = "drawdown_halt"
    limit_type: RiskLimitType = RiskLimitType.MAX_DRAWDOWN

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "drawdown_halt_pct": self.config.drawdown_halt_pct,
            "peak_equity": str(self.peak_equity),
        }

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        limit = Decimal(str(self.config.drawdown_halt_pct))
        if self.peak_equity <= 0:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason="peak equity is not positive; drawdown is undefined",
                limit=float(limit),
            )
        drawdown = max((self.peak_equity - portfolio.equity) / self.peak_equity, Decimal(0))
        if drawdown >= limit:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason=(
                    f"drawdown {drawdown:.2%} from the {self.peak_equity} peak has reached "
                    f"the {limit:.2%} halt; open positions continue to be managed"
                ),
                measured=float(drawdown),
                limit=float(limit),
            )
        return RiskAssessment(
            rule_name=self.name,
            limit_type=self.limit_type,
            decision=RiskDecision.ALLOW,
            reason="within limit",
            measured=float(drawdown),
            limit=float(limit),
        )


@dataclass(frozen=True, slots=True)
class DailyLossRule:
    """Stops new positions after a bad enough day.

    Distinct from the drawdown halt in what it protects against: drawdown is
    slow erosion, a daily loss limit is the day the market and the strategy
    disagree violently. Trading through the second is how the first is reached.
    """

    config: RiskConfig
    session_open_equity: Decimal
    name: str = "daily_loss"
    limit_type: RiskLimitType = RiskLimitType.DAILY_LOSS

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "max_daily_loss_pct": self.config.max_daily_loss_pct,
            "session_open_equity": str(self.session_open_equity),
        }

    def evaluate(self, proposal: SizingDecision, portfolio: PortfolioState) -> RiskAssessment:
        limit = Decimal(str(self.config.max_daily_loss_pct))
        if self.session_open_equity <= 0:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason="session-open equity is not positive; the day's loss is undefined",
                limit=float(limit),
            )
        loss = max(
            (self.session_open_equity - portfolio.equity) / self.session_open_equity, Decimal(0)
        )
        if loss >= limit:
            return RiskAssessment(
                rule_name=self.name,
                limit_type=self.limit_type,
                decision=RiskDecision.REJECT,
                reason=f"down {loss:.2%} today, at or beyond the {limit:.2%} limit",
                measured=float(loss),
                limit=float(limit),
            )
        return RiskAssessment(
            rule_name=self.name,
            limit_type=self.limit_type,
            decision=RiskDecision.ALLOW,
            reason="within limit",
            measured=float(loss),
            limit=float(limit),
        )
