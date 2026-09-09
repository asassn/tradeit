"""Stops, trailing and exits: the ladder a stop climbs and the reasons to leave.

Everything here implements parameters ``ExitConfig`` already declares. Nothing
in this module invents a threshold, and the one cross-parameter coherence
requirement — that breakeven cannot sit above the trailing activation — is
already enforced by the configuration itself.

The ladder
----------

A stop occupies one of three stages, and it climbs them in order:

* **initial** — where the position opened, and the only stop that defines the
  R it is measured in
* **breakeven** — at ``move_stop_to_breakeven_at_r``, the stop moves to entry
  and the trade becomes free
* **trailing** — at ``activate_trailing_at_r``, the stop follows the highest
  price seen since entry, ``trailing_stop_atr_multiple`` ATRs below it

**A stop never moves down.** Every proposal passes through :func:`ratchet`, so
no policy can loosen one — not a shrinking ATR, not a lower high after a data
correction, not a stage regression when an R multiple falls back below its
trigger. This is the invariant the module exists to hold, and it is asserted as
a property test over generated price paths rather than trusted to the three
call sites that currently honour it.

**R is measured against the initial stop, never the current one.** Measuring
against a trailed stop shrinks the denominator as the trade works, so a
position would report a larger R the more its stop had already climbed — and
``take_partial_profit_at_r`` would fire on the trail rather than on the profit.
This is why ``PositionState`` carries ``initial_stop_price`` separately.

**Long-only, and it says so.** A short position is refused rather than sized
with the inequalities flipped: nothing in this system opens one yet — the sizer
refuses a stop above entry — and a stop ladder that silently accepted shorts
would be untested machinery waiting to be trusted.

Exit precedence
---------------

Several reasons can fire on the same bar. The order is fixed and deliberate:
**stop, then target, then time, then partial profit.** A bar that broke the
stop *and* touched the target is recorded as a stop, because resolving it the
other way books a win on a bar that also broke the stop, and the record would
flatter every violent day the strategy ever had.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tradeit.core.enums import ExitReason, PositionStatus, Side
from tradeit.portfolio.base import PositionState
from tradeit.strategy.config import ExitConfig

__all__ = [
    "ExitSignal",
    "StopContext",
    "StopLadder",
    "StopStage",
    "StopUpdate",
    "ratchet",
]


class StopStage:
    """The rungs a stop climbs. Strings, so they survive into a stored record."""

    INITIAL = "initial"
    BREAKEVEN = "breakeven"
    TRAILING = "trailing"


def ratchet(current: Decimal, proposed: Decimal) -> Decimal:
    """The tighter of the two stops. A long stop never moves down."""
    return max(current, proposed)


@dataclass(frozen=True, slots=True)
class StopContext:
    """What the ladder needs about a position that the position does not carry.

    ``high_since_entry`` is the running maximum of the *high* since the
    position opened, not of the close. A trail computed from closes ignores the
    intraday extreme it is supposed to follow.

    ``partial_profit_taken`` has no default: a caller that has not tracked it
    would otherwise scale out of the same position on every bar above the
    trigger.
    """

    last_price: Decimal
    high_since_entry: Decimal
    sessions_held: int
    partial_profit_taken: bool
    atr: Decimal | None = None


@dataclass(frozen=True, slots=True)
class StopUpdate:
    """A proposed stop, the stage that produced it, and whether it moved."""

    instrument_id: int
    previous_stop: Decimal
    stop_price: Decimal
    stage: str
    reason: str

    @property
    def moved(self) -> bool:
        return self.stop_price != self.previous_stop


@dataclass(frozen=True, slots=True)
class ExitSignal:
    """A reason to leave, and how much of the position it applies to."""

    instrument_id: int
    reason: ExitReason
    fraction: Decimal
    detail: str

    def __post_init__(self) -> None:
        if not Decimal(0) < self.fraction <= Decimal(1):
            raise ValueError(
                f"exit fraction {self.fraction} is outside (0, 1]; an exit of none is not "
                "an exit and an exit of more than all is not a position"
            )


@dataclass(frozen=True, slots=True)
class StopLadder:
    """Advances stops and decides when a position leaves."""

    config: ExitConfig
    name: str = "stop_ladder"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "trailing_stop_atr_multiple": self.config.trailing_stop_atr_multiple,
            "activate_trailing_at_r": self.config.activate_trailing_at_r,
            "move_stop_to_breakeven_at_r": self.config.move_stop_to_breakeven_at_r,
            "time_stop_sessions": self.config.time_stop_sessions,
            "take_partial_profit_at_r": self.config.take_partial_profit_at_r,
            "partial_profit_fraction": self.config.partial_profit_fraction,
        }

    @staticmethod
    def _require_long_with_stops(position: PositionState) -> tuple[Decimal, Decimal]:
        if position.side is not Side.LONG:
            raise ValueError(
                f"position {position.position_id} is {position.side}; the stop ladder is "
                "long-only, and nothing in this system opens a short yet"
            )
        if position.stop_price is None or position.initial_stop_price is None:
            raise ValueError(
                f"position {position.position_id} has no stop; its risk is undefined and "
                "no R multiple can be computed"
            )
        return position.stop_price, position.initial_stop_price

    def r_multiple(self, position: PositionState, last_price: Decimal) -> Decimal:
        """Profit in units of the risk originally taken.

        Against ``initial_stop_price``, never the current stop. See the module
        docstring for what measuring against the trail would do.
        """
        _, initial_stop = self._require_long_with_stops(position)
        initial_risk = position.average_entry_price - initial_stop
        if initial_risk <= 0:
            raise ValueError(
                f"position {position.position_id} opened with a stop at or above entry; "
                "R is undefined"
            )
        return (last_price - position.average_entry_price) / initial_risk

    def advance(self, position: PositionState, context: StopContext) -> StopUpdate:
        """Where the stop should sit now, given how far the trade has run."""
        current_stop, _ = self._require_long_with_stops(position)
        r = self.r_multiple(position, context.last_price)

        stage = StopStage.INITIAL
        proposed = current_stop
        reason = f"below {self.config.move_stop_to_breakeven_at_r:.2f}R; stop unchanged"

        if r >= Decimal(str(self.config.move_stop_to_breakeven_at_r)):
            stage = StopStage.BREAKEVEN
            proposed = position.average_entry_price
            reason = (
                f"{r:.2f}R reached {self.config.move_stop_to_breakeven_at_r:.2f}R; "
                "stop to breakeven"
            )

        if r >= Decimal(str(self.config.activate_trailing_at_r)) and context.atr is not None:
            trail = (
                context.high_since_entry
                - Decimal(str(self.config.trailing_stop_atr_multiple)) * context.atr
            )
            # The trail is a *candidate*, not an instruction: early in the
            # trailing stage it often sits below breakeven, and taking it
            # would loosen a stop the ladder has already tightened.
            if trail > proposed:
                stage = StopStage.TRAILING
                proposed = trail
                reason = (
                    f"{r:.2f}R; trailing "
                    f"{self.config.trailing_stop_atr_multiple:.2f} ATR below "
                    f"{context.high_since_entry}"
                )

        return StopUpdate(
            instrument_id=position.instrument_id,
            previous_stop=current_stop,
            stop_price=ratchet(current_stop, proposed),
            stage=stage,
            reason=reason,
        )

    def evaluate_exit(self, position: PositionState, context: StopContext) -> ExitSignal | None:
        """The first exit reason that applies, in the declared precedence."""
        if position.status is not PositionStatus.OPEN:
            # Checked before the stop requirement: a closed position is allowed
            # to have no stop, and asking it for one would raise.
            return None
        current_stop, initial_stop = self._require_long_with_stops(position)

        if context.last_price <= current_stop:
            trailed = current_stop > initial_stop
            return ExitSignal(
                instrument_id=position.instrument_id,
                reason=ExitReason.TRAILING_STOP if trailed else ExitReason.STOP_LOSS,
                fraction=Decimal(1),
                detail=f"price {context.last_price} at or below stop {current_stop}",
            )

        r = self.r_multiple(position, context.last_price)

        if position.target_price is not None and context.last_price >= position.target_price:
            return ExitSignal(
                instrument_id=position.instrument_id,
                reason=ExitReason.TARGET_REACHED,
                fraction=Decimal(1),
                detail=f"price {context.last_price} reached target {position.target_price}",
            )

        if self.config.time_stop_sessions is not None and (
            context.sessions_held >= self.config.time_stop_sessions
        ):
            return ExitSignal(
                instrument_id=position.instrument_id,
                reason=ExitReason.TIME_STOP,
                fraction=Decimal(1),
                detail=(
                    f"held {context.sessions_held} sessions, at the "
                    f"{self.config.time_stop_sessions} limit, at {r:.2f}R"
                ),
            )

        target = self.config.take_partial_profit_at_r
        if target is not None and not context.partial_profit_taken and r >= Decimal(str(target)):
            return ExitSignal(
                instrument_id=position.instrument_id,
                reason=ExitReason.PARTIAL_PROFIT,
                fraction=Decimal(str(self.config.partial_profit_fraction)),
                detail=f"{r:.2f}R reached the {target:.2f}R partial-profit trigger",
            )

        return None
