"""A simulated venue that behaves like a venue, not like a backtester.

Phase 11's paper broker. It implements :class:`~tradeit.data.provider.BrokerProvider`
so the execution layer above cannot tell it from a live adapter -- which is the
whole reason paper results say anything about live behaviour.

What makes this different from the backtester
---------------------------------------------

The backtester owns the clock and knows every bar in advance. A broker does
not. So this class is split the way a real venue is:

* :meth:`submit_order` **accepts an order and fills nothing.** It returns
  ``NEW``, because that is what a venue says when it has taken an order and
  the market has not yet traded against it. An implementation that filled here
  would be answering with information it does not have.
* :meth:`advance` applies one session's bar to the resting orders. Everything
  that fills, fills *after* the order was placed, and the structure rather than
  a comment is what prevents a T-filled-at-T order.

**Idempotency is enforced, not documented.** ``client_order_id`` is generated
by the caller before submission, and resubmitting the same id returns the
existing order's status unchanged. Without that, a submission that times out
has no safe retry, and "did that order go through?" is answered by guessing.
Two *different* orders sharing an id is the dangerous case and raises.

**It refuses to run in live mode itself** (ADR-0004), rather than trusting its
caller. A paper adapter reachable from live configuration is a bug.

What it deliberately does not do
--------------------------------

**No short selling, no margin, no partial-fill queueing across sessions.** Each
is a real venue behaviour and each would be invented here rather than modelled:
borrow availability, maintenance margin and queue priority are not things this
project has evidence about. An order that cannot fill in full against one bar
fills what it can and the remainder is **cancelled at end of session**, which is
``TimeInForce.DAY`` behaving as written. Carrying a remainder forward would be
a GTC order the caller did not ask for.

**It does not price overnight gaps against the caller's intent.** The fill model
does that, and it is the same ``BarFillModel`` the backtester uses -- so a
paper fill and a backtest fill of the same order against the same bar agree by
construction rather than by coincidence.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from tradeit.config import Settings, get_settings
from tradeit.core.enums import OrderSide, OrderStatus
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import CostModel, Fill, FillModel, OrderRequest

__all__ = [
    "PaperAccount",
    "PaperBroker",
    "PaperOrderStatus",
    "PaperPosition",
    "PaperTradingRefused",
]


class PaperTradingRefused(RuntimeError):
    """The paper broker was constructed where real orders could be placed."""


@dataclass(frozen=True, slots=True)
class PaperAccount:
    """Account state as this venue reports it."""

    account_id: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    maintenance_margin: Decimal
    as_of: dt.datetime


@dataclass(frozen=True, slots=True)
class PaperPosition:
    """A position as this venue reports it."""

    instrument_id: int | None
    symbol: str
    quantity: Decimal
    average_price: Decimal
    market_value: Decimal


@dataclass(frozen=True, slots=True)
class PaperOrderStatus:
    """Order state as this venue reports it."""

    broker_order_id: str
    client_order_id: str
    status: OrderStatus
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    updated_at: dt.datetime


@dataclass
class _Resting:
    """An accepted order and what has happened to it so far."""

    order_id: int
    broker_order_id: str
    request: OrderRequest
    status: OrderStatus
    filled_quantity: Decimal = Decimal(0)
    filled_value: Decimal = Decimal(0)
    updated_at: dt.datetime = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

    @property
    def average_fill_price(self) -> Decimal | None:
        if self.filled_quantity <= 0:
            return None
        return self.filled_value / self.filled_quantity

    @property
    def remaining(self) -> Decimal:
        return self.request.quantity - self.filled_quantity

    def as_status(self) -> PaperOrderStatus:
        return PaperOrderStatus(
            broker_order_id=self.broker_order_id,
            client_order_id=self.request.client_order_id,
            status=self.status,
            filled_quantity=self.filled_quantity,
            average_fill_price=self.average_fill_price,
            updated_at=self.updated_at,
        )


@dataclass
class _Holding:
    quantity: Decimal
    cost_basis: Decimal

    @property
    def average_price(self) -> Decimal:
        if self.quantity <= 0:
            return Decimal(0)
        return self.cost_basis / self.quantity


@dataclass
class PaperBroker:
    """A simulated venue holding its own book.

    ``costs`` and ``fills`` are the same models the backtester runs, taken
    rather than constructed: a paper venue with its own cost assumptions would
    produce paper results that cannot be compared with any backtest, which
    defeats the point of having one.
    """

    account_id: str
    costs: CostModel
    fills: FillModel
    starting_cash: Decimal
    settings: Settings | None = None
    symbols: dict[int, str] = field(default_factory=dict)

    _cash: Decimal = field(init=False)
    _holdings: dict[int, _Holding] = field(init=False, default_factory=dict)
    _orders: dict[str, _Resting] = field(init=False, default_factory=dict)
    _by_client_id: dict[str, str] = field(init=False, default_factory=dict)
    _last_prices: dict[int, Decimal] = field(init=False, default_factory=dict)
    _as_of: dt.datetime = field(init=False)
    _sequence: int = field(init=False, default=0)
    _fills: list[Fill] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        settings = self.settings or get_settings()
        if not settings.orders_are_simulated:
            # Checked here rather than at the call site, per ADR-0004: an
            # adapter that trusts its caller about which mode it is in has no
            # defence against being called from the wrong one.
            raise PaperTradingRefused(
                f"PaperBroker constructed with trading_mode={settings.trading_mode}; "
                "a simulated venue must never be reachable where real orders can be placed"
            )
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        self._cash = self.starting_cash
        self._as_of = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

    # -- BrokerProvider ------------------------------------------------------

    @property
    def name(self) -> str:
        return "paper"

    #: Whether this venue accepts fractional quantities.
    #:
    #: False, and not a placeholder: a venue that silently accepts 10.4 shares
    #: makes every sizing result untestable against a real broker that would
    #: have rejected it. The sizer already has `allow_fractional_shares`.
    supports_fractional_shares: bool = field(default=False, init=False)
    supports_extended_hours: bool = field(default=False, init=False)

    def get_account(self) -> PaperAccount:
        return PaperAccount(
            account_id=self.account_id,
            cash=self._cash,
            equity=self.equity,
            # No margin is modelled, so buying power IS cash. Stated rather
            # than inflated: a buying power above cash would be inventing a
            # credit line this project has no evidence about.
            buying_power=self._cash,
            maintenance_margin=Decimal(0),
            as_of=self._as_of,
        )

    def list_positions(self) -> tuple[PaperPosition, ...]:
        out: list[PaperPosition] = []
        for instrument_id, holding in sorted(self._holdings.items()):
            if holding.quantity <= 0:
                continue
            price = self._last_prices.get(instrument_id, holding.average_price)
            out.append(
                PaperPosition(
                    instrument_id=instrument_id,
                    symbol=self.symbols.get(instrument_id, str(instrument_id)),
                    quantity=holding.quantity,
                    average_price=holding.average_price,
                    market_value=holding.quantity * price,
                )
            )
        return tuple(out)

    def submit_order(self, order: object, client_order_id: str) -> PaperOrderStatus:
        """Accept an order. **Fills nothing** -- see the module docstring."""
        if not isinstance(order, OrderRequest):
            raise TypeError(f"PaperBroker requires an OrderRequest, got {type(order).__name__}")
        if order.client_order_id != client_order_id:
            raise ValueError(
                f"client_order_id mismatch: order carries {order.client_order_id!r}, "
                f"submission says {client_order_id!r}. The id is the idempotency key and "
                "two values for it make a retry unsafe"
            )
        existing = self._by_client_id.get(client_order_id)
        if existing is not None:
            resting = self._orders[existing]
            if resting.request != order:
                # The dangerous case: the same idempotency key for a different
                # order. Returning the old status would silently drop the new
                # order; accepting it would make the key meaningless.
                raise ValueError(
                    f"client_order_id {client_order_id!r} was already used for a different "
                    "order; an idempotency key cannot identify two intentions"
                )
            return resting.as_status()

        if order.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(
                f"{order.side} is not supported by this venue: short selling needs borrow "
                "availability this project has no evidence about, and modelling it would be "
                "inventing one"
            )
        if (
            not self.supports_fractional_shares
            and order.quantity != order.quantity.to_integral_value()
        ):
            raise ValueError(
                f"fractional quantity {order.quantity} rejected: this venue does not support "
                "fractional shares, and accepting it would make the result untestable against "
                "a real broker"
            )

        self._sequence += 1
        broker_order_id = f"{self.account_id}-{self._sequence:06d}"
        resting = _Resting(
            order_id=self._sequence,
            broker_order_id=broker_order_id,
            request=order,
            status=OrderStatus.NEW,
            updated_at=self._as_of,
        )
        self._orders[broker_order_id] = resting
        self._by_client_id[client_order_id] = broker_order_id
        return resting.as_status()

    def cancel_order(self, broker_order_id: str) -> PaperOrderStatus:
        resting = self._orders.get(broker_order_id)
        if resting is None:
            raise KeyError(f"unknown order {broker_order_id!r}")
        if resting.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED):
            # Idempotent: cancelling a finished order is a no-op, not an error.
            # A caller retrying a cancel after a timeout must not be punished.
            return resting.as_status()
        resting.status = OrderStatus.CANCELLED
        resting.updated_at = self._as_of
        return resting.as_status()

    def get_order(self, broker_order_id: str) -> PaperOrderStatus:
        resting = self._orders.get(broker_order_id)
        if resting is None:
            raise KeyError(f"unknown order {broker_order_id!r}")
        return resting.as_status()

    def is_market_open(self) -> bool:
        """Always False between sessions, because this venue has no intraday clock.

        A paper venue that claimed the market was open would invite a caller to
        place an order expecting it to fill now. Orders rest until
        :meth:`advance` is called with the next session's bar.
        """
        return False

    # -- the venue's own clock ----------------------------------------------

    @property
    def equity(self) -> Decimal:
        total = self._cash
        for instrument_id, holding in self._holdings.items():
            price = self._last_prices.get(instrument_id, holding.average_price)
            total += holding.quantity * price
        return total

    @property
    def executed_fills(self) -> tuple[Fill, ...]:
        """Every fill this venue has produced, in order.

        Named `executed_fills` rather than `fills` because `fills` is the
        injected FillModel -- a property of that name shadowed the model and
        mypy caught it. Keeping both names distinct matters more than brevity:
        one decides whether an order fills, the other records that it did.
        """
        return tuple(self._fills)

    def advance(self, session_date: dt.date, bars: dict[int, OhlcvBar]) -> tuple[Fill, ...]:
        """Trade one session's bars against the resting orders.

        Returns the fills produced. Orders that could not fill in full are
        **cancelled**, not carried: ``TimeInForce.DAY`` means the day, and a
        remainder surviving to tomorrow is a GTC order nobody asked for.
        """
        self._as_of = dt.datetime.combine(session_date, dt.time(20, 0), tzinfo=dt.UTC)
        produced: list[Fill] = []
        for resting in sorted(self._orders.values(), key=lambda r: r.broker_order_id):
            if resting.status not in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
                continue
            bar = bars.get(resting.request.instrument_id)
            if bar is None:
                # No print for this instrument today. The order neither fills
                # nor expires: a venue does not cancel an order because a
                # symbol did not trade, and treating silence as a cancel would
                # invent an event.
                continue
            self._last_prices[resting.request.instrument_id] = bar.close
            fill = self._attempt(resting, bar)
            if fill is not None:
                produced.append(fill)
        for instrument_id, bar in bars.items():
            self._last_prices[instrument_id] = bar.close
        self._expire_day_orders()
        self._fills.extend(produced)
        return tuple(produced)

    def _attempt(self, resting: _Resting, bar: OhlcvBar) -> Fill | None:
        remaining = resting.remaining
        if remaining <= 0:
            return None
        probe = OrderRequest(
            client_order_id=resting.request.client_order_id,
            portfolio_id=resting.request.portfolio_id,
            instrument_id=resting.request.instrument_id,
            side=resting.request.side,
            order_type=resting.request.order_type,
            quantity=remaining,
            limit_price=resting.request.limit_price,
            stop_price=resting.request.stop_price,
            time_in_force=resting.request.time_in_force,
            intent=resting.request.intent,
            parent_position_id=resting.request.parent_position_id,
        )
        cost = self.costs.estimate(probe, bar)
        simulated = self.fills.simulate(probe, bar, cost)
        if simulated is None:
            return None
        if simulated.quantity <= 0:
            return None
        affordable = self._affordable(probe.side, simulated)
        if affordable <= 0:
            return None
        if affordable < simulated.quantity:
            simulated = Fill(
                order_id=simulated.order_id,
                instrument_id=simulated.instrument_id,
                side=simulated.side,
                quantity=affordable,
                price=simulated.price,
                filled_at=simulated.filled_at,
                commission=simulated.commission * affordable / simulated.quantity,
                slippage=simulated.slippage,
                venue=simulated.venue,
            )
        applied = Fill(
            # The venue's own id, not the fill model's: the model is handed a
            # probe order and has no idea what this venue called it.
            order_id=resting.order_id,
            instrument_id=simulated.instrument_id,
            side=simulated.side,
            quantity=simulated.quantity,
            price=simulated.price,
            filled_at=self._as_of,
            commission=simulated.commission,
            slippage=simulated.slippage,
            venue=self.name,
        )
        self._apply(applied)
        resting.filled_quantity += applied.quantity
        resting.filled_value += applied.price * applied.quantity
        resting.status = (
            OrderStatus.FILLED if resting.remaining <= 0 else OrderStatus.PARTIALLY_FILLED
        )
        resting.updated_at = self._as_of
        return applied

    def _affordable(self, side: OrderSide, fill: Fill) -> Decimal:
        """How much of this fill the book can actually take.

        A buy is capped by cash and a sell by the quantity held. Both are
        refusals a real venue makes, and allowing either to go negative would
        produce a book that cannot exist -- which is worse than a rejected
        order because it reconciles against nothing.
        """
        if side is OrderSide.BUY:
            unit = fill.price + (fill.commission / fill.quantity if fill.quantity else Decimal(0))
            if unit <= 0:
                return Decimal(0)
            affordable = self._cash / unit
            if not self.supports_fractional_shares:
                affordable = affordable.to_integral_value(rounding="ROUND_DOWN")
            return min(fill.quantity, max(affordable, Decimal(0)))
        held = self._holdings.get(fill.instrument_id)
        return min(fill.quantity, held.quantity if held else Decimal(0))

    def _apply(self, fill: Fill) -> None:
        holding = self._holdings.setdefault(
            fill.instrument_id, _Holding(quantity=Decimal(0), cost_basis=Decimal(0))
        )
        if fill.side is OrderSide.BUY:
            holding.quantity += fill.quantity
            holding.cost_basis += fill.gross_value
        else:
            # Cost basis reduces proportionally, so the average price of what
            # remains is unchanged by a partial sale. Reducing it by proceeds
            # instead would make a profitable sale look like it cut the basis
            # of the remainder, and the broker's average_price is exactly what
            # reconciliation compares.
            share = fill.quantity / holding.quantity if holding.quantity > 0 else Decimal(0)
            holding.cost_basis -= holding.cost_basis * share
            holding.quantity -= fill.quantity
        self._cash += fill.net_value
        if holding.quantity <= 0:
            self._holdings.pop(fill.instrument_id, None)

    def _expire_day_orders(self) -> None:
        for resting in self._orders.values():
            if resting.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
                resting.status = OrderStatus.CANCELLED
                resting.updated_at = self._as_of
