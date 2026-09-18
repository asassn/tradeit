"""Simulated costs and fills, shared by the backtester and the paper broker.

These two classes are where most fictitious backtest returns come from, so each
rule below is written against the naive implementation it replaces.

**Slippage is always adverse, never symmetric.** A buy pays more than the
reference price and a sell receives less — never the reverse. Modelling
slippage as a zero-mean random draw is defensible in the abstract and wrong in
practice: it averages out across a backtest and quietly returns the frictionless
result, which is the number the model existed to avoid.

**Cost rises with participation.** A flat basis-point model lets a backtest buy
a million shares of an illiquid name at the closing price. The impact term is
proportional to the fraction of a session's dollar volume the order consumes,
so the same order costs more in a thin name than a liquid one.

**Gaps are respected, and this is the rule that matters most.** A stop at 50 on
a session that opens at 44 fills at 44. Backtests that assume stops fill at
their trigger price systematically understate drawdown, and they understate it
worst in exactly the sessions that decide whether a strategy survives.
``PositionState.open_risk`` says the stop assumption is optimistic and points
here; this is where the optimism is removed.

**Nothing fills outside the bar's range**, and **volume caps the fill**. An
order for 30% of a session's volume does not all fill at one price; it fills
partially, and the remainder is the caller's problem rather than a free
completion.

**The model fills against the bar it is handed.** It does not know what
"tomorrow" means. Keeping the decision-to-execution offset in the engine rather
than here is deliberate: there is exactly one place to check that a decision
taken on session T executes against T+1, and it is not spread across two
modules.

``max_participation`` has no default. It is the number that decides whether a
backtest is realistic, and a default would let it be inherited by accident.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from tradeit.core.enums import OrderSide, OrderType
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import CostEstimate, Fill, OrderRequest
from tradeit.strategy.config import CostConfig

__all__ = ["BUY_SIDES", "BarFillModel", "ParticipationCostModel"]

#: Sides that acquire exposure and therefore pay up rather than down.
BUY_SIDES = frozenset({OrderSide.BUY, OrderSide.BUY_TO_COVER})

_BPS = Decimal(10000)


@dataclass(frozen=True, slots=True)
class ParticipationCostModel:
    """Commission, half-spread and a participation-scaled impact term.

    ``expected_fill_price`` moves against the order by the spread and impact
    per share, so a caller that uses it without reading the components still
    gets a conservative number.
    """

    config: CostConfig
    name: str = "participation"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "commission_per_share": self.config.commission_per_share,
            "commission_minimum": self.config.commission_minimum,
            "slippage_bps": self.config.slippage_bps,
            "spread_bps": self.config.spread_bps,
            "market_impact_coefficient": self.config.market_impact_coefficient,
        }

    def estimate(
        self,
        order: OrderRequest,
        reference_bar: OhlcvBar,
        average_dollar_volume: Decimal | None = None,
    ) -> CostEstimate:
        return self.estimate_at(
            side=order.side,
            quantity=order.quantity,
            reference=reference_bar.close,
            average_dollar_volume=average_dollar_volume,
        )

    def estimate_at(
        self,
        *,
        side: OrderSide,
        quantity: Decimal,
        reference: Decimal,
        average_dollar_volume: Decimal | None = None,
    ) -> CostEstimate:
        """The same estimate from a price rather than a bar.

        For a caller that has a proposal but no order and no bar yet -- the
        transaction-cost risk rule, which must price a trade before it exists
        and must price it with exactly the arithmetic that will charge it.
        """
        notional = reference * quantity

        commission = max(
            Decimal(str(self.config.commission_per_share)) * quantity,
            Decimal(str(self.config.commission_minimum)),
        )

        # Half the quoted spread, plus the fixed slippage allowance. Both are
        # charged on every order in the adverse direction.
        friction_bps = Decimal(str(self.config.spread_bps)) / 2 + Decimal(
            str(self.config.slippage_bps)
        )
        spread_cost = notional * friction_bps / _BPS

        participation = 0.0
        impact = Decimal(0)
        if average_dollar_volume is not None and average_dollar_volume > 0:
            participation = float(notional / average_dollar_volume)
            # Linear in participation: an order that is 10% of a day's volume
            # pays ten times the impact of one that is 1%.
            impact = (
                notional
                * Decimal(str(self.config.market_impact_coefficient))
                * Decimal(str(participation))
            )

        per_share = (spread_cost + impact) / quantity
        direction = 1 if side in BUY_SIDES else -1
        return CostEstimate(
            commission=commission,
            spread_cost=spread_cost,
            market_impact=impact,
            expected_fill_price=reference + direction * per_share,
            participation_rate=participation,
        )


@dataclass(frozen=True, slots=True)
class BarFillModel:
    """Fills an order against one bar, honouring range, gaps and volume.

    ``max_participation`` is the largest share of a session's volume one order
    may take. Anything above it fills partially.
    """

    max_participation: Decimal
    name: str = "bar"

    @property
    def parameters(self) -> dict[str, object]:
        return {"max_participation": str(self.max_participation)}

    def simulate(
        self,
        order: OrderRequest,
        bar: OhlcvBar,
        cost: CostEstimate,
    ) -> Fill | None:
        """The fill, or ``None`` when the order did not trigger.

        ``None`` means "no execution today", not "rejected": an unfilled limit
        order is still live, and it is the caller that decides whether to keep
        working it.
        """
        if order.instrument_id != bar.instrument_id:
            raise ValueError(
                f"order is for instrument {order.instrument_id} and the bar is for "
                f"{bar.instrument_id}; filling one against the other would invent a price"
            )
        trigger = self._trigger_price(order, bar)
        if trigger is None:
            return None

        quantity = self._fillable(order.quantity, bar.volume)
        if quantity <= 0:
            return None

        buying = order.side in BUY_SIDES
        slip_per_share = abs(cost.spread_cost + cost.market_impact) / order.quantity
        price = trigger + slip_per_share if buying else trigger - slip_per_share
        # Slippage may not push the fill outside the session's range: the
        # exchange did not print a price there, and pretending otherwise trades
        # one fiction for another.
        price = min(max(price, bar.low), bar.high)

        commission = cost.commission * quantity / order.quantity
        return Fill(
            order_id=0,
            instrument_id=order.instrument_id,
            side=order.side,
            quantity=quantity,
            price=price,
            filled_at=dt.datetime.combine(bar.session_date, dt.time(), tzinfo=dt.UTC),
            commission=commission,
            slippage=abs(price - trigger) * quantity,
            venue="simulated",
        )

    def _fillable(self, wanted: Decimal, volume: Decimal) -> Decimal:
        """How much of the order the session could absorb.

        A session with no volume fills nothing. A halted or untraded day is not
        an opportunity to transact at yesterday's price.
        """
        if volume <= 0:
            return Decimal(0)
        cap = (volume * self.max_participation).to_integral_value(rounding=ROUND_DOWN)
        return min(wanted, cap)

    @staticmethod
    def _trigger_price(order: OrderRequest, bar: OhlcvBar) -> Decimal | None:
        """Where the order would execute before slippage, or ``None``.

        Every branch takes the *worse* of the trigger and the open. That single
        choice is what makes a gap cost what it costs: a stop-loss on a session
        that opens below it fills at the open, not at the stop.
        """
        buying = order.side in BUY_SIDES

        if order.order_type in (OrderType.MARKET, OrderType.MARKET_ON_OPEN):
            return bar.open
        if order.order_type is OrderType.MARKET_ON_CLOSE:
            return bar.close

        if order.order_type is OrderType.LIMIT:
            limit = order.limit_price
            assert limit is not None  # OrderRequest.__post_init__ guarantees it
            if buying:
                # Nothing fills below the session's low.
                if bar.low > limit:
                    return None
                # An open below the limit is a better price, and it is real.
                return min(limit, bar.open)
            if bar.high < limit:
                return None
            return max(limit, bar.open)

        stop = order.stop_price
        assert stop is not None
        if order.order_type is OrderType.STOP:
            if buying:
                if bar.high < stop:
                    return None
                return max(stop, bar.open)  # gap up: pay the open
            if bar.low > stop:
                return None
            return min(stop, bar.open)  # gap down: take the open

        # STOP_LIMIT: the stop must trigger, and then the limit must be
        # respected. A gap through both is the case that produces no fill at
        # all -- the protection worked and the exit did not happen, which is a
        # real and unpleasant outcome the model must be able to express.
        limit = order.limit_price
        assert limit is not None
        if buying:
            if bar.high < stop:
                return None
            entry = max(stop, bar.open)
            return entry if entry <= limit else None
        if bar.low > stop:
            return None
        entry = min(stop, bar.open)
        return entry if entry >= limit else None
