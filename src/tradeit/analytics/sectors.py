"""Sector strength and rotation.

Aggregates member-level features into a per-sector score. Two design points
carry most of the weight:

**Classification is point-in-time.** Members are resolved from the ``sectors``
interval table as it stood on the evaluation date. A company reclassified from
Information Technology to Communication Services in 2018 was in Information
Technology in 2017, and using today's mapping for all history measures a
different strategy from the one being claimed. The engine takes a
``members_by_sector`` roster for the date and cannot reach for a current
classification even if one exists.

**ETF proxies are a documented fallback, never a silent substitute.** Historical
GICS constituent data requires a commercial vendor. Until one is licensed, a
sector's strength can be measured from its SPDR ETF's own price history — which
is a legitimate point-in-time series — and the result is marked
``source="etf_proxy"`` so nothing downstream mistakes it for a constituent
aggregate. What the engine will *not* do is apply today's constituent list to
2015 and present the result as historical sector strength.

Scoring weights are configurable and deliberately not fitted to historical
returns during Phase 3.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

import numpy as np

from tradeit.analytics.registry import (
    FeatureKind,
    FeatureRegistry,
    FeatureSpec,
    NullBehaviour,
    OutputType,
)
from tradeit.core.enums import Bartimeframe
from tradeit.strategy.config import SectorStrengthConfig

SECTOR_INPUTS = ("ohlcv_bars", "sectors", "universe_memberships", "indicator_values")


class SectorSource(StrEnum):
    """How a sector's strength was measured. Never inferred -- always recorded."""

    #: Aggregated from constituents classified as of the evaluation date.
    CONSTITUENTS = "constituents"
    #: Derived from the sector ETF's own price history, because point-in-time
    #: constituent classification was unavailable.
    ETF_PROXY = "etf_proxy"
    #: Both available; constituents used, proxy retained for comparison.
    CONSTITUENTS_WITH_PROXY = "constituents_with_proxy"


@dataclass(frozen=True, slots=True)
class SectorMemberInput:
    """One constituent's contribution on one date."""

    instrument_id: int
    return_over: dict[int, float | None]
    above_ma: dict[int, bool | None]
    at_52w_high: bool | None = None
    at_52w_low: bool | None = None
    relative_performance_vs_market: float | None = None
    relative_volume: float | None = None


