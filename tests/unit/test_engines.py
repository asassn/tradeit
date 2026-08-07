"""Relative strength, breadth, sector strength, and the two regime engines.

The recurring theme is the point-in-time universe: every cross-sectional
measure must be computed over the roster that was eligible on the date, and
these tests refuse to let one be computed over anything else.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.analytics.breadth import BreadthEngine, InstrumentBreadthInput, universe_digest
from tradeit.analytics.regime import BenchmarkTrendInput, MarketRegimeEngine
from tradeit.analytics.relative_strength import (
    RelativeStrengthEngine,
    align_series,
    compare_to_benchmark,
    percentile_rank,
)
from tradeit.analytics.sectors import SectorMemberInput, SectorSource, SectorStrengthEngine
from tradeit.analytics.volatility import (
    VolatilityRegime,
    VolatilityRegimeEngine,
)
from tradeit.core.enums import MarketRegime
from tradeit.errors import DataError
from tradeit.strategy.config import StrategyConfig

CONFIG = StrategyConfig(name="baseline")
DAY = dt.date(2024, 3, 8)


# ---------------------------------------------------------------------------
# Relative strength
# ---------------------------------------------------------------------------


class TestBenchmarkComparison:
    def _series(self, start: float, end: float, n: int = 21) -> list[float]:
        step = (end / start) ** (1 / (n - 1))
        return [start * step**i for i in range(n)]

    def test_relative_performance_is_compounding_correct(self):
        """Stock +10%, benchmark -20%: relative is +37.5%, excess is +30 points.

        The two answer different questions and must not be conflated. The
        compounding form is the one that describes what a relative-strength
        position would have earned.
        """
        stock = self._series(100, 110)
        benchmark = self._series(100, 80)
        result = compare_to_benchmark(stock, benchmark, "SPY", 20)

        assert result.security_return == pytest.approx(0.10)
        assert result.benchmark_return == pytest.approx(-0.20)
        assert result.relative_performance == pytest.approx(1.10 / 0.80 - 1)
        assert result.excess_return == pytest.approx(0.30)
        assert result.outperforming

    def test_a_lookback_spans_n_plus_one_closes(self):
        """A 20-session return needs 21 observations. Off-by-one here shifts
        every relative measure by a session."""
        assert compare_to_benchmark([100.0] * 20, [100.0] * 20, "SPY", 20) is None
        assert compare_to_benchmark([100.0] * 21, [100.0] * 21, "SPY", 20) is not None

    def test_misaligned_series_are_refused(self):
        """A plausible number computed from mismatched days is worse than an error."""
        with pytest.raises(DataError, match="differ in length"):
            compare_to_benchmark([100.0] * 21, [100.0] * 20, "SPY", 20)

    def test_relative_trend_is_positive_when_outperformance_builds(self):
        stock = self._series(100, 130)
        benchmark = self._series(100, 100)
        assert compare_to_benchmark(stock, benchmark, "SPY", 20).relative_trend > 0

    def test_alignment_intersects_on_shared_sessions(self):
        """A halted stock must not be paired with the index's other days."""
        stock = {dt.date(2024, 3, d): 100.0 + d for d in (4, 5, 8)}
        benchmark = {dt.date(2024, 3, d): 200.0 + d for d in (4, 5, 6, 7, 8)}
        shared, left, right = align_series(stock, benchmark)
        assert shared == [dt.date(2024, 3, 4), dt.date(2024, 3, 5), dt.date(2024, 3, 8)]
        assert len(left) == len(right) == 3


