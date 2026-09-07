"""Could this security actually have been bought on this day?

12.8% of adjusted bars close at exactly the previous price and 5.8% carry zero
volume. A strategy tested on those does not produce an optimistic result, it
produces a meaningless one: the fills never existed, so neither did the
returns. These tests pin that the screen refuses rather than scores, and that
its three reasons stay independent.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.research01.tradability import (
    Tradability,
    TradabilityRule,
    UntradableReason,
    assess_tradability,
    tradable_days,
)

DAY = dt.date(2020, 6, 1)
RULE = TradabilityRule(
    min_price=Decimal("1.00"),
    min_dollar_volume=Decimal("100000"),
    reject_stale_quotes=True,
)


def _assess(close: str, volume: str, previous: str | None = None) -> Tradability:
    return assess_tradability(
        session_date=DAY,
        close=Decimal(close),
        volume=Decimal(volume),
        previous_close=Decimal(previous) if previous is not None else None,
        rule=RULE,
    )


def test_a_liquid_day_is_tradable() -> None:
    assert _assess("25.00", "50000").tradable


def test_zero_volume_is_untradable_however_good_the_close_looks() -> None:
    """The close is a quote, not a transaction. Nobody bought at it."""
    got = _assess("25.00", "0")
    assert not got.tradable
    assert UntradableReason.NO_VOLUME in got.reasons


def test_a_sub_dollar_price_is_refused() -> None:
    """Where survivorship and tradability meet: failing companies spend their
    last months here, and a backtest filling at the close overstates every
    round trip."""
    got = _assess("0.40", "5000000")
    assert UntradableReason.BELOW_PRICE_FLOOR in got.reasons


def test_a_thin_day_is_refused_even_at_a_healthy_price() -> None:
    got = _assess("50.00", "100")
    assert UntradableReason.TOO_THIN in got.reasons
    assert UntradableReason.BELOW_PRICE_FLOOR not in got.reasons


def test_every_reason_is_reported_not_only_the_first() -> None:
    """A universe that empties under a price floor is a different problem from
    one that empties under a volume floor, and stopping at the first hides
    which."""
    got = _assess("0.10", "0", previous="0.10")
    assert set(got.reasons) == {
        UntradableReason.NO_VOLUME,
        UntradableReason.BELOW_PRICE_FLOOR,
        UntradableReason.TOO_THIN,
        UntradableReason.STALE_QUOTE,
    }


def test_a_flat_bar_that_actually_traded_is_not_stale() -> None:
    """A price that did not move but did trade is a real session. Only a bar
    that neither moved nor traded is a carried-forward quote."""
    got = _assess("25.00", "50000", previous="25.00")
    assert UntradableReason.STALE_QUOTE not in got.reasons
    assert got.tradable


def test_stale_detection_can_be_turned_off_for_studying_illiquidity() -> None:
    """ "Almost always wanted" is not "always"."""
    rule = TradabilityRule(
        min_price=Decimal("1.00"),
        min_dollar_volume=Decimal("0"),
        reject_stale_quotes=False,
    )
    got = assess_tradability(
        session_date=DAY,
        close=Decimal("25.00"),
        volume=Decimal("0"),
        previous_close=Decimal("25.00"),
        rule=rule,
    )
    assert UntradableReason.STALE_QUOTE not in got.reasons
    assert UntradableReason.NO_VOLUME in got.reasons


def test_the_rule_has_no_defaults() -> None:
    """A default threshold is a strategy parameter hiding in a signature, which
    is what ADR-0008 exists to prevent."""
    with pytest.raises(TypeError):
        TradabilityRule()  # type: ignore[call-arg]


def test_a_series_judges_each_day_against_its_own_predecessor() -> None:
    bars = [
        (dt.date(2020, 6, 1), Decimal("10.00"), Decimal("50000")),
        (dt.date(2020, 6, 2), Decimal("10.00"), Decimal("0")),
        (dt.date(2020, 6, 3), Decimal("11.00"), Decimal("50000")),
    ]
    got = tradable_days(bars, RULE)
    assert [t.tradable for t in got] == [True, False, True]
    assert UntradableReason.STALE_QUOTE in got[1].reasons


def test_the_first_bar_cannot_be_stale() -> None:
    """An absent predecessor is not evidence of a repeated price, and treating
    it as one would refuse the first day of every series."""
    bars = [(dt.date(2020, 6, 1), Decimal("10.00"), Decimal("0"))]
    got = tradable_days(bars, RULE)
    assert UntradableReason.STALE_QUOTE not in got[0].reasons
    assert UntradableReason.NO_VOLUME in got[0].reasons
