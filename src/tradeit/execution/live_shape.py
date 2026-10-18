"""Running the decision loop the way live would run it.

The backtester owns everything: the clock, the book, the fills. That is correct
for a backtest and it is the reason a backtest cannot tell you whether the live
path works, because in live the **broker** owns the book and we own a model of
it that can be wrong.

This module runs the same :class:`~tradeit.portfolio.cycle.PortfolioCycle`
against a broker, with the parts live has and the backtester does not:

* **Our book is an independent model**, updated only from fills the broker
  reports. It is *not* read back from the broker — if it were, reconciliation
  would compare the broker to itself and agree forever, which is the failure
  :class:`~tradeit.execution.service.BrokerExecutionService` refuses to
  perform.
* **Reconciliation runs every session**, and a disagreement halts.
* **Orders go out and may not come back.** An order the venue cancels is simply
  not filled, and our book must not assume otherwise.

What this is for
----------------

Phase 11's components were exercised by replaying the backtester's own orders
through the venue, which proved the two produce identical fills for identical
orders. That is a necessary check and it is not this one: a replay cannot find
a defect in the *loop* — in what we believe we hold, in what we therefore
decide next, in whether a drift is noticed. Only a run where the venue is the
authority can.

Deliberate limits
-----------------

**One lot per instrument.** The backtester's `_RunState` tracks a lot with its
own stop ladder state, entry extremes and sessions held. This keeps the same
shape, so a position added to is averaged rather than held as two lots. A real
broker reports one position per symbol, so this matches what reconciliation can
actually see.

**No intraday clock.** Sessions advance one bar at a time, as the paper venue
does. Nothing here claims to model a trading day.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from tradeit.core.enums import OrderSide, OrderType, PositionStatus, Side
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import Fill, OrderRequest
from tradeit.execution.service import BrokerExecutionService
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.stops import StopContext

__all__ = ["LiveShapedSession", "Lot", "SessionOutcome"]


@dataclass
class Lot:
    """Our model of one holding, and the ladder state the broker does not carry.

    A broker reports quantity and average price. It does not report the high
    since entry, how many sessions we have held, or whether a partial profit
    was taken — those are ours, and losing them would reset the stop ladder on
    every restart. Kept here so what is ours and what is the broker's stay
    visibly separate.
    """

    instrument_id: int
    quantity: Decimal
    entry_price: Decimal
    initial_stop: Decimal
    current_stop: Decimal
    highest: Decimal
    opened_on: dt.date
    sessions_held: int = 0
    partial_profit_taken: bool = False


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    """What one session did, including when it did nothing."""

    session_date: dt.date
    fills: tuple[Fill, ...]
    orders_placed: int
    discrepancies: int
    halted: bool
    equity: Decimal
    positions: int


@dataclass
class LiveShapedSession:
    """Drives the cycle against a broker, holding our own view of the book."""

    cycle: PortfolioCycle
    service: BrokerExecutionService
    broker: object
    portfolio_id: int = 1
    initial_capital: Decimal = Decimal(100_000)
    _cash: Decimal = field(init=False)
    _lots: dict[int, Lot] = field(init=False, default_factory=dict)
    _last_prices: dict[int, Decimal] = field(init=False, default_factory=dict)
    _as_of: dt.datetime = field(init=False)
    _orders: int = field(init=False, default=0)
    #: The stop each entry order was decided with, keyed by its order id.
    #:
    #: Remembered against the ORDER rather than the fill, because the fill
    #: price is not known when the decision is made and the stop was part of
    #: that decision. A stop derived from the fill price instead would quietly
    #: move with slippage.
    _pending_stops: dict[str, Decimal] = field(init=False, default_factory=dict)
    #: Where a stop goes when the decision's own stop cannot be used.
    #:
    #: A fraction of the fill price. Only reached when an entry gapped
    #: through its intended stop, or when no stop was recorded for the order.
    _fallback_stop: Decimal = field(init=False, default=Decimal("0.92"))
    #: Entries whose fill gapped through the stop they were decided with.
    #:
    #: The backtester refuses these outright. A live system cannot, because
    #: the shares are already bought -- so they are counted and reported
    #: rather than hidden, and a large number means the stop is too tight
    #: for the universe's overnight behaviour.
    premise_failed: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self._cash = self.initial_capital
        self._as_of = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
        # The service reconciles OUR state, which it reads through this
        # callable. Wired here rather than by the caller so the two cannot be
        # pointed at different books by accident.
        self.service.ours = self.state

    # -- our model of the book ----------------------------------------------

    @property
    def equity(self) -> Decimal:
        total = self._cash
        for lot in self._lots.values():
            total += lot.quantity * self._last_prices.get(lot.instrument_id, lot.entry_price)
        return total

    def state(self, portfolio_id: int | None = None) -> PortfolioState:
        """Our view, as the risk and allocation layers expect it."""
        del portfolio_id
        return PortfolioState(
            portfolio_id=self.portfolio_id,
            as_of=self._as_of,
            cash=self._cash,
            equity=self.equity,
            positions=tuple(
                PositionState(
                    position_id=lot.instrument_id,
                    portfolio_id=self.portfolio_id,
                    instrument_id=lot.instrument_id,
                    side=Side.LONG,
                    status=PositionStatus.OPEN,
                    quantity=lot.quantity,
                    average_entry_price=lot.entry_price,
                    stop_price=lot.current_stop,
                    opened_on=lot.opened_on,
                    initial_stop_price=lot.initial_stop,
                )
                for lot in sorted(self._lots.values(), key=lambda item: item.instrument_id)
            ),
            last_prices=dict(self._last_prices),
        )

    # -- one session ---------------------------------------------------------

    def run_session(
        self,
        session_date: dt.date,
        bars: Mapping[int, OhlcvBar],
        candidates: Sequence[EntryCandidate],
        splits: Mapping[int, Decimal] | None = None,
    ) -> SessionOutcome:
        """Advance one session: splits, fills, our book, reconcile, decide.

        The order is the live order and it is not interchangeable. Splits
        restate before anything is priced; yesterday's orders meet today's
        prices before we decide; reconciliation happens **before** deciding,
        because deciding on a book known to be wrong is the thing the halt
        exists to prevent.
        """
        self._as_of = dt.datetime.combine(session_date, dt.time(20, 0), tzinfo=dt.UTC)

        if splits:
            self.broker.apply_splits(splits)  # type: ignore[attr-defined]
            self._apply_splits(splits)

        fills = self.broker.advance(session_date, dict(bars))  # type: ignore[attr-defined]
        for fill in fills:
            self._apply_fill(fill, session_date)

        for instrument_id, bar in bars.items():
            self._last_prices[instrument_id] = bar.close
        for lot in self._lots.values():
            lot.sessions_held += 1
            price = self._last_prices.get(lot.instrument_id)
            if price is not None and price > lot.highest:
                lot.highest = price

        discrepancies = self.service.reconcile(self.portfolio_id, session_date=session_date)
        placed = 0
        if not discrepancies:
            placed = self._decide(session_date, bars, candidates)

        return SessionOutcome(
            session_date=session_date,
            fills=tuple(fills),
            orders_placed=placed,
            discrepancies=len(discrepancies),
            halted=self.service.is_halted(self.portfolio_id),
            equity=self.equity,
            positions=len(self._lots),
        )

    def _decide(
        self,
        session_date: dt.date,
        bars: Mapping[int, OhlcvBar],
        candidates: Sequence[EntryCandidate],
    ) -> int:
        state = self.state()
        contexts: dict[int, StopContext] = {}
        for position in state.open_positions:
            lot = self._lots[position.instrument_id]
            last = self._last_prices.get(position.instrument_id)
            if last is None:
                # Nothing has ever printed for a holding we think we have.
                # Refusing to decide is right: the engine does the same, and
                # guessing a price here would move a stop on an invention.
                return 0
            contexts[position.instrument_id] = StopContext(
                last_price=last,
                high_since_entry=lot.highest,
                sessions_held=lot.sessions_held,
                partial_profit_taken=lot.partial_profit_taken,
            )

        eligible = [
            candidate
            for candidate in candidates
            if candidate.instrument_id in bars and candidate.instrument_id not in self._lots
        ]
        plan = self.cycle.plan(state, eligible, contexts)

        for update in plan.stop_updates:
            held = self._lots.get(update.instrument_id)
            if held is not None and update.stop_price > held.current_stop:
                held.current_stop = update.stop_price

        placed = 0
        for signal in plan.exits:
            holding = self._lots.get(signal.instrument_id)
            if holding is None or signal.instrument_id not in bars:
                continue
            quantity = holding.quantity * signal.fraction
            if quantity <= 0:
                continue
            quantity = quantity.to_integral_value(rounding="ROUND_DOWN")
            if quantity <= 0:
                continue
            placed += self._submit(session_date, signal.instrument_id, OrderSide.SELL, quantity)
        for entry in plan.entries:
            if entry.quantity <= 0:
                continue
            placed += self._submit(
                session_date,
                entry.candidate.instrument_id,
                OrderSide.BUY,
                entry.quantity.to_integral_value(rounding="ROUND_DOWN"),
                candidate=entry.candidate,
            )
        return placed

    def _submit(
        self,
        session_date: dt.date,
        instrument_id: int,
        side: OrderSide,
        quantity: Decimal,
        candidate: EntryCandidate | None = None,
    ) -> int:
        if quantity <= 0:
            return 0
        prefix = "e" if side is OrderSide.BUY else "x"
        order = OrderRequest(
            client_order_id=f"{prefix}-{instrument_id}-{session_date.isoformat()}",
            portfolio_id=self.portfolio_id,
            instrument_id=instrument_id,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            intent=f"{prefix} from live-shaped session",
        )
        self.service.place(order)
        self._orders += 1
        if candidate is not None:
            # The intended stop is remembered against the order, not the fill,
            # because the fill price is not known yet and the stop was part of
            # the decision.
            self._pending_stops[order.client_order_id] = candidate.stop_price
        return 1

    def _apply_fill(self, fill: Fill, session_date: dt.date) -> None:
        lot = self._lots.get(fill.instrument_id)
        if fill.side is OrderSide.BUY:
            if lot is None:
                intended = next(
                    (
                        value
                        for key, value in self._pending_stops.items()
                        if key.startswith(f"e-{fill.instrument_id}-")
                    ),
                    None,
                )
                stop = intended if intended is not None else fill.price * self._fallback_stop
                if stop >= fill.price:
                    # **The backtester's option does not exist here.** When a
                    # fill gaps through the intended stop, `engine._settle`
                    # declines to open the position at all and counts it in
                    # `abandoned_entries` -- the premise "enter here, risk down
                    # to there" no longer holds.
                    #
                    # In live the shares are already bought. There is nothing to
                    # decline: refusing to record the lot would leave us holding
                    # a position our own model does not know about, which is the
                    # precise drift reconciliation exists to catch, caused by us.
                    #
                    # So the fill is accepted and the stop is placed the
                    # configured distance below it, which keeps R defined. The
                    # alternative -- exiting immediately -- is trading logic and
                    # needs a scoped proposition, so it is not taken here.
                    self.premise_failed += 1
                    stop = fill.price * self._fallback_stop
                self._lots[fill.instrument_id] = Lot(
                    instrument_id=fill.instrument_id,
                    quantity=fill.quantity,
                    entry_price=fill.price,
                    initial_stop=stop,
                    current_stop=stop,
                    highest=fill.price,
                    opened_on=session_date,
                )
            else:
                total = lot.quantity + fill.quantity
                lot.entry_price = (
                    lot.entry_price * lot.quantity + fill.price * fill.quantity
                ) / total
                lot.quantity = total
        elif lot is not None:
            lot.quantity -= fill.quantity
            if lot.quantity <= 0:
                self._lots.pop(fill.instrument_id, None)
        self._cash += fill.net_value

    def _apply_splits(self, ratios: Mapping[int, Decimal]) -> None:
        for instrument_id, ratio in ratios.items():
            if ratio <= 0:
                continue
            lot = self._lots.get(instrument_id)
            if lot is not None:
                lot.quantity *= ratio
                lot.entry_price /= ratio
                lot.initial_stop /= ratio
                lot.current_stop /= ratio
                lot.highest /= ratio
            if instrument_id in self._last_prices:
                self._last_prices[instrument_id] /= ratio