class TestCrossSectionalRanking:
    @pytest.fixture
    def engine(self) -> RelativeStrengthEngine:
        return RelativeStrengthEngine(CONFIG.relative_strength)

    def test_percentile_needs_a_minimum_peer_count(self):
        """A percentile over four names is a number, not a rank."""
        assert percentile_rank(0.5, [0.1, 0.2, 0.3], minimum_peers=20) is None
        assert percentile_rank(0.5, [i / 100 for i in range(30)], minimum_peers=20) is not None

    def test_the_best_performer_ranks_at_one(self, engine):
        universe = list(range(1, 31))
        performance = {i: i / 100 for i in universe}
        ranks = engine.rank_cross_section(DAY, universe, performance)
        assert ranks[30] == pytest.approx(1.0)
        assert ranks[1] == pytest.approx(1 / 30)

    def test_ranking_against_an_instrument_outside_the_roster_is_refused(self, engine):
        """The survivorship guard: a value for an instrument that was not
        eligible on the date is either a bug or a leak, and both must be loud."""
        universe = list(range(1, 31))
        performance = {i: 0.1 for i in universe}
        performance[999] = 0.9  # not in the roster
        with pytest.raises(DataError, match="not in the eligible universe"):
            engine.rank_cross_section(DAY, universe, performance)

    def test_the_ranking_roster_materially_changes_the_answer(self, engine):
        """Why the point-in-time roster is not a formality.

        Names that later delisted were disproportionately poor performers, so
        including them — as a correct point-in-time universe does — lifts every
        surviving name's percentile. Rank a mid-pack survivor against survivors
        only and it looks worse than it truly was; the reported rank is a
        different statistic from the one it claims to be.

        The direction matters less than the magnitude: the same instrument on
        the same date gets a materially different rank purely from the roster,
        which is why the roster is an argument rather than something the engine
        fetches for itself.
        """
        survivors = list(range(1, 21))
        casualties = list(range(21, 31))
        # Survivors spread from -5% to +14%; the subject sits mid-pack.
        performance = {i: (i - 6) / 100 for i in survivors}
        performance |= {i: -0.60 for i in casualties}
        subject = 10

        point_in_time = engine.rank_cross_section(DAY, survivors + casualties, performance)
        survivors_only = engine.rank_cross_section(
            DAY, survivors, {i: performance[i] for i in survivors}
        )

        assert point_in_time[subject] > survivors_only[subject]
        assert point_in_time[subject] - survivors_only[subject] > 0.10, (
            "a roster change of this size must move the rank materially"
        )

    def test_group_ranking_leaves_unclassified_names_null(self, engine):
        """An unclassified name in an 'Other' bucket would distort that bucket
        and hide the gap in the data."""
        groups = {1: "Tech", 2: "Tech", 3: "Tech", 4: "Tech", 5: "Tech", 6: None}
        performance = {i: i / 100 for i in range(1, 7)}
        ranks = engine.rank_within_groups(DAY, groups, performance)
        assert ranks[6] is None
        assert ranks[5] == pytest.approx(1.0)

    def test_score_blends_lookbacks_with_longer_horizons_dominating(self, engine):
        leader_long = engine.score({20: 0.2, 60: 0.2, 120: 0.9, 250: 0.9})
        leader_short = engine.score({20: 0.9, 60: 0.9, 120: 0.2, 250: 0.2})
        assert leader_long > leader_short

    def test_score_renormalises_over_available_lookbacks(self, engine):
        """A young instrument with only short history still gets a score from
        what exists, rather than a null."""
        assert engine.score({20: 1.0, 60: 1.0}) == pytest.approx(100.0)
        assert engine.score({}) is None

    def test_score_is_zero_to_one_hundred(self, engine):
        assert engine.score(dict.fromkeys(CONFIG.relative_strength.lookbacks, 0.0)) == 0.0
        assert engine.score(dict.fromkeys(CONFIG.relative_strength.lookbacks, 1.0)) == 100.0

    def test_the_registry_names_every_benchmark_and_lookback(self, engine):
        names = set(engine.registry.names())
        assert "rs_relative_performance_spy_120" in names
        assert "rs_relative_performance_qqq_250" in names
        assert "rs_universe_percentile_20" in names
        assert "rs_score" in names


# ---------------------------------------------------------------------------
# Breadth
# ---------------------------------------------------------------------------


