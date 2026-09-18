"""Tests for the stop ladder.

The central one is ``test_a_stop_never_moves_down_over_any_path``: the ratchet
is the invariant this module exists to hold, and it is asserted over generated
price paths rather than over the three hand-written cases that happen to
honour it.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import replace
from decimal import Decimal

import pytest

from tradeit.core.enums import ExitReason, PositionStatus, Side
from tradeit.portfolio.base import PositionState
from tradeit.portfolio.stops import (
    ExitSignal,
    StopContext,
    StopLadder,
    StopStage,
    ratchet,
)
from tradeit.strategy.config import ExitConfig

ENTRY = Decimal(100)
INITIAL_STOP = Decimal(90)  # 10 points of risk: 1R = 10


def _position(
    *,
    stop: Decimal = INITIAL_STOP,
    initial_stop: Decimal = INITIAL_STOP,
    side: Side = Side.LONG,
    status: PositionStatus = PositionStatus.OPEN,
    target: Decimal | None = None,
) -> PositionState:
    return PositionState(
        position_id=1,
        portfolio_id=1,
        instrument_id=7,
        side=side,
        status=status,
        quantity=Decimal(100),
        average_entry_price=ENTRY,
        stop_price=stop,
        opened_on=dt.date(2024, 5, 1),
        initial_stop_price=initial_stop,
        target_price=target,
    )


def _context(
    last: str,
    *,
    high: str | None = None,
    atr: str | None = None,
    sessions: int = 5,
    partial_taken: bool = False,
) -> StopContext:
    return StopContext(
        last_price=Decimal(last),
        high_since_entry=Decimal(high if high is not None else last),
        sessions_held=sessions,
        partial_profit_taken=partial_taken,
        atr=Decimal(atr) if atr is not None else None,
    )


LADDER = StopLadder(config=ExitConfig())


class TestRMultiple:
    def test_r_is_measured_against_the_initial_stop_not_the_current_one(self) -> None:
        """A trailed stop must not shrink the denominator."""
        position = _position(stop=Decimal(105), initial_stop=INITIAL_STOP)
        assert LADDER.r_multiple(position, Decimal(120)) == Decimal(2)

    def test_a_loss_is_a_negative_r(self) -> None:
        assert LADDER.r_multiple(_position(), Decimal(95)) == Decimal("-0.5")

    def test_a_position_opened_with_no_risk_has_no_r(self) -> None:
        position = _position(stop=Decimal(100), initial_stop=Decimal(100))
        with pytest.raises(ValueError, match="R is undefined"):
            LADDER.r_multiple(position, Decimal(120))


class TestTheLadder:
    def test_below_breakeven_the_stop_does_not_move(self) -> None:
        update = LADDER.advance(_position(), _context("105"))  # 0.5R
        assert update.stage == StopStage.INITIAL
        assert update.stop_price == INITIAL_STOP
        assert not update.moved

    def test_at_breakeven_r_the_stop_moves_to_entry(self) -> None:
        update = LADDER.advance(_position(), _context("110"))  # 1.0R
        assert update.stage == StopStage.BREAKEVEN
        assert update.stop_price == ENTRY
        assert update.moved

    def test_in_the_trailing_stage_the_stop_follows_the_high(self) -> None:
        update = LADDER.advance(_position(), _context("120", high="122", atr="2"))  # 2.0R
        assert update.stage == StopStage.TRAILING
        assert update.stop_price == Decimal(116)  # 122 - 3 x 2
        assert "trailing" in update.reason

    def test_a_trail_looser_than_breakeven_is_not_taken(self) -> None:
        """Early in the trailing stage a wide ATR puts the trail below entry."""
        update = LADDER.advance(_position(), _context("116", high="116", atr="6"))  # 1.6R
        assert update.stage == StopStage.BREAKEVEN
        assert update.stop_price == ENTRY

    def test_a_missing_atr_falls_back_to_breakeven_rather_than_failing(self) -> None:
        update = LADDER.advance(_position(), _context("120", high="122", atr=None))
        assert update.stage == StopStage.BREAKEVEN
        assert update.stop_price == ENTRY

    def test_a_falling_price_does_not_regress_the_stop(self) -> None:
        trailed = _position(stop=Decimal(116))
        update = LADDER.advance(trailed, _context("104", high="122", atr="2"))  # 0.4R
        assert update.stop_price == Decimal(116)
        assert not update.moved


class TestTheRatchet:
    def test_a_looser_proposal_is_refused(self) -> None:
        assert ratchet(Decimal(100), Decimal(95)) == Decimal(100)

    def test_a_tighter_proposal_is_taken(self) -> None:
        assert ratchet(Decimal(100), Decimal(105)) == Decimal(105)

    @pytest.mark.parametrize("seed", [1, 2, 3, 17, 99])
    def test_a_stop_never_moves_down_over_any_path(self, seed: int) -> None:
        """Generated paths, including ones that spike and collapse."""
        rng = random.Random(seed)
        position = _position()
        price = ENTRY
        high = ENTRY
        previous = INITIAL_STOP
        for _ in range(250):
            price = max(Decimal(1), price + Decimal(str(round(rng.uniform(-6, 6.5), 2))))
            high = max(high, price)
            # ATR wanders too: a shrinking ATR tightens the trail, a growing
            # one would loosen it if the ratchet were not there.
            atr = Decimal(str(round(rng.uniform(0.5, 9.0), 2)))
            update = LADDER.advance(
                position,
                StopContext(
                    last_price=price,
                    high_since_entry=high,
                    sessions_held=1,
                    partial_profit_taken=False,
                    atr=atr,
                ),
            )
            assert update.stop_price >= previous
            previous = update.stop_price
            position = replace(position, stop_price=update.stop_price)


class TestRefusals:
    def test_a_short_position_is_refused_not_silently_inverted(self) -> None:
        with pytest.raises(ValueError, match="long-only"):
            LADDER.advance(_position(side=Side.SHORT), _context("110"))

    def test_a_position_without_a_stop_is_refused(self) -> None:
        position = PositionState(
            position_id=1,
            portfolio_id=1,
            instrument_id=7,
            side=Side.LONG,
            status=PositionStatus.OPEN,
            quantity=Decimal(100),
            average_entry_price=ENTRY,
            stop_price=None,
            opened_on=dt.date(2024, 5, 1),
        )
        with pytest.raises(ValueError, match="no stop"):
            LADDER.advance(position, _context("110"))

    def test_an_exit_of_none_is_not_an_exit(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            ExitSignal(
                instrument_id=7,
                reason=ExitReason.STOP_LOSS,
                fraction=Decimal(0),
                detail="",
            )

    def test_an_exit_of_more_than_all_is_not_a_position(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            ExitSignal(
                instrument_id=7,
                reason=ExitReason.TARGET_REACHED,
                fraction=Decimal("1.5"),
                detail="",
            )


class TestExitPrecedence:
    def test_an_untouched_position_has_no_exit(self) -> None:
        assert LADDER.evaluate_exit(_position(), _context("105")) is None

    def test_a_breached_initial_stop_is_a_stop_loss(self) -> None:
        signal = LADDER.evaluate_exit(_position(), _context("89"))
        assert signal is not None
        assert signal.reason is ExitReason.STOP_LOSS
        assert signal.fraction == Decimal(1)

    def test_a_breached_trailed_stop_is_a_trailing_stop(self) -> None:
        signal = LADDER.evaluate_exit(_position(stop=Decimal(116)), _context("115"))
        assert signal is not None
        assert signal.reason is ExitReason.TRAILING_STOP

    def test_a_bar_that_broke_the_stop_and_touched_the_target_is_a_stop(self) -> None:
        """Booking the win would flatter every violent day the strategy had."""
        position = _position(target=Decimal(130))
        signal = LADDER.evaluate_exit(position, _context("89", high="131"))
        assert signal is not None
        assert signal.reason is ExitReason.STOP_LOSS

    def test_a_target_outranks_a_time_stop(self) -> None:
        position = _position(target=Decimal(130))
        signal = LADDER.evaluate_exit(position, _context("131", sessions=99))
        assert signal is not None
        assert signal.reason is ExitReason.TARGET_REACHED

    def test_a_time_stop_outranks_a_partial_profit(self) -> None:
        signal = LADDER.evaluate_exit(_position(), _context("120", sessions=40))
        assert signal is not None
        assert signal.reason is ExitReason.TIME_STOP

    def test_partial_profit_fires_once(self) -> None:
        taken = LADDER.evaluate_exit(_position(), _context("120"))
        assert taken is not None
        assert taken.reason is ExitReason.PARTIAL_PROFIT
        assert taken.fraction == Decimal("0.33")
        again = LADDER.evaluate_exit(_position(), _context("120", partial_taken=True))
        assert again is None

    def test_a_closed_position_produces_no_exit(self) -> None:
        position = _position(status=PositionStatus.CLOSED)
        assert LADDER.evaluate_exit(position, _context("50")) is None

    def test_a_disabled_time_stop_is_not_an_immediate_one(self) -> None:
        ladder = StopLadder(config=ExitConfig(time_stop_sessions=None))
        assert ladder.evaluate_exit(_position(), _context("105", sessions=9999)) is None


class TestHoldingToTheClock:
    """``price_exits=False``: the arm the stop-ladder registration compares with.

    Only the time stop may close the position; the stop still exists because it
    sized the position, so the two arms own the same amounts.
    """

    HOLD = StopLadder(config=ExitConfig(price_exits=False, time_stop_sessions=63))

    def test_a_breached_stop_does_not_close_it(self) -> None:
        assert self.HOLD.evaluate_exit(_position(), _context("50")) is None

    def test_a_breached_trailed_stop_does_not_close_it(self) -> None:
        assert self.HOLD.evaluate_exit(_position(stop=Decimal(116)), _context("115")) is None

    def test_a_target_does_not_close_it(self) -> None:
        position = _position(target=Decimal(130))
        assert self.HOLD.evaluate_exit(position, _context("131")) is None

    def test_a_partial_profit_does_not_fire(self) -> None:
        assert self.HOLD.evaluate_exit(_position(), _context("120")) is None

    def test_the_clock_still_closes_it_whatever_the_price(self) -> None:
        for price in ("50", "100", "150"):
            signal = self.HOLD.evaluate_exit(_position(), _context(price, sessions=63))
            assert signal is not None
            assert signal.reason is ExitReason.TIME_STOP
            assert signal.fraction == Decimal(1)

    def test_the_default_is_unchanged(self) -> None:
        """Every existing run must behave exactly as before the switch existed."""
        assert ExitConfig().price_exits is True
        signal = LADDER.evaluate_exit(_position(), _context("89"))
        assert signal is not None and signal.reason is ExitReason.STOP_LOSS
