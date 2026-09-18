"""The event-driven backtester: a clock, a simulated venue, and nothing else new.

``BacktestEngine`` says the commitment plainly and this module keeps it: **the
backtester does not reimplement the strategy.** It advances a clock through
history and calls the same sizer, risk engine, allocation ranker and stop ladder
that would run live, with the simulated broker substituted at the edge. A
backtester carrying its own copy of the entry logic tests a program that
resembles the live system, and the two drift apart in silence.

The look-ahead rule is structural, not a convention
---------------------------------------------------

**A decision taken on session T executes against session T+1.** That is not
enforced by a comment or a review checklist — the engine physically cannot do
otherwise, because planning produces ``pending`` orders and pending orders are
only ever filled at the top of the *next* iteration. There is no code path from
a plan to a fill within one session, so the most common way to fabricate
backtest returns is unavailable rather than discouraged.

The consequence is visible and correct: a position is sized on the price the
strategy could see (T's close) and filled at the price it would have got (T+1's
open). The gap between those two is real and the backtest pays it.

Exits are pessimistic, deliberately
-----------------------------------

The stop ladder evaluates on T's close, and a breached stop exits as a market
order on T+1's open. A live system would have a resting stop that filled
intraday on T, usually at a better price. **The difference is left in the
pessimistic direction on purpose**, and the alternative was considered and
rejected for now: modelling resting stops means treating an order as live
*during* a bar, which is a real feature and a real source of optimism, and it
should arrive with its own tests rather than as a side effect of this one.

Every exit is its own trade
---------------------------

A position scaled out in two pieces produces two ``BacktestTrade`` rows, not
one averaged one. ``ExitReason`` already states why: a scaled-out position can
leave for several reasons, and averaging them away loses the lesson.

What it does not do
-------------------

It does not decide *what* to buy. Candidates arrive from a
:class:`SessionData` source, which in production is the same screening and
scoring stack the live job uses and in tests is whatever the test needs. The
engine's job is the loop, the venue and the measurement.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradeit.backtesting.base import (
    BacktestResult,
    BacktestSpec,
    BacktestTrade,
    PerformanceAnalyzer,
    PerformanceMetrics,
)
from tradeit.core.enums import (
    BacktestStatus,
    ExitReason,
    OrderSide,
    OrderType,
    PositionStatus,
    Side,
)
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import CostModel, Fill, FillModel, OrderRequest
from tradeit.portfolio.base import PortfolioState, PositionState
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.stops import ExitSignal, StopContext
from tradeit.reproducibility.versioning import RunManifest

__all__ = ["EventDrivenEngine", "SessionData"]


@runtime_checkable
class SessionData(Protocol):
    """Everything the engine needs to know about one session.

    ``candidates`` is the strategy's output, not the engine's: whatever
    produced it must already have respected point-in-time discipline, because
    the engine cannot tell a legitimately-known feature from a leaked one.
    """

    def sessions(self, start: dt.date, end: dt.date) -> Sequence[dt.date]: ...

    def bars(self, session_date: dt.date) -> Mapping[int, OhlcvBar]: ...

    def candidates(self, session_date: dt.date) -> Sequence[EntryCandidate]: ...

    def splits_on(self, session_date: dt.date) -> Mapping[int, Decimal]:
        """Split ratios with this ex-date. ``{}`` when there are none.

        Required rather than optional, because a source that silently had no
        way to report splits would produce a backtest that shows a 50% loss
        every time a holding splits two-for-one -- and it would look like a
        strategy result rather than a missing feature.
        """
        ...


@dataclass(slots=True)
class _OpenLot:
    """Bookkeeping a ``PositionState`` does not carry but a trade record needs."""

    instrument_id: int
    entry_date: dt.date
    entry_price: Decimal
    entry_costs: Decimal
    quantity: Decimal
    initial_stop: Decimal
    #: Where the stop sits now. Starts at the initial stop and only ever rises,
    #: as the ladder moves it to breakeven and trails it. Carried separately
    #: because ``initial_stop`` must stay fixed -- it defines R.
    current_stop: Decimal
    highest: Decimal
    lowest: Decimal
    sessions_held: int = 0
    sessions_since_bar: int = 0
    partial_profit_taken: bool = False
    score_at_entry: float | None = None
    sector: str | None = None

    def observe(self, bar: OhlcvBar) -> None:
        self.highest = max(self.highest, bar.high)
        self.lowest = min(self.lowest, bar.low)
        self.sessions_held += 1
        self.sessions_since_bar = 0

    def observe_silence(self) -> None:
        """A session the security did not print. Still held, not marked anew."""
        self.sessions_held += 1
        self.sessions_since_bar += 1


@dataclass(slots=True)
class _Pending:
    """An order decided on one session, to be filled on the next."""

    order: OrderRequest
    candidate: EntryCandidate | None
    exit_reason: ExitReason | None
    reference_bar: OhlcvBar


@dataclass(frozen=True, slots=True)
class EventDrivenEngine:
    """Advances a clock and calls the live components at every step."""

    cycle: PortfolioCycle
    costs: CostModel
    fills: FillModel
    data: SessionData
    analyzer: PerformanceAnalyzer
    #: Sessions of silence after which a holding is treated as delisted.
    #:
    #: Required. A universe containing companies that failed -- the only kind
    #: worth measuring -- holds positions whose prices simply stop, and a
    #: backtester with no answer for that carries them to the end of the run,
    #: occupying a position slot and a share of portfolio heat no real holder
    #: still had.
    delisting_after_sessions: int
    #: Fraction of the last quoted price a delisted holding recovers.
    #:
    #: Required, and the most consequential assumption in a survivorship-honest
    #: backtest. 1.0 says the position was sold at its last print, which is
    #: optimistic -- a company whose quotes stop because it failed did not
    #: usually offer an exit there. 0.0 says the holding went to nothing. Both
    #: an acquisition and a bankruptcy stop the prices, and this system cannot
    #: yet tell them apart, so the caller states the assumption rather than
    #: inheriting one that would quietly decide the result.
    delisting_recovery: Decimal
    #: The reproducible context this engine runs in.
    #:
    #: **Taken, never constructed.** A manifest names the exact strategy-config
    #: artifact and data snapshot a run used, and the engine knows neither. An
    #: engine that minted its own would produce a manifest that always
    #: validates and never reproduces, which is fabricated provenance wearing
    #: the shape of the real thing.
    manifest: RunManifest
    #: Per-instrument recovery, overriding :attr:`delisting_recovery` for the
    #: holdings it names.
    #:
    #: Exists because the single number above had to cover two opposite fates.
    #: Classified from EDGAR (``SIGNAL_SCOREBOARD.md`` §16), 78.8% of the
    #: securities whose prices stopped in 2000-2009 were bought out and 4.0%
    #: went bankrupt -- so a bracket of 0.0 to 1.0 applied to all of them spent
    #: most of its width on companies whose fate the filings state. A caller
    #: with evidence names each holding; the scalar remains the stated
    #: assumption for everything the evidence does not reach. Empty by default,
    #: which is exactly the previous behaviour.
    delisting_recovery_by_instrument: Mapping[int, Decimal] = field(default_factory=dict)
    name: str = "event_driven"

    def __post_init__(self) -> None:
        for instrument_id, recovery in self.delisting_recovery_by_instrument.items():
            if not Decimal(0) <= recovery <= Decimal(1):
                raise ValueError(
                    f"delisting recovery for instrument {instrument_id} is {recovery}; it is "
                    "a fraction of the last quoted price and must lie in [0, 1]"
                )

    def recovery_for(self, instrument_id: int) -> Decimal:
        """The fraction of its last price a delisted holding recovers."""
        return self.delisting_recovery_by_instrument.get(instrument_id, self.delisting_recovery)

    def run(self, spec: BacktestSpec) -> BacktestResult:
        state = _RunState(spec)
        sessions = list(self.data.sessions(spec.effective_start, spec.end))
        if len(sessions) < 2:
            raise ValueError(
                f"{len(sessions)} sessions between {spec.effective_start} and {spec.end}; "
                "a backtest needs at least two, because decisions execute on the next one"
            )

        for session_date in sessions:
            bars = self.data.bars(session_date)
            # 0. The share count changes before anything is priced against it.
            self._apply_splits(state, session_date)
            # 1. Yesterday's decisions meet today's prices. Always first.
            self._settle(state, bars, session_date)
            # 2. Mark the book and record the day.
            self._mark(state, bars, session_date)
            # 2a. Retire anything that has stopped printing.
            self._expire_delisted(state, session_date)
            # 3. Decide, using only what today's close revealed.
            self._decide(state, bars, session_date)

        self._close_out(state, sessions[-1])
        return self._result(spec, state)

    # -- the steps ----------------------------------------------------------

    def _apply_splits(self, state: _RunState, session_date: dt.date) -> None:
        """Restate holdings and resting orders for splits with this ex-date.

        First thing in the session, because the session's own prints are
        already post-split: marking a pre-split share count against a
        post-split price reports a 50% loss on a two-for-one that cost the
        holder nothing.

        Entry price, initial stop and the running extremes divide by the same
        ratio, so R multiples and the stop ladder mean after the split exactly
        what they meant before it. A pending order is restated too rather than
        cancelled -- a decision taken yesterday was a decision about a
        percentage of the account, and the split did not change it.
        """
        ratios = self.data.splits_on(session_date)
        for instrument_id, ratio in ratios.items():
            if ratio <= 0:
                continue
            lot = state.lots.get(instrument_id)
            if lot is not None:
                lot.quantity *= ratio
                lot.entry_price /= ratio
                lot.initial_stop /= ratio
                lot.current_stop /= ratio
                lot.highest /= ratio
                lot.lowest /= ratio
            for item in state.pending:
                if item.order.instrument_id == instrument_id:
                    item.order = dataclasses.replace(
                        item.order, quantity=item.order.quantity * ratio
                    )
            if instrument_id in state.last_prices:
                state.last_prices[instrument_id] /= ratio

    # -- the three steps ----------------------------------------------------

    def _settle(
        self, state: _RunState, bars: Mapping[int, OhlcvBar], session_date: dt.date
    ) -> None:
        pending, state.pending = state.pending, []
        for item in pending:
            bar = bars.get(item.order.instrument_id)
            if bar is None:
                # No print today. The order is not silently cancelled -- it is
                # carried, because a halted session is not a decision.
                state.pending.append(item)
                continue
            estimate = self.costs.estimate(item.order, item.reference_bar)
            fill = self.fills.simulate(item.order, bar, estimate)
            if fill is None:
                continue
            if item.order.side is OrderSide.BUY:
                if item.candidate is not None and fill.price <= item.candidate.stop_price:
                    # The market gapped through the intended stop overnight, so
                    # the fill is at or below it. The premise of the trade --
                    # enter here, risk down to there -- no longer holds, and a
                    # position whose stop sits above its entry has no defined
                    # risk and no meaningful R. Refused rather than opened and
                    # instantly stopped out, which would book a loss on a trade
                    # nobody would have taken.
                    state.abandoned_entries += 1
                    continue
                state.open_lot(fill, item.candidate, session_date)
            else:
                state.close_lot(fill, item.exit_reason or ExitReason.DISCRETIONARY)

    def _mark(self, state: _RunState, bars: Mapping[int, OhlcvBar], session_date: dt.date) -> None:
        for lot in state.lots.values():
            bar = bars.get(lot.instrument_id)
            if bar is not None:
                lot.observe(bar)
            else:
                lot.observe_silence()
        state.mark(bars, session_date)

    def _expire_delisted(self, state: _RunState, session_date: dt.date) -> None:
        """Close holdings that have gone quiet for longer than the threshold.

        Booked on the session the silence is recognised rather than on the last
        print, because the holder did not know it was the last one at the time.
        """
        for instrument_id in list(state.lots):
            lot = state.lots[instrument_id]
            if lot.sessions_since_bar < self.delisting_after_sessions:
                continue
            last = state.last_prices.get(instrument_id, lot.entry_price)
            state.close_at(
                instrument_id=instrument_id,
                price=last * self.recovery_for(instrument_id),
                exit_date=session_date,
                reason=ExitReason.DELISTED_EXIT,
            )

    def _decide(
        self, state: _RunState, bars: Mapping[int, OhlcvBar], session_date: dt.date
    ) -> None:
        portfolio = state.portfolio()
        contexts = {
            position.instrument_id: self._context(state, position, bars)
            for position in portfolio.open_positions
        }
        if any(context is None for context in contexts.values()):
            # A held name with no print today cannot have its stop managed, and
            # the cycle refuses that case rather than guessing. Skip deciding
            # rather than lying about the context.
            return
        candidates = [
            candidate
            for candidate in self.data.candidates(session_date)
            if candidate.instrument_id in bars
        ]
        plan = self.cycle.plan(
            portfolio,
            candidates,
            {k: v for k, v in contexts.items() if v is not None},
        )
        # The ladder's stop moves take effect from here: breakeven and trailing
        # were computed at this close and govern the next session's exit test.
        # Until 2026-09-18 these updates were discarded, so every backtest ran
        # with the initial stop only -- no breakeven, no trailing -- while the
        # platform's declared ladder included both. Found by the stop-ladder
        # registration's pilot, which recorded 66 partial profits and zero
        # trailing exits; see SIGNAL_SCOREBOARD §38.
        for update in plan.stop_updates:
            lot = state.lots.get(update.instrument_id)
            if lot is not None and update.stop_price > lot.current_stop:
                lot.current_stop = update.stop_price
        for signal in plan.exits:
            if signal.instrument_id not in bars:
                # The stop was evaluated against a stale mark, and there is no
                # market to sell into. The position stays; if the silence
                # continues it is retired by the delisting rule instead. A
                # backtest that filled here would be selling at a price nobody
                # was quoting, which is the most flattering fill there is.
                continue
            state.pending.append(self._exit_order(state, signal, bars))
        for entry in plan.accepted:
            state.pending.append(self._entry_order(entry.candidate, entry.quantity, bars))

    # -- order construction -------------------------------------------------

    @staticmethod
    def _context(
        state: _RunState, position: PositionState, bars: Mapping[int, OhlcvBar]
    ) -> StopContext | None:
        """The stop ladder's view of one holding.

        A security that did not print today is carried at its last known price
        rather than skipped. That is not a guess -- it is the last thing anybody
        knew, and being unchanged from yesterday it cannot trigger a stop that
        yesterday did not. Skipping would stall the whole session's decisions
        over one quiet name, which in a universe containing failed companies is
        most sessions.
        """
        lot = state.lots.get(position.instrument_id)
        if lot is None:
            return None
        bar = bars.get(position.instrument_id)
        last = bar.close if bar is not None else state.last_prices.get(position.instrument_id)
        if last is None:
            return None
        return StopContext(
            last_price=last,
            high_since_entry=lot.highest,
            sessions_held=lot.sessions_held,
            partial_profit_taken=lot.partial_profit_taken,
            atr=None,
        )

    def _entry_order(
        self, candidate: EntryCandidate, quantity: Decimal, bars: Mapping[int, OhlcvBar]
    ) -> _Pending:
        return _Pending(
            order=OrderRequest(
                client_order_id=f"e-{candidate.instrument_id}",
                portfolio_id=1,
                instrument_id=candidate.instrument_id,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=quantity,
                intent="entry",
            ),
            candidate=candidate,
            exit_reason=None,
            reference_bar=bars[candidate.instrument_id],
        )

    @staticmethod
    def _exit_order(state: _RunState, signal: ExitSignal, bars: Mapping[int, OhlcvBar]) -> _Pending:
        lot = state.lots[signal.instrument_id]
        quantity = (lot.quantity * signal.fraction).quantize(Decimal(1))
        if signal.fraction >= 1 or quantity > lot.quantity:
            quantity = lot.quantity
        if quantity <= 0:
            quantity = lot.quantity
        if signal.reason is ExitReason.PARTIAL_PROFIT:
            lot.partial_profit_taken = True
        return _Pending(
            order=OrderRequest(
                client_order_id=f"x-{signal.instrument_id}",
                portfolio_id=1,
                instrument_id=signal.instrument_id,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
                intent=str(signal.reason),
            ),
            candidate=None,
            exit_reason=signal.reason,
            reference_bar=bars[signal.instrument_id],
        )

    def _close_out(self, state: _RunState, last_session: dt.date) -> None:
        """Everything still open leaves at the final mark.

        Recorded as ``BACKTEST_END`` rather than folded into the metrics as an
        unrealised gain: a strategy holding a large winner on the last day did
        not earn that money, and labelling the exit says so.
        """
        for instrument_id in list(state.lots):
            lot = state.lots[instrument_id]
            state.close_at(
                instrument_id=instrument_id,
                price=state.last_prices.get(instrument_id, lot.entry_price),
                exit_date=last_session,
                reason=ExitReason.BACKTEST_END,
            )

    def _result(self, spec: BacktestSpec, state: _RunState) -> BacktestResult:
        caveats = list(state.caveats)
        warnings = list(state.warnings)
        if state.abandoned_entries:
            warnings.append(
                f"{state.abandoned_entries} entries were abandoned because the fill "
                "gapped to or through the intended stop"
            )
        metrics: PerformanceMetrics | None = None
        if len(state.curve) >= 2:
            metrics = self.analyzer.compute(state.trades, state.curve)
        else:
            caveats.append("fewer than two marked sessions; no metrics computed")
        return BacktestResult(
            spec=spec,
            manifest=self.manifest,
            status=BacktestStatus.COMPLETED,
            metrics=metrics,
            trades=tuple(state.trades),
            equity_curve=tuple(state.curve),
            warnings=tuple(warnings),
            data_caveats=tuple(caveats),
        )


@dataclass(slots=True)
class _RunState:
    """Cash, open lots, the curve and the trade log, advanced session by session."""

    spec: BacktestSpec
    cash: Decimal = field(init=False)
    equity: Decimal = field(init=False)
    lots: dict[int, _OpenLot] = field(default_factory=dict)
    pending: list[_Pending] = field(default_factory=list)
    trades: list[BacktestTrade] = field(default_factory=list)
    curve: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    last_prices: dict[int, Decimal] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    #: Entries whose fill gapped through their own stop before they opened.
    #: Counted rather than dropped silently: a large number means the stop is
    #: too tight for the universe's overnight behaviour, which is a finding
    #: about the strategy and not a quirk of the simulator.
    abandoned_entries: int = 0
    as_of: dt.datetime = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

    def __post_init__(self) -> None:
        self.cash = self.spec.initial_capital
        self.equity = self.spec.initial_capital

    def portfolio(self) -> PortfolioState:
        return PortfolioState(
            portfolio_id=1,
            as_of=self.as_of,
            cash=self.cash,
            equity=self.equity,
            positions=tuple(
                PositionState(
                    position_id=instrument_id,
                    portfolio_id=1,
                    instrument_id=instrument_id,
                    side=Side.LONG,
                    status=PositionStatus.OPEN,
                    quantity=lot.quantity,
                    average_entry_price=lot.entry_price,
                    stop_price=lot.current_stop,
                    opened_on=lot.entry_date,
                    initial_stop_price=lot.initial_stop,
                )
                for instrument_id, lot in self.lots.items()
            ),
            last_prices=dict(self.last_prices),
        )

    def mark(self, bars: Mapping[int, OhlcvBar], session_date: dt.date) -> None:
        for instrument_id, bar in bars.items():
            self.last_prices[instrument_id] = bar.close
        held = sum(
            (
                lot.quantity * self.last_prices.get(instrument_id, lot.entry_price)
                for instrument_id, lot in self.lots.items()
            ),
            Decimal(0),
        )
        self.equity = self.cash + held
        self.as_of = dt.datetime.combine(session_date, dt.time(), tzinfo=dt.UTC)
        self.curve.append((session_date, self.equity))

    def open_lot(self, fill: Fill, candidate: EntryCandidate | None, session_date: dt.date) -> None:
        self.cash -= fill.gross_value + fill.commission
        existing = self.lots.get(fill.instrument_id)
        if existing is None:
            assert candidate is not None
            self.lots[fill.instrument_id] = _OpenLot(
                instrument_id=fill.instrument_id,
                entry_date=session_date,
                entry_price=fill.price,
                entry_costs=fill.commission,
                quantity=fill.quantity,
                initial_stop=candidate.stop_price,
                current_stop=candidate.stop_price,
                highest=fill.price,
                lowest=fill.price,
                score_at_entry=candidate.score.total,
                sector=candidate.sector,
            )
            return
        total = existing.quantity + fill.quantity
        existing.entry_price = (
            existing.entry_price * existing.quantity + fill.price * fill.quantity
        ) / total
        existing.quantity = total
        existing.entry_costs += fill.commission

    def close_at(
        self,
        *,
        instrument_id: int,
        price: Decimal,
        exit_date: dt.date,
        reason: ExitReason,
    ) -> None:
        """Liquidate a whole lot at a stated price, with no order and no fill.

        Used where no market transaction happened: a delisting, and the run's
        final mark. No commission is charged, because none was paid.
        """
        lot = self.lots.get(instrument_id)
        if lot is None:
            return
        self.cash += price * lot.quantity
        self.record_exit(
            instrument_id=instrument_id,
            quantity=lot.quantity,
            price=price,
            costs=Decimal(0),
            exit_date=exit_date,
            reason=reason,
        )

    def close_lot(self, fill: Fill, reason: ExitReason) -> None:
        self.cash += fill.gross_value - fill.commission
        self.record_exit(
            instrument_id=fill.instrument_id,
            quantity=fill.quantity,
            price=fill.price,
            costs=fill.commission,
            exit_date=fill.filled_at.date(),
            reason=reason,
        )

    def record_exit(
        self,
        *,
        instrument_id: int,
        quantity: Decimal,
        price: Decimal,
        costs: Decimal,
        exit_date: dt.date,
        reason: ExitReason,
    ) -> None:
        """One trade row per exit, per ``ExitReason``'s own reasoning."""
        lot = self.lots.get(instrument_id)
        if lot is None or quantity <= 0:
            return
        quantity = min(quantity, lot.quantity)
        share = quantity / lot.quantity if lot.quantity else Decimal(0)
        entry_costs = lot.entry_costs * share
        gross = (price - lot.entry_price) * quantity
        total_costs = entry_costs + costs
        risk_per_share = lot.entry_price - lot.initial_stop
        self.trades.append(
            BacktestTrade(
                instrument_id=instrument_id,
                entry_date=lot.entry_date,
                entry_price=lot.entry_price,
                exit_date=exit_date,
                exit_price=price,
                quantity=quantity,
                side="long",
                exit_reason=str(reason),
                gross_pnl=gross,
                net_pnl=gross - total_costs,
                costs=total_costs,
                return_pct=float((price - lot.entry_price) / lot.entry_price),
                r_multiple=(
                    float((price - lot.entry_price) / risk_per_share)
                    if risk_per_share > 0
                    else None
                ),
                holding_sessions=lot.sessions_held,
                mae=lot.lowest,
                mfe=lot.highest,
                score_at_entry=lot.score_at_entry,
                sector=lot.sector,
            )
        )
        if quantity >= lot.quantity:
            del self.lots[instrument_id]
        else:
            self.lots[instrument_id] = dataclasses.replace(
                lot, quantity=lot.quantity - quantity, entry_costs=lot.entry_costs - entry_costs
            )