class TestBreadth:
    @pytest.fixture
    def engine(self) -> BreadthEngine:
        return BreadthEngine(CONFIG.breadth)

    def _input(self, instrument_id: int, *, up: bool, above: bool) -> InstrumentBreadthInput:
        return InstrumentBreadthInput(
            instrument_id=instrument_id,
            close=101.0 if up else 99.0,
            previous_close=100.0,
            volume=1_000_000,
            above_ma=dict.fromkeys(CONFIG.breadth.ma_periods, above),
            at_52w_high=up and above,
            at_52w_low=not up and not above,
        )

    def test_advances_and_declines_are_counted(self, engine):
        universe = list(range(1, 101))
        inputs = {i: self._input(i, up=i <= 60, above=i <= 70) for i in universe}
        snapshot = engine.compute(DAY, "us_equity", universe, inputs)

        assert snapshot.advances == 60
        assert snapshot.declines == 40
        assert snapshot.advance_decline_spread == 20
        assert snapshot.pct_above_ma[200] == pytest.approx(0.70)

    def test_the_snapshot_records_the_roster_it_was_computed_over(self, engine):
        """68% of *what*, exactly? The snapshot must answer that."""
        universe = list(range(1, 51))
        inputs = {i: self._input(i, up=True, above=True) for i in universe}
        snapshot = engine.compute(DAY, "us_equity", universe, inputs)

        assert snapshot.universe_size == 50
        assert snapshot.universe_digest == universe_digest(universe)

    def test_two_rosters_produce_different_digests(self, engine):
        """So a breadth series whose universe silently changed cannot be
        compared as though it were one series."""
        assert universe_digest([1, 2, 3]) != universe_digest([1, 2, 3, 4])

    def test_an_instrument_outside_the_roster_is_refused(self, engine):
        universe = list(range(1, 51))
        inputs = {i: self._input(i, up=True, above=True) for i in range(1, 60)}
        with pytest.raises(DataError, match="outside the eligible universe"):
            engine.compute(DAY, "us_equity", universe, inputs)

    def test_a_small_universe_is_flagged_low_confidence(self, engine):
        universe = list(range(1, 11))
        inputs = {i: self._input(i, up=True, above=True) for i in universe}
        snapshot = engine.compute(DAY, "us_equity", universe, inputs)
        assert snapshot.low_confidence
        assert any("below the" in note for note in snapshot.notes)

    def test_missing_instruments_are_reported_not_hidden(self, engine):
        universe = list(range(1, 101))
        inputs = {i: self._input(i, up=True, above=True) for i in range(1, 81)}
        snapshot = engine.compute(DAY, "us_equity", universe, inputs)

        assert snapshot.universe_size == 100
        assert snapshot.evaluated == 80
        assert any("had no usable data" in note for note in snapshot.notes)

    def test_pct_above_ma_uses_only_instruments_with_a_defined_average(self, engine):
        """A young instrument with no 200DMA must not count as 'below' it."""
        universe = [1, 2, 3, 4]
        inputs = {
            1: InstrumentBreadthInput(
                1, 101, 100, 1e6, {20: True, 50: True, 200: True}, False, False
            ),
            2: InstrumentBreadthInput(
                2, 101, 100, 1e6, {20: True, 50: True, 200: None}, False, False
            ),
            3: InstrumentBreadthInput(
                3, 99, 100, 1e6, {20: False, 50: False, 200: False}, False, False
            ),
            4: InstrumentBreadthInput(
                4, 99, 100, 1e6, {20: False, 50: False, 200: None}, False, False
            ),
        }
        snapshot = engine.compute(DAY, "us_equity", universe, inputs)
        assert snapshot.pct_above_ma[200] == pytest.approx(0.5), "nulls must not count as below"

    def test_up_down_volume_ratio(self, engine):
        universe = [1, 2]
        inputs = {
            1: InstrumentBreadthInput(1, 101, 100, 3_000_000, {}, False, False),
            2: InstrumentBreadthInput(2, 99, 100, 1_000_000, {}, False, False),
        }
        snapshot = engine.compute(DAY, "us_equity", universe, inputs)
        assert snapshot.up_down_volume_ratio == pytest.approx(3.0)

    def test_the_ad_line_is_cumulative(self, engine):
        universe = list(range(1, 21))
        snapshots = []
        for advancing in (15, 5, 12):
            inputs = {i: self._input(i, up=i <= advancing, above=True) for i in universe}
            snapshots.append(engine.compute(DAY, "us_equity", universe, inputs))
        line = BreadthEngine.advance_decline_line(snapshots)
        assert line == [10, 10 - 10, 0 + 4]


# ---------------------------------------------------------------------------
# Sector strength
# ---------------------------------------------------------------------------


