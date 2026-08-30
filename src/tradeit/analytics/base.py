"""Interfaces for indicator and feature computation.

Nothing here computes anything. These are the contracts Phase 3 implements
against, and the two rules they encode are the ones that decide whether the
whole analytics layer is trustworthy.

**Causality.** The value at bar *t* may depend on bars ≤ *t* and nothing else.
This sounds obvious and is violated constantly, usually by respectable-looking
operations: a centred moving average, a z-score computed over the full sample, a
min-max normalisation whose bounds come from the whole series, a "percentile
rank within the universe" computed once over all history. Each of these leaks
the future into the past in a way that looks like a legitimate transform.

**Warm-up.** An indicator with insufficient history returns ``None``, not a
number computed from a short window. A 200-day moving average of 40 bars is not
a 200-day moving average; it is a different indicator that happens to have the
same name, and it will systematically differ from what a live system computes.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from tradeit.core.models import OhlcvBar


@dataclass(frozen=True, slots=True)
class IndicatorValue:
    """One indicator observation, tied to the bar that produced it.

    ``session_date`` is the bar's date, not the computation date. ``is_warm``
    distinguishes "this indicator has enough history to be meaningful" from
    "this indicator happens to be null today", which are different problems.
    """

    session_date: dt.date
    value: float | None
    is_warm: bool

    @property
    def usable(self) -> bool:
        return self.is_warm and self.value is not None


@runtime_checkable
class Indicator(Protocol):
    """A causal transform from a bar series to a value series.

    Implementations must satisfy the property test in
    ``tests/unit/test_causality.py``: computing an indicator over ``bars[:k]``
    must produce exactly the first *k* values of computing it over all bars, for
    every *k*. An indicator that fails this leaks the future, whatever its
    docstring claims.
    """

    name: str

    @property
    def warmup_periods(self) -> int:
        """Bars required before the first usable value.

        Callers use this to request enough history. An indicator that needs 200
        bars and is handed 200 bars produces exactly one usable value.
        """
        ...

    @property
    def parameters(self) -> dict[str, object]:
        """The indicator's configuration, for the feature-set version hash.

        Two indicators with the same name and different parameters are
        different features and must hash differently.
        """
        ...

    def compute(self, bars: Sequence[OhlcvBar]) -> list[IndicatorValue]:
        """Compute over a chronologically ordered series.

        Returns one value per input bar, in the same order, with
        ``is_warm=False`` for the leading warm-up region. Returning a shorter
        list is not permitted: alignment bugs between a bar series and a
        truncated indicator series are a recurring source of off-by-one leakage.
        """
        ...


@runtime_checkable
class CrossSectionalFeature(Protocol):
    """A feature whose value depends on the whole universe on one date.

    Relative strength and percentile ranks live here, and they are separated
    from :class:`Indicator` because their leakage mode is different. An
    indicator leaks across *time*; a cross-sectional feature leaks across
    *instruments* — most commonly by ranking against today's universe rather
    than the universe as it stood on the date being ranked, which quietly
    excludes every company that later delisted.

    Implementations must therefore rank within the point-in-time universe
    supplied to them, never against a list fetched independently.
    """

    name: str

    def compute(
        self,
        session_date: dt.date,
        values_by_instrument: dict[int, float],
    ) -> dict[int, float]:
        """Rank or normalise one date's cross-section.

        ``values_by_instrument`` contains only instruments in the universe on
        ``session_date``. Instruments absent from the input must be absent from
        the output rather than defaulted.
        """
        ...


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """All features for one instrument on one date.

    The unit the scoring layer consumes and the trade journal records. Storing
    the vector — not just the final score — is what makes a recommendation
    explainable after the fact: "why did this rank third in March?" is
    answerable only if March's inputs were kept.
    """

    instrument_id: int
    session_date: dt.date
    features: dict[str, float | None]
    feature_set_digest: str

    def get(self, name: str) -> float | None:
        return self.features.get(name)

    @property
    def complete(self) -> bool:
        """Whether every feature has a value.

        Incomplete vectors are not an error — a young listing genuinely has no
        200-day average — but they must not be silently zero-filled. Zero is a
        meaningful value for most features, and imputing it turns missing data
        into a strong signal.
        """
        return all(v is not None for v in self.features.values())

    def missing(self) -> list[str]:
        return sorted(name for name, value in self.features.items() if value is None)


@runtime_checkable
class RegimeClassifier(Protocol):
    """Classifies the market environment from benchmark data.

    Kept deliberately separate from per-instrument analytics: regime is a
    property of the market, computed once per date and shared, rather than
    something each instrument recomputes.
    """

    name: str

    @property
    def warmup_periods(self) -> int: ...

    def classify(self, benchmark_bars: Sequence[OhlcvBar]) -> IndicatorValue: ...
