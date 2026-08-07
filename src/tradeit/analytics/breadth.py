"""Market breadth.

Breadth answers "how many things are participating?", and the answer is
meaningless without knowing **how many things were counted**. That is the whole
design constraint here: every breadth measure carries the roster it was computed
over, and the roster is the point-in-time eligible universe for that date.

The failure this prevents is the most common bias in breadth work. Compute
"percentage of stocks above their 200-day average" for 2008 using today's index
constituents, and you have computed it over the companies that *survived* 2008.
The number comes out far too high, the 2008 bear market looks mild in the
breadth series, and any regime model calibrated on it will fail exactly when it
matters. The roster must include the companies that went to zero.

``BreadthSnapshot.universe_size`` and ``universe_digest`` exist so that a
historical breadth value can be audited: 68% of *what*, exactly?
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from tradeit.analytics.registry import (
    FeatureKind,
    FeatureRegistry,
    FeatureSpec,
    NullBehaviour,
    OutputType,
)
from tradeit.core.enums import Bartimeframe
from tradeit.errors import DataError
from tradeit.reproducibility.versioning import content_hash
from tradeit.strategy.config import BreadthConfig

BREADTH_INPUTS = ("ohlcv_bars", "universe_memberships", "indicator_values")


@dataclass(frozen=True, slots=True)
class InstrumentBreadthInput:
    """One instrument's contribution to a date's breadth.

    Deliberately a narrow value object rather than a full feature vector: it
    makes the breadth computation's dependencies explicit and testable, and it
    keeps the memory footprint of a 4,000-name cross-section small.
    """

    instrument_id: int
    close: float
    previous_close: float | None
    volume: float
    above_ma: dict[int, bool | None]
    at_52w_high: bool | None
    at_52w_low: bool | None

    @property
    def advanced(self) -> bool | None:
        if self.previous_close is None or self.previous_close <= 0:
            return None
        return self.close > self.previous_close

    @property
    def declined(self) -> bool | None:
        if self.previous_close is None or self.previous_close <= 0:
            return None
        return self.close < self.previous_close


@dataclass(frozen=True, slots=True)
class BreadthSnapshot:
    """Breadth for one date, with the roster it was computed over.

    ``universe_size`` and ``universe_digest`` are not metadata -- they are part
    of the measurement. A breadth series whose universe silently changed size is
    not a series, and comparing 2008 (computed over 500 survivors) with 2024
    (computed over 4,000 names) is comparing two different statistics.
    """

    session_date: dt.date
    universe_name: str
    universe_size: int
    universe_digest: str
    #: Instruments with enough data to contribute. Always <= universe_size.
    evaluated: int

    advances: int
    declines: int
    unchanged: int
    advance_volume: float
    decline_volume: float
    pct_above_ma: dict[int, float | None] = field(default_factory=dict)
    new_highs: int = 0
    new_lows: int = 0
    ad_line_delta: int = 0
    thrust_ratio: float | None = None
    low_confidence: bool = False
    notes: tuple[str, ...] = ()

    @property
    def advance_decline_ratio(self) -> float | None:
        if self.declines == 0:
            return None if self.advances == 0 else float("inf")
        return self.advances / self.declines

    @property
    def advance_decline_spread(self) -> int:
        return self.advances - self.declines

    @property
    def up_down_volume_ratio(self) -> float | None:
        if self.decline_volume <= 0:
            return None
        return self.advance_volume / self.decline_volume

    @property
    def high_low_spread(self) -> int:
        return self.new_highs - self.new_lows

    @property
    def participation(self) -> float | None:
        """Fraction of evaluated instruments that advanced."""
        contributing = self.advances + self.declines + self.unchanged
        if contributing == 0:
            return None
        return self.advances / contributing


def universe_digest(instrument_ids: Sequence[int]) -> str:
    """Content hash of a roster, so two breadth values can be compared honestly."""
    return content_hash({"instruments": sorted(instrument_ids)})


class BreadthEngine:
    """Computes breadth over an explicitly supplied point-in-time universe."""

    def __init__(self, config: BreadthConfig) -> None:
        self.config = config
        self.registry = self._build_registry(config)

    @staticmethod
    def _build_registry(config: BreadthConfig) -> FeatureRegistry:
        registry = FeatureRegistry()

        def spec(
            name: str,
            description: str,
            output: OutputType,
            warmup: int,
            parameters: dict[str, object],
            null: NullBehaviour = NullBehaviour.WARMUP,
        ) -> None:
            registry.register(
                FeatureSpec(
                    name=name,
                    version=1,
                    description=description,
                    kind=FeatureKind.MARKET_LEVEL,
                    timeframe=Bartimeframe.D1,
                    output_type=output,
                    null_behaviour=null,
                    input_datasets=BREADTH_INPUTS,
                    warmup_periods=warmup,
                    parameters=parameters,
                )
            )

        spec("breadth_advances", "Instruments closing up.", OutputType.COUNT, 2, {})
        spec("breadth_declines", "Instruments closing down.", OutputType.COUNT, 2, {})
        spec(
            "breadth_ad_spread",
            "Advances minus declines.",
            OutputType.COUNT,
            2,
            {},
        )
        spec(
            "breadth_ad_line",
            "Cumulative advance-decline line. Like OBV, only its slope carries "
            "information -- the level depends on where the series starts.",
            OutputType.COUNT,
            2,
            {},
        )
        spec(
            "breadth_up_down_volume_ratio",
            "Advancing volume over declining volume.",
            OutputType.RATIO,
            2,
            {},
            NullBehaviour.UNDEFINED,
        )
        for period in config.ma_periods:
            spec(
                f"breadth_pct_above_{period}dma",
                f"Fraction of the eligible universe above its {period}-session average.",
                OutputType.PERCENTILE_0_1,
                period,
                {"period": period},
            )
        spec(
            "breadth_new_highs",
            f"Instruments at a {config.new_high_low_lookback}-session high.",
            OutputType.COUNT,
            config.new_high_low_lookback,
            {"lookback": config.new_high_low_lookback},
        )
        spec(
            "breadth_new_lows",
            f"Instruments at a {config.new_high_low_lookback}-session low.",
            OutputType.COUNT,
            config.new_high_low_lookback,
            {"lookback": config.new_high_low_lookback},
        )
        spec(
            "breadth_high_low_spread",
            "New highs minus new lows.",
            OutputType.COUNT,
            config.new_high_low_lookback,
            {"lookback": config.new_high_low_lookback},
        )
        spec(
            "breadth_thrust",
            "Advances as a fraction of advances plus declines, averaged over a "
            "short window. Sustained extreme readings mark thrusts.",
            OutputType.PERCENTILE_0_1,
            config.thrust_lookback,
            {"lookback": config.thrust_lookback},
        )
        spec(
            "breadth_participation",
            "Fraction of evaluated instruments advancing.",
            OutputType.PERCENTILE_0_1,
            2,
            {},
        )
        registry.validate()
        return registry

    def compute(
        self,
        session_date: dt.date,
        universe_name: str,
        eligible_universe: Sequence[int],
        inputs: Mapping[int, InstrumentBreadthInput],
    ) -> BreadthSnapshot:
        """Compute breadth for one date over one explicit roster.

        Instruments in ``inputs`` but not in ``eligible_universe`` are refused
        rather than ignored. Silently including one would mean a breadth value
        computed over a universe other than the one recorded alongside it, which
        defeats the audit trail the snapshot exists to provide.
        """
        roster = list(dict.fromkeys(eligible_universe))
        roster_set = set(roster)

        stray = set(inputs) - roster_set
        if stray:
            raise DataError(
                f"breadth inputs supplied for {len(stray)} instrument(s) outside the "
                f"eligible universe on {session_date}: {sorted(stray)[:5]}. Breadth must "
                "be computed over exactly the roster it records."
            )

        advances = declines = unchanged = 0
        advance_volume = decline_volume = 0.0
        new_highs = new_lows = 0
        evaluated = 0
        above_counts: dict[int, int] = dict.fromkeys(self.config.ma_periods, 0)
        above_evaluated: dict[int, int] = dict.fromkeys(self.config.ma_periods, 0)

        for instrument_id in roster:
            record = inputs.get(instrument_id)
            if record is None:
                continue
            evaluated += 1

            if record.advanced is True:
                advances += 1
                advance_volume += record.volume
            elif record.declined is True:
                declines += 1
                decline_volume += record.volume
            elif record.advanced is False and record.declined is False:
                unchanged += 1

            for period in self.config.ma_periods:
                state = record.above_ma.get(period)
                if state is None:
                    continue
                above_evaluated[period] += 1
                if state:
                    above_counts[period] += 1

            if record.at_52w_high:
                new_highs += 1
            if record.at_52w_low:
                new_lows += 1

        pct_above = {
            period: (above_counts[period] / above_evaluated[period])
            if above_evaluated[period] > 0
            else None
            for period in self.config.ma_periods
        }

        notes: list[str] = []
        low_confidence = len(roster) < self.config.min_universe_size
        if low_confidence:
            notes.append(
                f"universe of {len(roster)} is below the {self.config.min_universe_size} "
                "minimum; breadth percentages are noisy at this size"
            )
        if evaluated < len(roster):
            notes.append(
                f"{len(roster) - evaluated} of {len(roster)} eligible instruments had no "
                "usable data and did not contribute"
            )

        return BreadthSnapshot(
            session_date=session_date,
            universe_name=universe_name,
            universe_size=len(roster),
            universe_digest=universe_digest(roster),
            evaluated=evaluated,
            advances=advances,
            declines=declines,
            unchanged=unchanged,
            advance_volume=advance_volume,
            decline_volume=decline_volume,
            pct_above_ma=pct_above,
            new_highs=new_highs,
            new_lows=new_lows,
            ad_line_delta=advances - declines,
            low_confidence=low_confidence,
            notes=tuple(notes),
        )

    @staticmethod
    def advance_decline_line(snapshots: Sequence[BreadthSnapshot]) -> list[int]:
        """Cumulative A/D line from a chronological run of snapshots.

        Like OBV, the *level* is arbitrary — it depends entirely on where the
        series starts — so only the slope and its divergence from price carry
        information. Returned so those can be computed, not to be read directly.
        """
        total = 0
        out: list[int] = []
        for snapshot in snapshots:
            total += snapshot.ad_line_delta
            out.append(total)
        return out

    def thrust(self, snapshots: Sequence[BreadthSnapshot]) -> float | None:
        """Mean advancing share over the trailing thrust window.

        Uses only the snapshots supplied, so a caller passing a chronological
        run ending at the evaluation date cannot reach forward.
        """
        window = snapshots[-self.config.thrust_lookback :]
        if len(window) < self.config.thrust_lookback:
            return None
        ratios = []
        for snapshot in window:
            contributing = snapshot.advances + snapshot.declines
            if contributing > 0:
                ratios.append(snapshot.advances / contributing)
        return float(np.mean(ratios)) if ratios else None