class TestSectorStrength:
    @pytest.fixture
    def engine(self) -> SectorStrengthEngine:
        return SectorStrengthEngine(CONFIG.sector_strength)

    def _members(self, count: int, ret: float, above: bool) -> list[SectorMemberInput]:
        return [
            SectorMemberInput(
                instrument_id=i,
                return_over=dict.fromkeys(CONFIG.sector_strength.momentum_lookbacks, ret),
                above_ma=dict.fromkeys(CONFIG.sector_strength.breadth_ma_periods, above),
                at_52w_high=above and ret > 0,
                relative_volume=1.2,
            )
            for i in range(1, count + 1)
        ]

    def test_a_strong_sector_scores_above_a_weak_one(self, engine):
        market = dict.fromkeys(CONFIG.sector_strength.momentum_lookbacks, 0.02)
        strong = engine.evaluate_sector(DAY, "Tech", self._members(20, 0.12, True), market)
        weak = engine.evaluate_sector(DAY, "Utilities", self._members(20, -0.05, False), market)
        assert strong.score > weak.score
        assert strong.usable and weak.usable

    def test_a_sector_below_the_member_minimum_is_not_scored(self, engine):
        """Three constituents is arithmetic, not a measure of the sector."""
        market = dict.fromkeys(CONFIG.sector_strength.momentum_lookbacks, 0.0)
        result = engine.evaluate_sector(DAY, "Tiny", self._members(3, 0.10, True), market)
        assert result.score is None
        assert not result.usable
        assert any("below the" in note for note in result.notes)

    def test_relative_return_is_measured_against_the_market(self, engine):
        market = dict.fromkeys(CONFIG.sector_strength.momentum_lookbacks, 0.10)
        result = engine.evaluate_sector(DAY, "Tech", self._members(20, 0.10, True), market)
        short = min(CONFIG.sector_strength.momentum_lookbacks)
        assert result.relative_return[short] == pytest.approx(0.0, abs=1e-9)

    def test_an_etf_proxy_result_is_labelled_as_one(self, engine):
        """It must never be mistaken for a constituent aggregate."""
        market = dict.fromkeys(CONFIG.sector_strength.momentum_lookbacks, 0.0)
        result = engine.evaluate_sector(
            DAY,
            "Real Estate",
            self._members(20, 0.05, True),
            market,
            source=SectorSource.ETF_PROXY,
        )
        assert result.source is SectorSource.ETF_PROXY
        assert any("sector ETF" in note for note in result.notes)

    def test_participation_distinguishes_broad_from_narrow_advances(self, engine):
        """The same sector return with a quarter versus three quarters of members
        participating is a materially different signal."""
        market = dict.fromkeys(CONFIG.sector_strength.momentum_lookbacks, 0.0)
        broad = self._members(16, 0.08, True)
        narrow = self._members(4, 0.30, True) + self._members(12, -0.02, False)
        # Renumber so instrument ids stay unique.
        narrow = [
            SectorMemberInput(i, m.return_over, m.above_ma, m.at_52w_high, m.at_52w_low)
            for i, m in enumerate(narrow, start=1)
        ]

        broad_result = engine.evaluate_sector(DAY, "A", broad, market)
        narrow_result = engine.evaluate_sector(DAY, "B", narrow, market)
        assert broad_result.participation_status == "broad"
        assert narrow_result.participation_status == "narrow"

    def test_ranking_orders_strongest_first(self, engine):
        market = dict.fromkeys(CONFIG.sector_strength.momentum_lookbacks, 0.0)
        results = [
            engine.evaluate_sector(DAY, "Weak", self._members(20, -0.10, False), market),
            engine.evaluate_sector(DAY, "Strong", self._members(20, 0.15, True), market),
            engine.evaluate_sector(DAY, "Mid", self._members(20, 0.02, True), market),
        ]
        ranked = engine.rank(results)
        assert [r.sector for r in ranked] == ["Strong", "Mid", "Weak"]
        assert [r.rank for r in ranked] == [1, 2, 3]

    def test_unscored_sectors_keep_a_null_rank(self, engine):
        market = dict.fromkeys(CONFIG.sector_strength.momentum_lookbacks, 0.0)
        results = [
            engine.evaluate_sector(DAY, "Big", self._members(20, 0.10, True), market),
            engine.evaluate_sector(DAY, "Tiny", self._members(2, 0.50, True), market),
        ]
        ranked = engine.rank(results)
        by_sector = {r.sector: r for r in ranked}
        assert by_sector["Big"].rank == 1
        assert by_sector["Tiny"].rank is None


# ---------------------------------------------------------------------------
# Regimes
# ---------------------------------------------------------------------------


