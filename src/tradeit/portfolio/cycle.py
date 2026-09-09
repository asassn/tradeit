"""One portfolio cycle: manage what is held, then deploy what that frees.

This is where the pieces meet, and where compounding actually lives. There is
no "compounding feature" anywhere in this codebase — there is a loop that sizes
in fractions of current equity, releases capital when positions leave, and
redeploys it in the same pass. Compounding is what that loop does.

Order matters, and it is the whole design
-----------------------------------------

**Exits are planned before entries.** Capital and heat released by a position
leaving today are available to a position entering today. Running entries first
would have the portfolio decline a trade for want of a risk budget it was about
to free, and the cost of that is invisible: the trade simply never appears.

**Each admitted entry is folded into a provisional state before the next
candidate is considered.** This is the subtle one. Sizing every candidate
against the same opening snapshot lets the entire slate through — twelve
positions each individually inside a 6% heat limit, 6% each, because none of
them can see the others. The provisional state is what makes the limits
cumulative, and it is why this loop cannot be replaced by a list comprehension
over the ranked slate.

**Every candidate is sized twice, and the second is authoritative.** Ranking
needs a size to rank, and the size depends on what has already been admitted,
which depends on the ranking. The knot is cut by sizing once against the
post-exit state for the ranker's benefit, then re-sizing at admission against
the state as it actually stands. The first number never authorises anything.

What this does not do
---------------------

It plans; it does not execute. And it holds equity constant across the plan:
converting cash into a position does not change equity, and an exit's realised
profit belongs to the fills that settle it, not to the intention to sell. A
plan that marked its own expected profit into equity would size the next entry
off a gain that has not happened.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradeit.core.enums import PositionStatus, RiskDecision, Side
from tradeit.portfolio.base import (
    AllocationCandidate,
    AllocationRanker,
    PortfolioState,
    PositionSizer,
    PositionState,
    SizingDecision,
)
from tradeit.portfolio.stops import ExitSignal, StopContext, StopLadder, StopUpdate
from tradeit.risk.base import RiskEngine, RiskVerdict
from tradeit.strategy.base import OpportunityScore

__all__ = ["CyclePlan", "EntryCandidate", "PlannedEntry", "PortfolioCycle"]


@dataclass(frozen=True, slots=True)
class EntryCandidate:
    """A scored opportunity with the prices needed to size it."""

    score: OpportunityScore
    entry_price: Decimal
    stop_price: Decimal
    sector: str | None = None
    average_dollar_volume: Decimal | None = None

    @property
    def instrument_id(self) -> int:
        return self.score.instrument_id


@dataclass(frozen=True, slots=True)
class PlannedEntry:
    """A candidate, what it was sized at, and what the risk engine said.

    Declined candidates are kept. The record of what the system refused to buy
    is what makes a strategy's results readable afterwards; keeping only the
    fills makes every strategy look more decisive than it was.
    """

    candidate: EntryCandidate
    sizing: SizingDecision
    verdict: RiskVerdict
    quantity: Decimal

    @property
    def accepted(self) -> bool:
        return self.quantity > 0

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.candidate.entry_price

    def explain(self) -> str:
        if self.accepted:
            return f"{self.quantity} shares, bound by {self.sizing.binding_constraint}"
        return self.verdict.explain() or (self.sizing.rejection_reason or "declined")


@dataclass(frozen=True, slots=True)
class CyclePlan:
    """Everything one cycle decided, including what it decided against."""

    stop_updates: tuple[StopUpdate, ...]
    exits: tuple[ExitSignal, ...]
    entries: tuple[PlannedEntry, ...]
    #: The portfolio as it would stand with every exit filled and every
    #: accepted entry opened. It is what the limits were actually measured
    #: against as the cycle progressed, so it is reported rather than left for
    #: the caller to reconstruct and get subtly wrong.
    projected: PortfolioState

    @property
    def accepted(self) -> tuple[PlannedEntry, ...]:
        return tuple(entry for entry in self.entries if entry.accepted)

    @property
    def declined(self) -> tuple[PlannedEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.accepted)

    @property
    def moved_stops(self) -> tuple[StopUpdate, ...]:
        return tuple(update for update in self.stop_updates if update.moved)

    @property
    def capital_deployed(self) -> Decimal:
        return sum((entry.notional for entry in self.accepted), Decimal(0))


@dataclass(frozen=True, slots=True)
class PortfolioCycle:
    """Runs one management-then-deployment pass over a portfolio."""

    sizer: PositionSizer
    engine: RiskEngine
    ranker: AllocationRanker
    ladder: StopLadder

    def plan(
        self,
        portfolio: PortfolioState,
        candidates: Sequence[EntryCandidate],
        contexts: Mapping[int, StopContext],
        correlation: dict[tuple[int, int], float] | None = None,
    ) -> CyclePlan:
        """Plan stops, exits and entries for one session.

        ``contexts`` must cover every open position. A held position with no
        market context is an error rather than a skip: silently declining to
        manage a stop because the price feed was short a symbol is the failure
        mode this refuses to have.
        """
        duplicates = sorted(
            {
                candidate.instrument_id
                for index, candidate in enumerate(candidates)
                if any(
                    other.instrument_id == candidate.instrument_id
                    for other in candidates[index + 1 :]
                )
            }
        )
        if duplicates:
            # Two candidates for one instrument leaves no answer to "at what
            # price?" -- and silently taking the last one would size the trade
            # off a row the ranking never saw.
            raise ValueError(f"slate contains more than one candidate for {duplicates}")

        missing = sorted(
            position.instrument_id
            for position in portfolio.open_positions
            if position.instrument_id not in contexts
        )
        if missing:
            raise ValueError(
                f"no market context for held instruments {missing}; their stops cannot be "
                "managed, and skipping them would leave positions unprotected"
            )

        stop_updates: list[StopUpdate] = []
        exits: list[ExitSignal] = []
        for position in portfolio.open_positions:
            context = contexts[position.instrument_id]
            stop_updates.append(self.ladder.advance(position, context))
            signal = self.ladder.evaluate_exit(position, context)
            if signal is not None:
                exits.append(signal)

        after_exits = self._apply_exits(portfolio, exits, contexts)
        entries, projected = self._plan_entries(after_exits, candidates, correlation)
        return CyclePlan(
            stop_updates=tuple(stop_updates),
            exits=tuple(exits),
            entries=tuple(entries),
            projected=projected,
        )

    @staticmethod
    def _apply_exits(
        portfolio: PortfolioState,
        exits: Sequence[ExitSignal],
        contexts: Mapping[int, StopContext],
    ) -> PortfolioState:
        """The portfolio as it would stand once the exits are filled.

        Proceeds return to cash so the same cycle can redeploy them -- the
        recycling this loop is named for. Equity is unchanged: the position was
        already marked at this price, and selling it does not create value.
        """
        if not exits:
            return portfolio
        fractions = {signal.instrument_id: signal.fraction for signal in exits}
        remaining: list[PositionState] = []
        cash = portfolio.cash
        for position in portfolio.positions:
            fraction = fractions.get(position.instrument_id)
            if fraction is None or position.status is not PositionStatus.OPEN:
                remaining.append(position)
                continue
            sold = position.quantity * fraction
            cash += sold * contexts[position.instrument_id].last_price
            if fraction >= 1 or sold >= position.quantity:
                continue
            remaining.append(dataclasses.replace(position, quantity=position.quantity - sold))
        return dataclasses.replace(portfolio, cash=cash, positions=tuple(remaining))

    def _plan_entries(
        self,
        portfolio: PortfolioState,
        candidates: Sequence[EntryCandidate],
        correlation: dict[tuple[int, int], float] | None,
    ) -> tuple[list[PlannedEntry], PortfolioState]:
        # First sizing: for the ranker only. See the module docstring.
        for_ranking = [
            AllocationCandidate(
                score=candidate.score,
                sizing=self._size(candidate, portfolio),
                sector=candidate.sector,
            )
            for candidate in candidates
        ]
        by_id = {candidate.instrument_id: candidate for candidate in candidates}
        ranked = self.ranker.rank(for_ranking, portfolio, correlation)

        provisional = portfolio
        planned: list[PlannedEntry] = []
        for allocation in ranked:
            candidate = by_id[allocation.score.instrument_id]
            # Second sizing: authoritative, against the state as it now stands.
            sizing = self._size(candidate, provisional)
            verdict = self.engine.evaluate(sizing, provisional)
            quantity = (
                verdict.final_quantity
                if verdict.decision is not RiskDecision.REJECT
                else Decimal(0)
            )
            planned.append(
                PlannedEntry(candidate=candidate, sizing=sizing, verdict=verdict, quantity=quantity)
            )
            if quantity > 0:
                provisional = self._admit(provisional, candidate, quantity)
        return planned, provisional

    def _size(self, candidate: EntryCandidate, portfolio: PortfolioState) -> SizingDecision:
        return self.sizer.size(
            candidate.score,
            candidate.entry_price,
            candidate.stop_price,
            portfolio,
            candidate.average_dollar_volume,
        )

    @staticmethod
    def _admit(
        portfolio: PortfolioState, candidate: EntryCandidate, quantity: Decimal
    ) -> PortfolioState:
        """Fold an admitted entry in, so the next candidate sees it.

        An add to a name already held is merged into that position at a
        weighted-average entry rather than appended as a second one: the
        pyramid limit counts entries, and two rows for one holding would let
        the third add through by splitting the count.
        """
        notional = quantity * candidate.entry_price
        existing = next(
            (
                position
                for position in portfolio.open_positions
                if position.instrument_id == candidate.instrument_id
            ),
            None,
        )
        prices = dict(portfolio.last_prices)
        prices[candidate.instrument_id] = candidate.entry_price

        if existing is None:
            opened = PositionState(
                position_id=0,
                portfolio_id=portfolio.portfolio_id,
                instrument_id=candidate.instrument_id,
                side=Side.LONG,
                status=PositionStatus.OPEN,
                quantity=quantity,
                average_entry_price=candidate.entry_price,
                stop_price=candidate.stop_price,
                opened_on=portfolio.as_of.date(),
                initial_stop_price=candidate.stop_price,
            )
            positions = (*portfolio.positions, opened)
        else:
            total = existing.quantity + quantity
            merged = dataclasses.replace(
                existing,
                quantity=total,
                average_entry_price=(existing.quantity * existing.average_entry_price + notional)
                / total,
                pyramid_entries=existing.pyramid_entries + 1,
            )
            positions = tuple(
                merged if position is existing else position for position in portfolio.positions
            )

        return dataclasses.replace(
            portfolio,
            cash=portfolio.cash - notional,
            positions=positions,
            last_prices=prices,
        )
