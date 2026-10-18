"""Placing orders, and refusing to trade against a fictional portfolio.

Phase 11's execution service. It sits between the portfolio cycle and a
:class:`~tradeit.data.provider.BrokerProvider`, and it does three things that
have to be done together or not at all: place orders, reconcile our view
against the broker's, and write both into the journal.

**The broker is authoritative and we are a model of it.** That is the whole
design. When the two disagree the platform halts for that portfolio rather than
continuing, because every decision after a disagreement is computed from a book
that does not exist. A system that logs a discrepancy and keeps trading has
chosen the worst of both: it knows it is wrong and acts anyway.

**A halt is sticky and has to be cleared deliberately.** :meth:`place` refuses
while a portfolio is halted. Clearing requires :meth:`resume`, which exists so
that clearing is an event someone performs rather than a side effect of the
next reconcile happening to agree — a transient disagreement that resolves
itself is exactly the case where silence is most dangerous.

Tolerance, and why there is one
-------------------------------

Quantities compare exactly: a share count is an integer and a disagreement
about it is real. **Cash and average price compare against a tolerance**,
because they are decimal quantities that accumulate rounding through commission
splits and partial fills, and halting on a half-cent would mean halting every
day. The tolerance is a constructor argument with no default, so no caller gets
one by accident and every run records what it used.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from tradeit.execution.base import Discrepancy, DiscrepancyKind, OrderRequest
from tradeit.execution.journal import JournalEntryKind, record_entry
from tradeit.portfolio.base import PortfolioState
from tradeit.risk.controls import ControlRefusal, PreTradeControls

__all__ = ["BrokerExecutionService", "OrderRefusedByControl", "TradingHalted"]


class TradingHalted(RuntimeError):
    """An order was attempted for a portfolio whose state does not reconcile."""


class OrderRefusedByControl(RuntimeError):
    """A pre-trade control refused the order before it reached a venue.

    Separate from :class:`TradingHalted` because the two mean different things
    and need different responses: a halt says our book is wrong, a control
    refusal says the order is wrong (or that trading is stopped). Collapsing
    them would send an operator to reconcile a book that is fine.
    """

    def __init__(self, refusals: tuple[ControlRefusal, ...]) -> None:
        self.refusals = refusals
        super().__init__("; ".join(refusal.describe() for refusal in refusals))


@dataclass
class BrokerExecutionService:
    """Places orders through a broker and reconciles against it.

    ``ours`` is a callable rather than a stored portfolio: the service must
    reconcile against our state *as it is now*, and a snapshot captured at
    construction would reconcile the broker against history and agree with
    itself forever.
    """

    broker: object
    session: Session
    #: How far cash and average prices may differ before it is a discrepancy.
    #: No default: a tolerance nobody chose is a tolerance nobody can defend.
    price_tolerance: Decimal
    cash_tolerance: Decimal
    ours: object = None
    #: The controls a strategy cannot reach, checked before every submission.
    #:
    #: Optional, and `configured` reports what is installed — because a run
    #: with no controls must be visible as such rather than letting a reader
    #: assume protection that was never wired. Checked HERE, at the boundary
    #: where orders leave, rather than at sizing: a kill switch that only
    #: stopped sizing would let a resting order fill after it was thrown.
    controls: PreTradeControls | None = None
    _halted: dict[int, tuple[Discrepancy, ...]] = field(default_factory=dict)
    _order_ids: dict[int, str] = field(default_factory=dict)
    _sequence: int = 0

    def is_halted(self, portfolio_id: int) -> bool:
        return portfolio_id in self._halted

    def halt_reason(self, portfolio_id: int) -> tuple[Discrepancy, ...]:
        return self._halted.get(portfolio_id, ())

    def place(
        self,
        order: OrderRequest,
        *,
        session_date: dt.date | None = None,
        as_of: dt.datetime | None = None,
        last_seen: Mapping[int, dt.datetime] | None = None,
    ) -> int:
        """Submit an order, unless this portfolio is halted or a control refuses.

        The halt is checked first: a book known to be wrong makes every
        decision after it meaningless, so there is nothing for a control to
        usefully say about an order that should not exist.

        ``session_date`` and ``as_of`` are required **when controls are
        installed** and are not defaulted to today, for the reason `reconcile`
        gives: a replay or a session crossing midnight UTC would otherwise be
        judged against the wrong day, and a stale-data guard reading wall-clock
        time could not be replayed at all.
        """
        if self.is_halted(order.portfolio_id):
            raise TradingHalted(
                f"portfolio {order.portfolio_id} is halted on "
                f"{len(self._halted[order.portfolio_id])} unresolved discrepancies; "
                "resolve them and call resume() rather than placing orders against a "
                "portfolio whose state is known to be wrong"
            )
        if self.controls is not None:
            if session_date is None or as_of is None:
                raise ValueError(
                    "controls are installed, so place() needs session_date and as_of; "
                    "defaulting them to now would make a stale-data guard unreplayable "
                    "and a duplicate guard wrong across midnight"
                )
            refusals = self.controls.check(
                order, session_date=session_date, as_of=as_of, last_seen=last_seen
            )
            if refusals:
                record_entry(
                    self.session,
                    kind=JournalEntryKind.DECLINED,
                    session_date=session_date,
                    portfolio_id=order.portfolio_id,
                    instrument_id=order.instrument_id,
                    summary=f"order refused by {len(refusals)} pre-trade control(s)",
                    rationale="; ".join(refusal.describe() for refusal in refusals),
                )
                raise OrderRefusedByControl(refusals)
        status = self.broker.submit_order(order, order.client_order_id)  # type: ignore[attr-defined]
        if self.controls is not None and session_date is not None:
            # Recorded only AFTER a successful submission. An order the venue
            # refused was not sent, and recording it would block the
            # legitimate retry.
            self.controls.record(order, session_date)
        self._sequence += 1
        self._order_ids[self._sequence] = status.broker_order_id
        return self._sequence

    def cancel(self, order_id: int) -> None:
        broker_order_id = self._order_ids.get(order_id)
        if broker_order_id is None:
            raise KeyError(f"unknown order {order_id}")
        self.broker.cancel_order(broker_order_id)  # type: ignore[attr-defined]

    def resume(
        self, portfolio_id: int, *, session_date: dt.date, resolved_by: str, note: str
    ) -> None:
        """Clear a halt, as an event with an author.

        Requires both arguments. A halt cleared by nobody, for no stated
        reason, is indistinguishable from a halt that never happened — and the
        journal is the only place that distinction survives.
        """
        if not resolved_by.strip() or not note.strip():
            raise ValueError("resuming requires who resolved it and what they found")
        if portfolio_id not in self._halted:
            return
        count = len(self._halted.pop(portfolio_id))
        record_entry(
            self.session,
            kind=JournalEntryKind.NOTE,
            session_date=session_date,
            portfolio_id=portfolio_id,
            summary=f"trading resumed after {count} discrepancies",
            rationale=note,
            author=resolved_by,
        )

    def reconcile(self, portfolio_id: int, *, session_date: dt.date) -> tuple[Discrepancy, ...]:
        """Compare our state against the broker's, and halt on any difference.

        ``session_date`` is required and is **not** defaulted to today. A
        journal entry's session date is the trading session it belongs to,
        which the caller knows and wall-clock does not: a reconcile run after
        midnight UTC, or replayed over history, would otherwise file itself
        under the wrong day and quietly corrupt the one record that is
        supposed to explain what happened when.
        """
        ours = self._our_state(portfolio_id)
        account = self.broker.get_account()  # type: ignore[attr-defined]
        theirs = {
            position.instrument_id: position
            for position in self.broker.list_positions()  # type: ignore[attr-defined]
            if position.instrument_id is not None
        }
        found = list(
            _compare(
                ours,
                theirs,
                account_cash=account.cash,
                price_tolerance=self.price_tolerance,
                cash_tolerance=self.cash_tolerance,
            )
        )
        if found:
            self._halted[portfolio_id] = tuple(found)
            record_entry(
                self.session,
                kind=JournalEntryKind.DISCREPANCY,
                session_date=session_date,
                portfolio_id=portfolio_id,
                summary=f"reconciliation found {len(found)} discrepancies; trading halted",
                rationale="; ".join(item.describe() for item in found)[:4000],
                context={
                    "discrepancies": [
                        {
                            "kind": str(item.kind),
                            "instrument_id": item.instrument_id,
                            "ours": item.ours,
                            "theirs": item.theirs,
                            "note": item.note,
                        }
                        for item in found
                    ]
                },
            )
        return tuple(found)

    def _our_state(self, portfolio_id: int) -> PortfolioState:
        if self.ours is None:
            raise ValueError(
                "no source of our own state was given, so there is nothing to reconcile "
                "the broker against. A reconcile that compares the broker to itself "
                "always agrees, which is the failure this refuses to perform"
            )
        state = self.ours(portfolio_id)  # type: ignore[operator]
        if not isinstance(state, PortfolioState):
            raise TypeError(f"expected a PortfolioState, got {type(state).__name__}")
        return state


def _compare(
    ours: PortfolioState,
    theirs: Mapping[int, object],
    *,
    account_cash: Decimal,
    price_tolerance: Decimal,
    cash_tolerance: Decimal,
) -> Iterable[Discrepancy]:
    """Every way the two views can disagree, each reported once."""
    mine = {position.instrument_id: position for position in ours.open_positions}

    for instrument_id in sorted(set(mine) | set(theirs)):
        here = mine.get(instrument_id)
        there = theirs.get(instrument_id)
        if there is None:
            yield Discrepancy(
                kind=DiscrepancyKind.MISSING_POSITION,
                instrument_id=instrument_id,
                ours=here.quantity if here else None,
                theirs=None,
                note="we hold it and the broker does not report it",
            )
            continue
        their_quantity: Decimal = there.quantity  # type: ignore[attr-defined]
        if here is None:
            yield Discrepancy(
                kind=DiscrepancyKind.UNKNOWN_POSITION,
                instrument_id=instrument_id,
                ours=None,
                theirs=their_quantity,
                note="the broker reports a position we have no record of",
            )
            continue
        if here.quantity != their_quantity:
            # Exact: a share count is an integer and a tolerance on it would
            # hide a missed partial fill, which is the commonest real cause.
            yield Discrepancy(
                kind=DiscrepancyKind.QUANTITY,
                instrument_id=instrument_id,
                ours=here.quantity,
                theirs=their_quantity,
            )
            continue
        their_price: Decimal = there.average_price  # type: ignore[attr-defined]
        if abs(here.average_entry_price - their_price) > price_tolerance:
            yield Discrepancy(
                kind=DiscrepancyKind.AVERAGE_PRICE,
                instrument_id=instrument_id,
                ours=here.average_entry_price,
                theirs=their_price,
                note="quantities agree, cost basis does not",
            )

    if abs(ours.cash - account_cash) > cash_tolerance:
        yield Discrepancy(
            kind=DiscrepancyKind.CASH,
            instrument_id=None,
            ours=ours.cash,
            theirs=account_cash,
        )
