"""Money and quantity arithmetic.

Prices and cash are :class:`~decimal.Decimal`. Floats are fine for indicators
and scores, where a 1e-15 error is noise, but they are not fine for a cash
ledger that must reconcile to the cent after ten thousand fills.

Share quantities are also Decimal because fractional-share brokers exist and
``0.1 + 0.2`` shares should not drift.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

from tradeit.errors import DataError

#: Storage precision for prices. NUMERIC(18, 6) in PostgreSQL -- six decimals
#: covers sub-penny quotes without inviting float-style noise.
PRICE_QUANTUM: Final = Decimal("0.000001")

#: Cash is tracked to the cent; brokers reconcile at that granularity.
CASH_QUANTUM: Final = Decimal("0.01")

#: Fractional shares to six places matches the finest broker granularity in use.
QTY_QUANTUM: Final = Decimal("0.000001")

ZERO: Final = Decimal("0")


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    """Convert to Decimal, routing floats through ``str`` to avoid binary dust.

    ``Decimal(0.1)`` is 0.1000000000000000055511151231257827; ``Decimal("0.1")``
    is 0.1. Vendor payloads arrive as JSON floats, so this matters in practice.
    """
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DataError(f"cannot interpret {value!r} as a decimal number") from exc


def quantize_price(value: Decimal | int | float | str) -> Decimal:
    return to_decimal(value).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)


def quantize_cash(value: Decimal | int | float | str) -> Decimal:
    return to_decimal(value).quantize(CASH_QUANTUM, rounding=ROUND_HALF_EVEN)


def quantize_qty(value: Decimal | int | float | str) -> Decimal:
    return to_decimal(value).quantize(QTY_QUANTUM, rounding=ROUND_HALF_EVEN)


def pct_change(new: Decimal, old: Decimal) -> Decimal:
    """Fractional change from ``old`` to ``new`` (0.05 == +5%)."""
    if old == ZERO:
        raise DataError("percent change from a zero base is undefined")
    return (new - old) / old


def basis_points(fraction: Decimal) -> Decimal:
    """Convert a fraction (0.0015) to basis points (15)."""
    return fraction * Decimal(10_000)
