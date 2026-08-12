"""Detector geometry is validated before persistence, not by the database.

Three real scans crashed on a ``patterns`` check constraint. The last one was
an ``inverse_head_and_shoulders`` on BBBY at 2026-02-10 with resistance and
support both ``5.470000`` — a zero-height structure. A constraint can only
abort a run; it cannot name the structure or the rule.

The subtle part, and the one the first attempt got wrong: prices are stored as
``Numeric(18, 6)``, so a guard written on ``float`` passes for two values a
nanocent apart and the database still refuses them once quantised.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import numpy as np
import pytest

from tradeit.core.enums import Bartimeframe
from tradeit.core.models import OhlcvBar
from tradeit.patterns.base import Boundary, PatternGeometry, PricePoint
from tradeit.patterns.config import PatternEngineConfig
from tradeit.patterns.invariants import PRICE_SCALE, check_geometry, quantise_price
from tradeit.patterns.persistence import _as_decimal
from tradeit.patterns.registry import DetectorRegistry

#: The values the real BBBY inverse head-and-shoulders persisted.
FAILED_RESISTANCE = 5.470000
FAILED_SUPPORT = 5.470000
FAILED_INVALIDATION = 5.415300
STRUCTURAL_START = dt.date(2025, 11, 7)
STRUCTURAL_END = dt.date(2026, 2, 10)


def geometry(
    *,
    resistance: float | None = 10.0,
    support: float | None = 8.0,
    start: dt.date = STRUCTURAL_START,
    end: dt.date = STRUCTURAL_END,
    segments: dict[str, tuple[dt.date, dt.date]] | None = None,
    key_points: dict[str, PricePoint] | None = None,
) -> PatternGeometry:
    return PatternGeometry(
        start_date=start,
        end_date=end,
        segments=segments or {},
        resistance=(
            None
            if resistance is None
            else Boundary(kind="resistance", method="neckline", level=resistance, anchor_date=end)
        ),
        support=(
            None
            if support is None
            else Boundary(kind="support", method="single_extreme", level=support, anchor_date=start)
        ),
        key_points=key_points or {},
    )


class TestTheExactFailureFromTheScan:
    def test_the_bbby_inverse_head_and_shoulders_geometry_is_rejected(self) -> None:
        violations = check_geometry(
            geometry(resistance=FAILED_RESISTANCE, support=FAILED_SUPPORT),
            invalidation=FAILED_INVALIDATION,
            detector="inverse_head_and_shoulders",
        )
        assert [v.rule for v in violations] == ["boundaries_ordered"]
        assert "no height" in violations[0].detail
        assert "inverse_head_and_shoulders" in violations[0].detail

    def test_a_float_only_guard_would_have_let_it_through(self) -> None:
        """Why the rules quantise first.

        Two prices a nanocent apart satisfy ``support < resistance`` on floats
        and become one price at ``Numeric(18, 6)``. That is the exact gap the
        earlier ``boundaries_are_ordered`` guard left open, and the database
        found it 3,500 sessions into BBBY.
        """
        support, resistance = 5.4699999, 5.4700001
        assert support < resistance, "the naive float guard passes"
        assert quantise_price(support) == quantise_price(resistance) == Decimal("5.470000")
        violations = check_geometry(geometry(resistance=resistance, support=support), detector="x")
        assert [v.rule for v in violations] == ["boundaries_ordered"]

    def test_the_validator_quantises_exactly_as_persistence_does(self) -> None:
        """The two must agree or the validator is checking a different number."""
        for value in (5.47, 5.4699999, 0.005, 1234.5678915, 1e-7):
            assert quantise_price(value) == _as_decimal(value)
        assert PRICE_SCALE == 6


class TestTheRules:
    def test_a_healthy_geometry_passes(self) -> None:
        assert check_geometry(geometry(), invalidation=7.5) == []

    def test_inverted_boundaries_are_rejected(self) -> None:
        violations = check_geometry(geometry(resistance=8.0, support=10.0))
        assert [v.rule for v in violations] == ["boundaries_ordered"]
        assert "inverted" in violations[0].detail

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_prices_are_rejected(self, bad: float) -> None:
        rules = {v.rule for v in check_geometry(geometry(resistance=bad))}
        assert "finite_prices" in rules

    def test_a_nan_never_slips_through_the_ordering_comparison(self) -> None:
        # Every comparison against NaN is false, so an ordering rule alone
        # would have said nothing about this geometry.
        assert not (float("nan") > 8.0)
        rules = {v.rule for v in check_geometry(geometry(resistance=float("nan"), support=8.0))}
        assert rules == {"finite_prices"}

    @pytest.mark.parametrize("bad", [0.0, -1.5])
    def test_non_positive_prices_are_rejected(self, bad: float) -> None:
        rules = {v.rule for v in check_geometry(geometry(support=bad))}
        assert "positive_prices" in rules

    def test_a_non_finite_key_point_is_rejected(self) -> None:
        rules = {
            v.rule
            for v in check_geometry(
                geometry(key_points={"head": PricePoint(STRUCTURAL_START, float("nan"))})
            )
        }
        assert "finite_prices" in rules

    def test_a_structure_ending_before_it_starts_is_rejected(self) -> None:
        violations = check_geometry(geometry(start=STRUCTURAL_END, end=STRUCTURAL_START))
        assert [v.rule for v in violations] == ["structural_dates"]

    def test_a_segment_ending_before_it_starts_is_rejected(self) -> None:
        violations = check_geometry(geometry(segments={"head": (STRUCTURAL_END, STRUCTURAL_START)}))
        assert [v.rule for v in violations] == ["segment_dates"]

    def test_a_missing_boundary_is_not_a_violation(self) -> None:
        # Not every pattern draws both lines, and the column is nullable.
        assert check_geometry(geometry(resistance=None)) == []
        assert check_geometry(geometry(support=None)) == []

    def test_invalidation_is_not_required_to_sit_below_support(self) -> None:
        """Measured, not assumed. See the module comment.

        A bull flag invalidates when price closes below the *flag* low, while
        its support boundary clusters swing lows spanning the pole as well.
        Requiring one below the other rejected 48 legitimate structures.
        """
        assert check_geometry(geometry(support=8.0), invalidation=9.0) == []


# ---------------------------------------------------------------------------
# Every detector, over real-shaped series
# ---------------------------------------------------------------------------

SESSIONS_START, SESSIONS_END = dt.date(2019, 1, 2), dt.date(2022, 12, 30)


def series(seed: int, level: float) -> list[OhlcvBar]:
    """Tick-quantized bars at a given price level, like a real vendor series."""
    from tradeit.core.calendar import get_calendar

    days = get_calendar().sessions_between(SESSIONS_START, SESSIONS_END)
    rng = np.random.default_rng(seed)
    tick = Decimal("0.01")
    out: list[OhlcvBar] = []
    price = level
    for index, day in enumerate(days):
        price = max(price * (1.0 + rng.normal(0.0, 0.017)), 0.5)
        close = (Decimal(str(price)) / tick).quantize(Decimal("1")) * tick
        span = Decimal(str(abs(rng.normal(0.0, 0.012)) * price))
        high = ((close + span) / tick).quantize(Decimal("1")) * tick
        low = ((close - span) / tick).quantize(Decimal("1")) * tick
        low = max(min(low, close), tick)
        high = max(high, close)
        when = dt.datetime.combine(day, dt.time(21), tzinfo=dt.UTC)
        out.append(
            OhlcvBar(
                instrument_id=1,
                timeframe=Bartimeframe.D1,
                session_date=day,
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1_000_000 + index % 37 * 997,
                event_time=when,
                knowledge_time=when,
                knowledge_source="vendor_ingest",
            )
        )
    return out


#: Three price regimes. The penny one matters: the failure was on a $5.47
#: stock, where a tick is a much larger fraction of the price and boundaries
#: collapse onto each other far more readily than on a large-cap.
REGIMES = ((3, 180.0), (5, 5.5), (9, 12.0))


@pytest.mark.parametrize("detector_name", sorted(DetectorRegistry.from_config().entries))
def test_every_detector_emits_only_persistable_geometry(detector_name: str) -> None:
    """The property, over every enabled detector and three price regimes.

    This is what makes the shared validator an architecture rather than three
    one-off patches: a new detector is covered the day it is registered.
    """
    registry = DetectorRegistry.from_config(PatternEngineConfig())
    entry = registry.entries[detector_name]
    if not entry.enabled or not entry.supports(Bartimeframe.D1):
        pytest.skip(f"{detector_name} is not enabled on daily bars")
    detector = registry.detector(detector_name, Bartimeframe.D1)

    emitted = 0
    for seed, level in REGIMES:
        bars = series(seed, level)
        for cut in range(400, len(bars), 60):
            window = bars[: cut + 1]
            if len(window) < entry.minimum_bars:
                continue
            for instance in detector.detect(window, window[-1].session_date):
                emitted += 1
                violations = check_geometry(
                    instance.geometry,
                    invalidation=instance.invalidation_price,
                    detector=detector_name,
                )
                assert violations == [], f"{detector_name} emitted {violations}"

                # And the persisted representation itself, which is what the
                # database will apply its constraint to.
                resistance = _as_decimal(instance.resistance_price)
                support = _as_decimal(instance.support_price)
                if resistance is not None and support is not None:
                    assert support < resistance, (
                        f"{detector_name}: {support} !< {resistance} after quantisation — "
                        "ck_pattern_support_below_resistance would reject this"
                    )
                assert instance.geometry.end_date >= instance.geometry.start_date
                for value in (instance.resistance_price, instance.support_price):
                    assert value is None or math.isfinite(value)
    # A detector finding nothing in this corpus is not a failure — but the
    # corpus as a whole must exercise the property, which
    # `test_the_corpus_is_not_vacuous` asserts.
    _EMITTED[detector_name] = emitted


#: Filled by the parametrised test above, read by the guard below.
_EMITTED: dict[str, int] = {}


def test_the_corpus_is_not_vacuous() -> None:
    """Guards the property test against silently proving nothing.

    A corpus in which no detector fires would pass every assertion above while
    checking no geometry at all — which is exactly how the original defect
    survived: the synthetic fixtures never produced a boundary collapse.
    """
    registry = DetectorRegistry.from_config(PatternEngineConfig())
    enabled = {
        name
        for name, entry in registry.entries.items()
        if entry.enabled and entry.supports(Bartimeframe.D1)
    }
    assert _EMITTED, "the parametrised test must run before this one"
    assert set(_EMITTED) >= enabled, "every enabled detector must have been exercised"
    assert sum(_EMITTED.values()) > 100, (
        f"only {sum(_EMITTED.values())} patterns across {len(_EMITTED)} detectors; "
        "the corpus is too quiet to prove anything"
    )