class TestVolatilityRegime:
    @pytest.fixture
    def engine(self) -> VolatilityRegimeEngine:
        return VolatilityRegimeEngine(CONFIG.volatility_regime)

    @pytest.mark.parametrize(
        ("percentile", "expected"),
        [
            (0.05, VolatilityRegime.LOW),
            (0.40, VolatilityRegime.NORMAL),
            (0.70, VolatilityRegime.ELEVATED),
            (0.90, VolatilityRegime.HIGH),
            (0.99, VolatilityRegime.EXTREME),
        ],
    )
    def test_percentile_bands(self, engine, percentile, expected):
        state = engine.classify(DAY, realized_volatility=0.2, volatility_percentile=percentile)
        assert state.regime is expected

    def test_no_history_gives_unknown_rather_than_a_guess(self, engine):
        """An unreliable regime label is worse than an absent one: downstream
        code will act on it."""
        state = engine.classify(DAY, realized_volatility=None, volatility_percentile=None)
        assert state.regime is VolatilityRegime.UNKNOWN
        assert state.confidence == 0
        assert state.notes

    def test_confidence_falls_at_a_band_edge(self, engine):
        """A reading one basis point from a boundary is a coin flip."""
        edge = engine.classify(DAY, realized_volatility=0.2, volatility_percentile=0.201)
        centre = engine.classify(DAY, realized_volatility=0.2, volatility_percentile=0.40)
        assert edge.confidence < centre.confidence

    def test_contradicting_evidence_is_recorded(self, engine):
        state = engine.classify(
            DAY,
            realized_volatility=0.4,
            volatility_percentile=0.92,
            gap_frequency=0.01,
            volatility_expansion=0.7,
        )
        assert state.regime is VolatilityRegime.HIGH
        assert state.contradicting_evidence

    def test_dispersion_needs_a_meaningful_cross_section(self, engine):
        assert VolatilityRegimeEngine.cross_sectional_dispersion([0.01] * 5) is None
        assert (
            VolatilityRegimeEngine.cross_sectional_dispersion([i / 100 for i in range(30)])
            is not None
        )

    def test_explain_lists_both_kinds_of_evidence(self, engine):
        text = engine.classify(
            DAY, realized_volatility=0.4, volatility_percentile=0.92, gap_frequency=0.01
        ).explain()
        assert "Volatility Regime:" in text
        assert "Contradicting Evidence:" in text


