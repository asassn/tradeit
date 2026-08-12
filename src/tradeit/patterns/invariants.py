"""What a detector's geometry must satisfy before anything downstream sees it.

Three real scans, three crashes, all the same shape: a detector emitted a
geometry the ``patterns`` table refuses, and the *database* was the first thing
to notice. That ordering is backwards. A check constraint can only abort a
run — it cannot say which structure was wrong or why, and it fires four layers
away from the code that produced the value.

So the invariants live here, they are checked once at the point every detected
pattern passes through, and the constraints stay as the backstop they were
meant to be.

**Checked against the persisted representation, not the in-memory floats.**
This is the part that matters and the part the first attempt got wrong. Prices
are stored as ``Numeric(18, 6)``, so ``persistence._as_decimal`` quantises them
to six decimal places on the way in. A guard written as ``support.level <
resistance.level`` on ``float`` therefore passes for a pair separated by 1e-7 —
and then both round to ``5.470000`` and PostgreSQL rejects the row. The real
scan of BBBY produced exactly that on an ``inverse_head_and_shoulders`` at
2026-02-10. Every price rule below quantises first, so what is validated is
what is written.

**The failures these encode are detector defects, not market facts.** A
consolidation whose support sits at or above its resistance is not a rare
market condition; it is a structure the detector has mis-measured. So the
structure is rejected and no pattern is emitted, which is the same outcome as
the detector never having found it — and the property tests in
``tests/unit/test_pattern_invariants.py`` are where a detector that produces
them regularly becomes visible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from tradeit.patterns.base import PatternGeometry

__all__ = [
    "PRICE_SCALE",
    "GeometryViolation",
    "check_geometry",
    "quantise_price",
]

#: Decimal places the ``patterns`` table stores a price at — ``Numeric(18, 6)``.
#: Every price comparison here is made after quantising to this, because two
#: values that differ below it are the *same value* once written.
PRICE_SCALE = 6


def quantise_price(value: float) -> Decimal:
    """A price as the database will hold it.

    Mirrors ``tradeit.patterns.persistence._as_decimal``. Duplicated on purpose
    rather than imported: persistence imports this module's callers, and a
    validator that reached back into the writer would make the dependency a
    cycle. The single test that asserts the two agree is cheaper than the
    indirection needed to share one function.
    """
    return Decimal(f"{value:.{PRICE_SCALE}f}")


@dataclass(frozen=True, slots=True)
class GeometryViolation:
    """One invariant a geometry failed, and the numbers behind it."""

    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


def check_geometry(
    geometry: PatternGeometry,
    *,
    invalidation: float | None = None,
    detector: str = "",
) -> list[GeometryViolation]:
    """Every invariant the persisted row must satisfy, checked in one place.

    Returns the violations rather than raising: the caller's response is to
    drop the structure, and an exception would turn one mis-measured pattern
    into an aborted scan of 4,000 sessions.

    ``detector`` is only for the message.
    """
    violations: list[GeometryViolation] = []
    resistance = geometry.resistance
    support = geometry.support
    prefix = f"{detector}: " if detector else ""

    # -- finite, positive prices --------------------------------------------
    # A NaN reaches the database as NULL or as an error depending on the
    # driver, and either way every comparison against it is silently false.
    named: list[tuple[str, float]] = []
    if resistance is not None:
        named.append(("resistance", resistance.level))
    if support is not None:
        named.append(("support", support.level))
    if invalidation is not None:
        named.append(("invalidation", invalidation))
    for name, point in geometry.key_points.items():
        named.append((f"key_point[{name}]", point.price))

    for name, value in named:
        if not math.isfinite(value):
            violations.append(GeometryViolation("finite_prices", f"{prefix}{name} is {value}"))
        elif value <= 0:
            violations.append(
                GeometryViolation(
                    "positive_prices",
                    f"{prefix}{name} is {value}; a traded price is above zero",
                )
            )

    # -- support strictly below resistance -----------------------------------
    if resistance is not None and support is not None:
        low, high = quantise_price(support.level), quantise_price(resistance.level)
        if math.isfinite(support.level) and math.isfinite(resistance.level) and low >= high:
            violations.append(
                GeometryViolation(
                    "boundaries_ordered",
                    f"{prefix}support {low} is not below resistance {high} once "
                    f"quantised to {PRICE_SCALE} decimal places"
                    + (
                        " — the boundaries are the same line, so the structure has "
                        "no height and every measurement drawn from it is zero or "
                        "undefined"
                        if low == high
                        else " — the boundaries are inverted, so the structure "
                        "described is not a consolidation"
                    ),
                )
            )

    # -- invalidation is checked for being a *price*, and no further ---------
    #
    # It is deliberately not required to sit below the support boundary, and
    # that decision is measured rather than assumed: across ~1,050 structures
    # over three price regimes, `ascending_triangle` (25) and `bull_flag` (23)
    # place it above the drawn support line, and both are coherent. A bull
    # flag invalidates when price closes below the *flag* low, while its
    # support boundary is clustered from swing lows spanning the pole as well,
    # which can sit lower. The pattern fails before that line is reached.
    #
    # The database constrains no such relationship either. Inventing one here
    # would have rejected 48 legitimate structures to satisfy a rule nobody
    # stated, which is the opposite of what this module is for.

    # -- coherent structural dates -------------------------------------------
    if geometry.end_date < geometry.start_date:
        violations.append(
            GeometryViolation(
                "structural_dates",
                f"{prefix}structure ends {geometry.end_date} before it starts "
                f"{geometry.start_date}",
            )
        )
    # A segment is *not* required to lie inside the structure's own span, which
    # was the second rule this module tried to invent and the second one the
    # detectors were right about. `breakout_retest` names its `level` segment
    # from the level's own first formation, which necessarily precedes the
    # first confirmed touch the structure starts at — 2023-04-03 against a
    # structural start of 2023-04-05 in the corpus. The span is provenance for
    # where a boundary came from, not a claim about the structure's extent.
    for name, (start, end) in geometry.segments.items():
        if end < start:
            violations.append(
                GeometryViolation(
                    "segment_dates",
                    f"{prefix}segment {name!r} ends {end} before it starts {start}",
                )
            )

    return violations