@dataclass(frozen=True, slots=True)
class SectorStrengthResult:
    """One sector on one date, with its evidence."""

    session_date: dt.date
    scheme: str
    sector: str
    source: SectorSource
    member_count: int
    evaluated_members: int

    absolute_return: dict[int, float | None] = field(default_factory=dict)
    relative_return: dict[int, float | None] = field(default_factory=dict)
    relative_momentum: float | None = None
    pct_above_ma: dict[int, float | None] = field(default_factory=dict)
    new_highs: int = 0
    new_lows: int = 0
    participation: float | None = None
    leadership_participation: float | None = None
    volume_ratio: float | None = None

    score: float | None = None
    rank: int | None = None
    trend: str = "unknown"
    breadth_status: str = "unknown"
    participation_status: str = "unknown"
    factor_contributions: dict[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Whether this result should influence a decision.

        A sector scored from three constituents is arithmetic, not a measure of
        the sector, so `min_members` gates it and this property makes the gate
        visible to callers rather than implicit in a null.
        """
        return self.score is not None and self.evaluated_members > 0


def _mean(values: Sequence[float | None]) -> float | None:
    finite = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.mean(finite)) if finite else None


def _fraction_true(values: Sequence[bool | None]) -> float | None:
    known = [v for v in values if v is not None]
    return (sum(known) / len(known)) if known else None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class SectorStrengthEngine:
    """Aggregates point-in-time sector members into strength scores."""

    def __init__(self, config: SectorStrengthConfig) -> None:
        self.config = config
        self.registry = self._build_registry(config)

    @staticmethod
    def _build_registry(config: SectorStrengthConfig) -> FeatureRegistry:
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
                    kind=FeatureKind.SECTOR_LEVEL,
                    timeframe=Bartimeframe.D1,
                    output_type=output,
                    null_behaviour=null,
                    input_datasets=SECTOR_INPUTS,
                    warmup_periods=warmup,
                    parameters=parameters,
                )
            )

        for lookback in config.momentum_lookbacks:
            spec(
                f"sector_absolute_return_{lookback}",
                f"Mean constituent return over {lookback} sessions.",
                OutputType.RATIO,
                lookback + 1,
                {"lookback": lookback},
            )
            spec(
                f"sector_relative_return_{lookback}",
                f"Sector return against the market over {lookback} sessions.",
                OutputType.RATIO,
                lookback + 1,
                {"lookback": lookback},
            )
        for period in config.breadth_ma_periods:
            spec(
                f"sector_pct_above_{period}dma",
                f"Fraction of point-in-time constituents above their {period}-session average.",
                OutputType.PERCENTILE_0_1,
                period,
                {"period": period},
            )
        spec(
            "sector_relative_momentum",
            "Acceleration of sector relative performance -- rotation in progress.",
            OutputType.RATIO,
            max(config.momentum_lookbacks) + 1,
            {"lookbacks": list(config.momentum_lookbacks)},
        )
        spec(
            "sector_participation",
            "Fraction of constituents advancing with the sector.",
            OutputType.PERCENTILE_0_1,
            2,
            {},
        )
        spec(
            "sector_leadership_participation",
            "Fraction of constituents at or near 52-week highs -- whether the move "
            "is broad or a handful of names.",
            OutputType.PERCENTILE_0_1,
            252,
            {},
        )
        spec(
            "sector_strength_score",
            "Weighted blend of sector factors, 0-100. Weights are configurable and "
            "were NOT fitted to historical returns.",
            OutputType.SCORE_0_100,
            max(max(config.momentum_lookbacks), max(config.breadth_ma_periods)),
            {"weights": config.normalised_factor_weights()},
        )
        spec(
            "sector_rank",
            "Rank among scored sectors on this date, 1 = strongest.",
            OutputType.COUNT,
            max(max(config.momentum_lookbacks), max(config.breadth_ma_periods)),
            {},
            NullBehaviour.NOT_APPLICABLE,
        )
        registry.validate()
        return registry

    # -- per-sector ----------------------------------------------------------

    def evaluate_sector(
        self,
        session_date: dt.date,
        sector: str,
        members: Sequence[SectorMemberInput],
        market_return: Mapping[int, float | None],
        *,
        source: SectorSource = SectorSource.CONSTITUENTS,
        member_count: int | None = None,
    ) -> SectorStrengthResult:
        """Aggregate one sector's point-in-time constituents.

        ``members`` must already be filtered to instruments classified into this
        sector **on ``session_date``**. The engine takes the roster rather than
        resolving it, so a caller cannot accidentally hand it today's mapping
        without that being visible at the call site.
        """
        notes: list[str] = []
        evaluated = len(members)
        total_members = member_count if member_count is not None else evaluated

        absolute: dict[int, float | None] = {}
        relative: dict[int, float | None] = {}
        for lookback in self.config.momentum_lookbacks:
            sector_return = _mean([m.return_over.get(lookback) for m in members])
            absolute[lookback] = sector_return
            benchmark = market_return.get(lookback)
            if sector_return is None or benchmark is None or benchmark <= -1.0:
                relative[lookback] = None
            else:
                relative[lookback] = (1.0 + sector_return) / (1.0 + benchmark) - 1.0

        pct_above = {
            period: _fraction_true([m.above_ma.get(period) for m in members])
            for period in self.config.breadth_ma_periods
        }

        lookbacks = sorted(self.config.momentum_lookbacks)
        momentum = None
        if len(lookbacks) >= 2:
            short, long = relative.get(lookbacks[0]), relative.get(lookbacks[-1])
            if short is not None and long is not None:
                # Short-horizon relative strength exceeding long-horizon means
                # the sector is gaining, not merely ahead.
                momentum = short - long

        advancing = [
            m.return_over.get(lookbacks[0])
            for m in members
            if m.return_over.get(lookbacks[0]) is not None
        ]
        participation = (
            sum(1 for r in advancing if r is not None and r > 0) / len(advancing)
            if advancing
            else None
        )
        leadership = _fraction_true([m.at_52w_high for m in members])
        volume_ratio = _mean([m.relative_volume for m in members])
        new_highs = sum(1 for m in members if m.at_52w_high)
        new_lows = sum(1 for m in members if m.at_52w_low)

        if total_members < self.config.min_members:
            notes.append(
                f"{total_members} classified members is below the "
                f"{self.config.min_members} minimum; the aggregate is noise"
            )
        if source is SectorSource.ETF_PROXY:
            notes.append(
                "measured from the sector ETF because point-in-time constituent "
                "classification was unavailable; not a constituent aggregate"
            )

        result = SectorStrengthResult(
            session_date=session_date,
            scheme=self.config.scheme,
            sector=sector,
            source=source,
            member_count=total_members,
            evaluated_members=evaluated,
            absolute_return=absolute,
            relative_return=relative,
            relative_momentum=momentum,
            pct_above_ma=pct_above,
            new_highs=new_highs,
            new_lows=new_lows,
            participation=participation,
            leadership_participation=leadership,
            volume_ratio=volume_ratio,
            notes=tuple(notes),
        )

        if total_members < self.config.min_members:
            return result

        score, contributions = self._score(result)
        # dataclasses.replace, not __dict__: these are slotted dataclasses and
        # have no instance dictionary.
        return replace(
            result,
            score=score,
            factor_contributions=contributions,
            trend=self._trend(result),
            breadth_status=self._breadth_status(result),
            participation_status=self._participation_status(result),
        )

    def _score(self, result: SectorStrengthResult) -> tuple[float | None, dict[str, float]]:
        """Blend factors into 0-100, renormalising over the factors available.

        Each factor is mapped to [0, 1] by a transparent, stated rule. None of
        these mappings were tuned on historical returns; they are documented
        starting points that Phase 9 can test.
        """
        weights = self.config.normalised_factor_weights()
        lookbacks = sorted(self.config.momentum_lookbacks)
        short = lookbacks[0]

        candidates: dict[str, float | None] = {
            # A relative return of +10% over the window maps to 1.0.
            "relative_return": _scale(result.relative_return.get(short), -0.10, 0.10),
            "absolute_return": _scale(result.absolute_return.get(short), -0.15, 0.15),
            "relative_momentum": _scale(result.relative_momentum, -0.05, 0.05),
            "breadth_above_50dma": result.pct_above_ma.get(50),
            "breadth_above_200dma": result.pct_above_ma.get(200),
            "participation": result.participation,
        }

        contributions: dict[str, float] = {}
        used_weight = 0.0
        for name, value in candidates.items():
            weight = weights.get(name)
            if weight is None or value is None:
                continue
            contributions[name] = _clamp01(value) * weight
            used_weight += weight

        if used_weight <= 0:
            return None, {}
        blended = sum(contributions.values()) / used_weight
        return round(blended * 100.0, 4), {
            name: round(value / used_weight, 6) for name, value in contributions.items()
        }

    def _trend(self, result: SectorStrengthResult) -> str:
        momentum = result.relative_momentum
        if momentum is None:
            return "unknown"
        if momentum > 0.02:
            return "improving"
        if momentum < -0.02:
            return "deteriorating"
        return "stable"

    def _breadth_status(self, result: SectorStrengthResult) -> str:
        above_50 = result.pct_above_ma.get(50)
        if above_50 is None:
            return "unknown"
        if above_50 >= 0.70:
            return "strong"
        if above_50 >= 0.45:
            return "mixed"
        return "weak"

    def _participation_status(self, result: SectorStrengthResult) -> str:
        """Distinguishes a broad advance from a handful of large names.

        A sector can post a strong return with a quarter of its members
        participating, and that is a materially different signal from the same
        return with three quarters participating.
        """
        participation = result.participation
        leadership = result.leadership_participation
        if participation is None:
            return "unknown"
        if participation >= 0.65 and (leadership is None or leadership >= 0.15):
            return "broad"
        if participation >= 0.45:
            return "moderate"
        return "narrow"

    # -- cross-sector --------------------------------------------------------

    def rank(self, results: Sequence[SectorStrengthResult]) -> list[SectorStrengthResult]:
        """Rank scored sectors, strongest first. Unscored sectors keep rank ``None``."""
        scored = [r for r in results if r.score is not None]
        unscored = [r for r in results if r.score is None]
        ordered = sorted(scored, key=lambda r: r.score or 0.0, reverse=True)

        ranked = [replace(r, rank=position) for position, r in enumerate(ordered, start=1)]
        return ranked + unscored


def _scale(value: float | None, low: float, high: float) -> float | None:
    """Map a value in [low, high] onto [0, 1], clamped outside."""
    if value is None or not np.isfinite(value) or high <= low:
        return None
    return _clamp01((value - low) / (high - low))