class TestMarketRegime:
    @pytest.fixture
    def engine(self) -> MarketRegimeEngine:
        return MarketRegimeEngine(CONFIG.regime)

    def _benchmarks(self, *, bullish: bool) -> dict[str, BenchmarkTrendInput]:
        if bullish:
            return {
                s: BenchmarkTrendInput(s, close=110, ma_fast=105, ma_slow=100, ma_fast_slope=0.0015)
                for s in ("SPY", "QQQ", "IWM")
            }
        return {
            s: BenchmarkTrendInput(s, close=90, ma_fast=95, ma_slow=105, ma_fast_slope=-0.0015)
            for s in ("SPY", "QQQ", "IWM")
        }

    def test_a_broad_uptrend_classifies_bullish(self, engine):
        state = engine.classify(
            DAY,
            self._benchmarks(bullish=True),
            pct_above_200dma=0.75,
            new_highs=200,
            new_lows=20,
            sectors_above_fast_ma=9,
            sectors_total=11,
            volatility_regime=VolatilityRegime.NORMAL,
        )
        assert state.regime is MarketRegime.BULL_TRENDING
        assert state.label == "STRONG_BULL"
        assert state.is_risk_on

    def test_a_broad_downtrend_classifies_bearish(self, engine):
        state = engine.classify(
            DAY,
            self._benchmarks(bullish=False),
            pct_above_200dma=0.12,
            new_highs=5,
            new_lows=400,
            sectors_above_fast_ma=1,
            sectors_total=11,
            volatility_regime=VolatilityRegime.ELEVATED,
        )
        assert state.regime is MarketRegime.BEAR_TRENDING
        assert not state.is_risk_on

    def test_extreme_volatility_overrides_into_severe_risk_off(self, engine):
        """A different operating environment from an ordinary bear, and one
        where sizing should shrink regardless of what the trend says."""
        state = engine.classify(
            DAY,
            self._benchmarks(bullish=False),
            pct_above_200dma=0.10,
            new_highs=1,
            new_lows=800,
            sectors_above_fast_ma=0,
            sectors_total=11,
            volatility_regime=VolatilityRegime.EXTREME,
        )
        assert state.label == "SEVERE_RISK_OFF"
        assert any("overrides" in note for note in state.notes)

    def test_extreme_volatility_does_not_override_a_positive_market(self, engine):
        """Volatility alone is not risk-off; 2020 H2 was volatile and rising."""
        state = engine.classify(
            DAY,
            self._benchmarks(bullish=True),
            pct_above_200dma=0.72,
            new_highs=150,
            new_lows=30,
            sectors_above_fast_ma=9,
            sectors_total=11,
            volatility_regime=VolatilityRegime.EXTREME,
        )
        assert state.label != "SEVERE_RISK_OFF"

    def test_evidence_is_recorded_both_for_and_against(self, engine):
        """A BULL reading with three contradicting signals is a materially
        different statement from one with none."""
        mixed = {
            "SPY": BenchmarkTrendInput(
                "SPY", close=110, ma_fast=105, ma_slow=100, ma_fast_slope=0.0012
            ),
            "QQQ": BenchmarkTrendInput(
                "QQQ", close=110, ma_fast=105, ma_slow=100, ma_fast_slope=0.0015
            ),
            "IWM": BenchmarkTrendInput(
                "IWM", close=98, ma_fast=100, ma_slow=95, ma_fast_slope=-0.0004
            ),
        }
        state = engine.classify(
            DAY,
            mixed,
            pct_above_200dma=0.68,
            new_highs=180,
            new_lows=40,
            sectors_above_fast_ma=8,
            sectors_total=11,
            volatility_regime=VolatilityRegime.NORMAL,
            universe_size=3900,
        )
        assert state.supporting_evidence
        assert "IWM below 50DMA" in state.contradicting_evidence
        assert "IWM 50DMA falling" in state.contradicting_evidence

    def test_the_explanation_reconciles_with_the_score(self, engine):
        """Guards a late adjustment applied without being recorded as a signal."""
        state = engine.classify(
            DAY,
            self._benchmarks(bullish=True),
            pct_above_200dma=0.7,
            new_highs=100,
            new_lows=50,
            sectors_above_fast_ma=7,
            sectors_total=11,
            volatility_regime=VolatilityRegime.NORMAL,
        )
        assert state.reconciles

    def test_no_computable_signals_gives_unknown(self, engine):
        state = engine.classify(DAY, {"SPY": BenchmarkTrendInput("SPY")})
        assert state.regime is MarketRegime.UNKNOWN
        assert state.confidence == 0

    def test_partial_inputs_reduce_confidence_and_say_so(self, engine):
        sparse = engine.classify(DAY, self._benchmarks(bullish=True))
        full = engine.classify(
            DAY,
            self._benchmarks(bullish=True),
            pct_above_200dma=0.75,
            new_highs=200,
            new_lows=20,
            sectors_above_fast_ma=9,
            sectors_total=11,
            volatility_regime=VolatilityRegime.NORMAL,
        )
        assert sparse.confidence < full.confidence
        assert any("signals were computable" in note for note in sparse.notes)

    def test_classification_is_deterministic(self, engine):
        kwargs = dict(
            pct_above_200dma=0.7,
            new_highs=100,
            new_lows=50,
            sectors_above_fast_ma=7,
            sectors_total=11,
            volatility_regime=VolatilityRegime.NORMAL,
        )
        first = engine.classify(DAY, self._benchmarks(bullish=True), **kwargs)
        second = engine.classify(DAY, self._benchmarks(bullish=True), **kwargs)
        assert first.composite_score == second.composite_score
        assert first.regime is second.regime
        assert first.confidence == second.confidence

    def test_explain_matches_the_briefs_format(self, engine):
        text = engine.classify(
            DAY,
            self._benchmarks(bullish=True),
            pct_above_200dma=0.68,
            new_highs=180,
            new_lows=40,
            sectors_above_fast_ma=8,
            sectors_total=11,
            volatility_regime=VolatilityRegime.NORMAL,
        ).explain()
        for heading in (
            "Market Regime:",
            "Confidence:",
            "Supporting Evidence:",
            "Contradicting Evidence:",
        ):
            assert heading in text
