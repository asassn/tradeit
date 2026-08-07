"""Relative strength: benchmark-relative and cross-sectional.

**This is not RSI.** RSI is a momentum oscillator on a single series and lives
in the indicator engine. Relative strength here is a market-structure measure:
how a security performs against benchmarks and against its peers. The names
collide constantly in trading literature and the confusion produces real bugs,
so the distinction is stated in every docstring that touches it.

Two families of measure, with different leakage modes:

*Benchmark-relative* compares one security to one benchmark over a lookback. It
leaks across **time** if either series is not clock-gated, and the indicator
kernels already guard that.

*Cross-sectional* ranks a security against its peers on one date. It leaks
across **instruments** — and this is the subtle one. Ranking against today's
universe rather than the universe as it stood on the ranking date silently drops
every company that later delisted, which are disproportionately the losers. A
relative-strength rank computed that way is biased upward for every survivor,
and the bias is invisible in the output.

So the ranking function here takes an explicit eligible-universe roster for the
date and refuses to rank against anything else. The roster comes from
``UniverseMembership`` intervals (Phase 1), which include the casualties.
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
from tradeit.strategy.config import RelativeStrengthConfig

RS_INPUTS = ("ohlcv_bars", "corporate_actions", "universe_memberships")


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    """One security against one benchmark over one lookback.

    Both a ratio measure and a difference measure are kept because they answer
    different questions and disagree in the cases that matter. Over a period
    when the benchmark fell 20% and the stock fell 10%, the excess return is
    +10 points while the relative performance is +12.5% — the second is the
    compounding-correct statement and the first is the one most people mean.
    """

    benchmark: str
    lookback: int
    security_return: float
    benchmark_return: float
    #: (1 + r_security) / (1 + r_benchmark) - 1. Compounding-correct.
    relative_performance: float
    #: r_security - r_benchmark. Simple difference, in return points.
    excess_return: float
    #: Slope of the ratio line over the lookback: is outperformance building?
    relative_trend: float
    #: Ratio now against the ratio at the lookback midpoint. Recent acceleration.
    relative_momentum: float

    @property
    def outperforming(self) -> bool:
        return self.relative_performance > 0


@dataclass(frozen=True, slots=True)
class RelativeStrengthResult:
    """Everything the engine produces for one security on one date."""

    instrument_id: int
    session_date: dt.date
    comparisons: dict[tuple[str, int], BenchmarkComparison]
    universe_percentile: dict[int, float | None] = field(default_factory=dict)
    sector_percentile: dict[int, float | None] = field(default_factory=dict)
    industry_percentile: dict[int, float | None] = field(default_factory=dict)
    rs_score: float | None = None
    #: Instruments the percentile was computed against, per lookback. Recorded
    #: so a historical rank can be audited: "ranked 8th of what, exactly?"
    ranking_universe_size: dict[int, int] = field(default_factory=dict)

    def against(self, benchmark: str, lookback: int) -> BenchmarkComparison | None:
        return self.comparisons.get((benchmark, lookback))

    def beats_all_benchmarks(self, lookback: int) -> bool:
        relevant = [c for (b, lb), c in self.comparisons.items() if lb == lookback]
        return bool(relevant) and all(c.outperforming for c in relevant)


def _total_return(prices: Sequence[float], lookback: int) -> float | None:
    """Fractional return over the trailing ``lookback`` bars.

    Requires ``lookback + 1`` observations: a 20-session return spans 21 closes.
    Off-by-one here silently shifts every relative measure by a session.
    """
    if len(prices) < lookback + 1:
        return None
    start, end = prices[-(lookback + 1)], prices[-1]
    if start <= 0:
        return None
    return end / start - 1.0


def compare_to_benchmark(
    security_closes: Sequence[float],
    benchmark_closes: Sequence[float],
    benchmark: str,
    lookback: int,
) -> BenchmarkComparison | None:
    """Compare two aligned close series over one lookback.

    The two series must already be aligned on session dates by the caller;
    misaligned series produce a plausible number computed from mismatched days,
    which is worse than an error. :func:`align_series` does the alignment.
    """
    if len(security_closes) != len(benchmark_closes):
        raise DataError(
            f"security and benchmark series differ in length "
            f"({len(security_closes)} vs {len(benchmark_closes)}); align them on "
            "session dates first"
        )

    security_return = _total_return(security_closes, lookback)
    benchmark_return = _total_return(benchmark_closes, lookback)
    if security_return is None or benchmark_return is None:
        return None
    if benchmark_return <= -1.0:
        return None

    relative = (1.0 + security_return) / (1.0 + benchmark_return) - 1.0

    window_security = np.asarray(security_closes[-(lookback + 1) :], dtype=np.float64)
    window_benchmark = np.asarray(benchmark_closes[-(lookback + 1) :], dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(window_benchmark > 0, window_security / window_benchmark, np.nan)

    trend = 0.0
    if np.isfinite(ratio).sum() >= 2 and ratio[0] > 0:
        # Per-bar fractional drift of the ratio line, normalised by its start so
        # it is comparable across price levels.
        trend = float((ratio[-1] / ratio[0] - 1.0) / lookback)

    midpoint = len(ratio) // 2
    momentum = 0.0
    if midpoint > 0 and np.isfinite(ratio[midpoint]) and ratio[midpoint] > 0:
        momentum = float(ratio[-1] / ratio[midpoint] - 1.0)

    return BenchmarkComparison(
        benchmark=benchmark,
        lookback=lookback,
        security_return=security_return,
        benchmark_return=benchmark_return,
        relative_performance=relative,
        excess_return=security_return - benchmark_return,
        relative_trend=trend,
        relative_momentum=momentum,
    )


def align_series(
    security: Mapping[dt.date, float], benchmark: Mapping[dt.date, float]
) -> tuple[list[dt.date], list[float], list[float]]:
    """Restrict two date-keyed series to the sessions they share.

    A security that halted for three days and a benchmark that did not will
    otherwise be compared across offset windows. Intersecting is the
    conservative choice: it shortens the lookback rather than silently pairing
    Tuesday's stock price with Wednesday's index.
    """
    shared = sorted(set(security) & set(benchmark))
    return shared, [security[d] for d in shared], [benchmark[d] for d in shared]


def percentile_rank(
    value: float, peer_values: Sequence[float], *, minimum_peers: int
) -> float | None:
    """Fraction of the eligible peer set at or below ``value``, in [0, 1].

    ``peer_values`` **must** be the values of instruments eligible on the
    ranking date — including those that later delisted. This function cannot
    verify that; the caller assembles the roster from point-in-time universe
    membership, and :meth:`RelativeStrengthEngine.rank_cross_section` is the
    supported path.

    Returns ``None`` below ``minimum_peers``: a percentile over four names is a
    number, not a rank, and emitting it invites a screen that ranks a security
    first out of three.
    """
    finite = [v for v in peer_values if v is not None and np.isfinite(v)]
    if len(finite) < minimum_peers:
        return None
    at_or_below = sum(1 for v in finite if v <= value)
    return at_or_below / len(finite)


class RelativeStrengthEngine:
    """Benchmark-relative and cross-sectional strength.

    Stateless: every method takes the data it needs. That is what lets a
    backtest call it once per historical date with that date's universe, and
    what makes it impossible to accidentally carry a universe forward.
    """

    def __init__(self, config: RelativeStrengthConfig) -> None:
        self.config = config
        self.registry = self._build_registry(config)

    @staticmethod
    def _build_registry(config: RelativeStrengthConfig) -> FeatureRegistry:
        registry = FeatureRegistry()

        def spec(
            name: str,
            description: str,
            kind: FeatureKind,
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
                    kind=kind,
                    timeframe=Bartimeframe.D1,
                    output_type=output,
                    null_behaviour=null,
                    input_datasets=RS_INPUTS,
                    warmup_periods=warmup,
                    parameters=parameters,
                    lookback_sessions=parameters.get("lookback"),  # type: ignore[arg-type]
                )
            )

        for benchmark in config.benchmarks:
            key = benchmark.lower()
            for lookback in config.lookbacks:
                base = {"benchmark": benchmark, "lookback": lookback}
                spec(
                    f"rs_relative_performance_{key}_{lookback}",
                    f"Compounding-correct performance vs {benchmark} over {lookback} sessions.",
                    FeatureKind.TIME_SERIES,
                    OutputType.RATIO,
                    lookback + 1,
                    base,
                )
                spec(
                    f"rs_excess_return_{key}_{lookback}",
                    f"Simple return difference vs {benchmark} over {lookback} sessions.",
                    FeatureKind.TIME_SERIES,
                    OutputType.RATIO,
                    lookback + 1,
                    base,
                )
                spec(
                    f"rs_trend_{key}_{lookback}",
                    f"Per-session drift of the {benchmark} ratio line -- is "
                    "outperformance building or fading?",
                    FeatureKind.TIME_SERIES,
                    OutputType.RATIO,
                    lookback + 1,
                    base,
                )
                spec(
                    f"rs_momentum_{key}_{lookback}",
                    f"Recent acceleration of the {benchmark} ratio line.",
                    FeatureKind.TIME_SERIES,
                    OutputType.RATIO,
                    lookback + 1,
                    base,
                )

        for lookback in config.lookbacks:
            spec(
                f"rs_universe_percentile_{lookback}",
                f"Percentile of {lookback}-session relative performance within the "
                "POINT-IN-TIME eligible universe, including names that later delisted.",
                FeatureKind.CROSS_SECTIONAL,
                OutputType.PERCENTILE_0_1,
                lookback + 1,
                {"lookback": lookback, "universe": "point_in_time"},
            )
            if config.rank_against_sector:
                spec(
                    f"rs_sector_percentile_{lookback}",
                    f"Percentile within the security's sector as classified on that "
                    f"date, over {lookback} sessions.",
                    FeatureKind.SECTOR_LEVEL,
                    OutputType.PERCENTILE_0_1,
                    lookback + 1,
                    {"lookback": lookback, "scope": "sector"},
                    NullBehaviour.NOT_APPLICABLE,
                )
            if config.rank_against_industry:
                spec(
                    f"rs_industry_percentile_{lookback}",
                    f"Percentile within the security's industry as classified on that "
                    f"date, over {lookback} sessions.",
                    FeatureKind.SECTOR_LEVEL,
                    OutputType.PERCENTILE_0_1,
                    lookback + 1,
                    {"lookback": lookback, "scope": "industry"},
                    NullBehaviour.NOT_APPLICABLE,
                )

        spec(
            "rs_score",
            "Weighted blend of universe percentiles across lookbacks, scaled 0-100. "
            "Longer horizons dominate: a name that has led for a year is a stronger "
            "statement than one that led for a month.",
            FeatureKind.CROSS_SECTIONAL,
            OutputType.SCORE_0_100,
            max(config.lookbacks) + 1,
            {
                "lookbacks": list(config.lookbacks),
                "weights": config.normalised_lookback_weights(),
                "benchmark": config.score_benchmark,
            },
        )
        registry.validate()
        return registry

    # -- benchmark-relative --------------------------------------------------

    def compare(
        self,
        security_closes: Mapping[dt.date, float],
        benchmark_closes: Mapping[str, Mapping[dt.date, float]],
    ) -> dict[tuple[str, int], BenchmarkComparison]:
        """Compare a security against every configured benchmark and lookback."""
        out: dict[tuple[str, int], BenchmarkComparison] = {}
        for benchmark in self.config.benchmarks:
            series = benchmark_closes.get(benchmark)
            if not series:
                continue
            _, security, reference = align_series(security_closes, series)
            for lookback in self.config.lookbacks:
                comparison = compare_to_benchmark(security, reference, benchmark, lookback)
                if comparison is not None:
                    out[(benchmark, lookback)] = comparison
        return out

    # -- cross-sectional -----------------------------------------------------

    def rank_cross_section(
        self,
        session_date: dt.date,
        eligible_universe: Sequence[int],
        relative_performance: Mapping[int, float | None],
        *,
        minimum_peers: int | None = None,
    ) -> dict[int, float | None]:
        """Percentile-rank one date's cross-section.

        ``eligible_universe`` is the roster of instruments that were in the
        universe **on that date** — the survivorship control. Anything in
        ``relative_performance`` that is not in the roster is ignored rather
        than silently included, because a value for an instrument that was not
        yet listed is either a bug or a leak.
        """
        roster = set(eligible_universe)
        stray = set(relative_performance) - roster
        if stray:
            raise DataError(
                f"relative performance supplied for {len(stray)} instrument(s) not in the "
                f"eligible universe on {session_date}: {sorted(stray)[:5]}. Ranking "
                "against instruments that were not eligible imports future constituents."
            )

        peers = np.array(
            [
                value
                for instrument_id, value in relative_performance.items()
                if instrument_id in roster and value is not None and np.isfinite(value)
            ],
            dtype=np.float64,
        )
        threshold = minimum_peers or self.config.min_universe_for_rank

        out: dict[int, float | None] = {}
        if peers.size < threshold:
            return dict.fromkeys(eligible_universe, None)

        # Sort once and binary-search, rather than counting the peer set for
        # every instrument. The naive form is O(n^2): at a 4,000-name universe
        # it measured 11 seconds per lookback, which is 136 seconds per session
        # across the configured benchmarks and lookbacks -- roughly 28 hours over
        # a three-year backtest. This is O(n log n) and measures in milliseconds.
        ordered = np.sort(peers)
        for instrument_id in eligible_universe:
            value = relative_performance.get(instrument_id)
            if value is None or not np.isfinite(value):
                out[instrument_id] = None
                continue
            # 'side=right' counts values <= the subject, matching percentile_rank.
            at_or_below = int(np.searchsorted(ordered, value, side="right"))
            out[instrument_id] = at_or_below / ordered.size
        return out

    def rank_within_groups(
        self,
        session_date: dt.date,
        groups: Mapping[int, str | None],
        relative_performance: Mapping[int, float | None],
        *,
        minimum_peers: int | None = None,
    ) -> dict[int, float | None]:
        """Percentile-rank within sector or industry groups.

        ``groups`` maps instrument to its classification **as it stood on
        ``session_date``**. An instrument with no classification for that date
        gets ``None`` rather than being lumped into a default bucket — an
        unclassified name in an "Other" group would distort that group's ranks
        and hide the gap in the data.
        """
        threshold = minimum_peers or self.config.min_sector_peers_for_rank
        by_group: dict[str, list[float]] = {}
        for instrument_id, group in groups.items():
            value = relative_performance.get(instrument_id)
            if group is None or value is None or not np.isfinite(value):
                continue
            by_group.setdefault(group, []).append(value)

        out: dict[int, float | None] = {}
        for instrument_id, group in groups.items():
            value = relative_performance.get(instrument_id)
            if group is None or value is None or not np.isfinite(value):
                out[instrument_id] = None
                continue
            out[instrument_id] = percentile_rank(value, by_group[group], minimum_peers=threshold)
        return out

    # -- score ---------------------------------------------------------------

    def score(self, universe_percentiles: Mapping[int, float | None]) -> float | None:
        """Blend per-lookback percentiles into a 0-100 score.

        Missing lookbacks are dropped and the remaining weights renormalised, so
        a young instrument with only 20- and 60-session history still receives a
        score computed from what exists rather than a null. Whether a score
        built on short horizons should be *trusted* is a screening decision, and
        the feature registry records the warm-up so the screen can decide.
        """
        weights = self.config.normalised_lookback_weights()
        contributions: list[tuple[float, float]] = []
        for lookback, percentile in universe_percentiles.items():
            if percentile is None:
                continue
            weight = weights.get(str(lookback))
            if weight is None:
                continue
            contributions.append((percentile, weight))

        if not contributions:
            return None
        total_weight = sum(w for _, w in contributions)
        blended = sum(p * w for p, w in contributions) / total_weight
        return round(blended * 100.0, 4)

    def evaluate(
        self,
        instrument_id: int,
        session_date: dt.date,
        comparisons: Mapping[tuple[str, int], BenchmarkComparison],
        universe_percentiles: Mapping[int, float | None],
        *,
        sector_percentiles: Mapping[int, float | None] | None = None,
        industry_percentiles: Mapping[int, float | None] | None = None,
        ranking_universe_size: Mapping[int, int] | None = None,
    ) -> RelativeStrengthResult:
        """Assemble the full result for one security on one date."""
        return RelativeStrengthResult(
            instrument_id=instrument_id,
            session_date=session_date,
            comparisons=dict(comparisons),
            universe_percentile=dict(universe_percentiles),
            sector_percentile=dict(sector_percentiles or {}),
            industry_percentile=dict(industry_percentiles or {}),
            rs_score=self.score(universe_percentiles),
            ranking_universe_size=dict(ranking_universe_size or {}),
        )
