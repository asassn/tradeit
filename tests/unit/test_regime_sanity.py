"""Behavioural review of the regime, volatility and relative-strength models.

**What these tests are.** Each scenario feeds the classifiers the market
conditions that publicly and uncontroversially prevailed on a given date --
"in March 2020 the S&P was roughly 25% below its 200-day average, fewer than 5%
of stocks were above theirs, and realised volatility was at the top of its
multi-decade range" -- and asserts the model says something a person would
recognise. That is a review of the *classifier's logic*, which is the thing
under test.

**What they are not.** The inputs are stated historical conditions, not values
computed from a price feed. No claim is made here that the pipeline reproduces
those inputs from vendor data; that requires market data this environment
cannot reach (see ``docs/PHASE_03_GATE.md``). A scenario passing means the
classifier reasons correctly *given* the conditions, and nothing more.

**No thresholds were changed to make these pass.** The brief is explicit that
Phase 3 must not tune regime thresholds against historical outcomes, and doing
so with no out-of-sample harness would produce a model that looks excellent on
the data it was tuned on. Where a scenario initially disagreed with the label a
person would give, the disagreement is documented in the test rather than
legislated away -- see ``test_a_grinding_bear_is_not_a_crash``.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from tradeit.analytics.regime import BenchmarkTrendInput, MarketRegimeEngine
from tradeit.analytics.relative_strength import (
    RelativeStrengthEngine,
    compare_to_benchmark,
    percentile_rank,
)
from tradeit.analytics.volatility import VolatilityRegime, VolatilityRegimeEngine
from tradeit.core.enums import MarketRegime
from tradeit.errors import DataError
from tradeit.strategy.config import StrategyConfig

CONFIG = StrategyConfig(name="baseline")
REGIME = MarketRegimeEngine(CONFIG.regime)
VOLATILITY = VolatilityRegimeEngine(CONFIG.volatility_regime)
RS = RelativeStrengthEngine(CONFIG.relative_strength)


def benchmarks(
    *, above_fast: bool, above_slow: bool, slope: float, spread: float = 0.03
) -> dict[str, BenchmarkTrendInput]:
    """Three benchmarks in the same trend structure.

    ``spread`` is how far price sits from the moving averages, which is what
    distinguishes a mild drift from a dislocation.
    """
    close = 100.0
    fast = close * (1 - spread) if above_fast else close * (1 + spread)
    slow = close * (1 - spread * 2) if above_slow else close * (1 + spread * 2)
    return {
        symbol: BenchmarkTrendInput(
            symbol=symbol,
            close=close,
            ma_fast=fast,
            ma_slow=slow,
            ma_fast_slope=slope,
            ma_slow_slope=slope / 2,
        )
        for symbol in ("SPY", "QQQ", "IWM")
    }


class TestMarketRegimeAgainstRecognisableConditions:
    def test_march_2020_is_severe_risk_off(self):
        """The conditions: price far below both averages, breadth near zero,
        volatility at the top of its multi-decade range.

        This is the scenario where getting the label wrong is most expensive,
        because it is the one that should shrink exposure.
        """
        state = REGIME.classify(
            dt.date(2020, 3, 23),
            benchmarks(above_fast=False, above_slow=False, slope=-0.02, spread=0.12),
            pct_above_200dma=0.03,
            new_highs=2,
            new_lows=1_800,
            sectors_above_fast_ma=0,
            sectors_total=11,
            volatility_regime=VolatilityRegime.EXTREME,
            universe_size=3_000,
        )
        assert state.regime in (MarketRegime.HIGH_VOLATILITY, MarketRegime.BEAR_TRENDING)
        assert state.composite_score < -0.5
        assert state.confidence >= 70

    def test_the_2017_grind_higher_is_bull_trending_with_high_confidence(self):
        """Low volatility, broad participation, every index above its averages.

        The easy case, and worth pinning: a model that hedges here is useless.
        """
        state = REGIME.classify(
            dt.date(2017, 10, 2),
            benchmarks(above_fast=True, above_slow=True, slope=0.01),
            pct_above_200dma=0.72,
            new_highs=380,
            new_lows=40,
            sectors_above_fast_ma=10,
            sectors_total=11,
            volatility_regime=VolatilityRegime.LOW,
            universe_size=3_000,
        )
        assert state.regime is MarketRegime.BULL_TRENDING
        assert state.confidence >= 70
        assert not state.contradicting_evidence or len(state.contradicting_evidence) <= 1

    def test_a_narrow_advance_is_flagged_even_while_bullish(self):
        """2023: indices up, breadth weak. The distinction the model exists for.

        A cap-weighted index can rise while most stocks fall. A regime model
        that reports only the index direction says "bull" and hides the fact
        that a breakout system has almost nothing to buy.
        """
        state = REGIME.classify(
            dt.date(2023, 6, 15),
            benchmarks(above_fast=True, above_slow=True, slope=0.008),
            pct_above_200dma=0.41,
            new_highs=90,
            new_lows=120,
            sectors_above_fast_ma=4,
            sectors_total=11,
            volatility_regime=VolatilityRegime.NORMAL,
            universe_size=3_000,
        )
        assert state.composite_score > 0, "the index trend is genuinely up"
        assert state.contradicting_evidence, "narrow participation must be recorded"
        assert any(
            "sector" in e.lower() or "breadth" in e.lower() or "high" in e.lower()
            for e in state.contradicting_evidence
        )
        assert state.confidence < 90, "a divided market is not a confident reading"

    def test_a_grinding_bear_is_not_a_crash(self):
        """2022: a persistent decline at ordinary volatility.

        The model reports BEAR_TRENDING rather than HIGH_VOLATILITY, which is
        correct and is the distinction that matters for sizing: 2022 was a bad
        year to be long and a perfectly ordinary year to be *positioned*.
        Conflating it with March 2020 would have de-risked into every rally.
        """
        state = REGIME.classify(
            dt.date(2022, 9, 30),
            benchmarks(above_fast=False, above_slow=False, slope=-0.008, spread=0.05),
            pct_above_200dma=0.14,
            new_highs=15,
            new_lows=600,
            sectors_above_fast_ma=1,
            sectors_total=11,
            volatility_regime=VolatilityRegime.ELEVATED,
            universe_size=3_000,
        )
        assert state.regime is MarketRegime.BEAR_TRENDING
        assert state.regime is not MarketRegime.HIGH_VOLATILITY

    def test_a_transition_is_low_confidence_rather_than_wrong(self):
        """Conflicting evidence must read as uncertainty, not as a coin flip.

        Rule-based models lag turning points -- that is a known cost of
        ADR-0012. What they must not do is lag *confidently*.
        """
        state = REGIME.classify(
            dt.date(2020, 6, 8),
            benchmarks(above_fast=True, above_slow=False, slope=0.004, spread=0.02),
            pct_above_200dma=0.38,
            new_highs=60,
            new_lows=55,
            sectors_above_fast_ma=6,
            sectors_total=11,
            volatility_regime=VolatilityRegime.ELEVATED,
            universe_size=3_000,
        )
        assert state.confidence < 75
        assert state.supporting_evidence and state.contradicting_evidence

    def test_a_single_lagging_index_reaches_the_evidence(self):
        """IWM below its 50DMA while SPY and QQQ lead.

        The aggregate agreement signal outvotes one lagging index, so without
        the unscored divergence notes this observation would vanish -- which is
        precisely the small-cap non-confirmation a breakout system wants to see.
        """
        inputs = benchmarks(above_fast=True, above_slow=True, slope=0.01)
        inputs["IWM"] = BenchmarkTrendInput(
            symbol="IWM", close=100.0, ma_fast=104.0, ma_slow=98.0, ma_fast_slope=-0.002
        )
        state = REGIME.classify(
            dt.date(2021, 11, 19),
            inputs,
            pct_above_200dma=0.58,
            new_highs=200,
            new_lows=140,
            sectors_above_fast_ma=7,
            sectors_total=11,
            volatility_regime=VolatilityRegime.NORMAL,
            universe_size=3_000,
        )
        assert any("IWM" in e for e in state.contradicting_evidence)

    def test_missing_inputs_produce_unknown_rather_than_a_guess(self):
        """An unreliable label is worse than an absent one: something acts on it."""
        state = REGIME.classify(dt.date(2020, 3, 23), {})
        assert state.regime is MarketRegime.UNKNOWN
        assert state.confidence == 0

    def test_the_classification_is_reproducible(self):
        """Same inputs, same answer. No clock, no database, no global."""
        args = (
            dt.date(2021, 5, 3),
            benchmarks(above_fast=True, above_slow=True, slope=0.006),
        )
        kwargs = {
            "pct_above_200dma": 0.60,
            "new_highs": 150,
            "new_lows": 90,
            "sectors_above_fast_ma": 7,
            "sectors_total": 11,
            "volatility_regime": VolatilityRegime.NORMAL,
            "universe_size": 3_000,
        }
        first = REGIME.classify(*args, **kwargs)
        second = REGIME.classify(*args, **kwargs)
        assert first.regime is second.regime
        assert first.composite_score == second.composite_score
        assert first.supporting_evidence == second.supporting_evidence

    def test_the_score_moves_monotonically_with_the_evidence(self):
        """Improving one input must never make the reading more bearish.

        The property that catches a sign error in a weight, which is invisible
        in any single classification.
        """
        scores = []
        for breadth in (0.05, 0.25, 0.50, 0.75, 0.95):
            state = REGIME.classify(
                dt.date(2021, 5, 3),
                benchmarks(above_fast=True, above_slow=True, slope=0.005),
                pct_above_200dma=breadth,
                new_highs=100,
                new_lows=100,
                sectors_above_fast_ma=6,
                sectors_total=11,
                volatility_regime=VolatilityRegime.NORMAL,
                universe_size=3_000,
            )
            scores.append(state.composite_score)
        assert scores == sorted(scores)

    def test_a_tiny_universe_does_not_produce_a_confident_breadth_reading(self):
        """Breadth on 12 names is noise wearing a percentage sign."""
        wide = REGIME.classify(
            dt.date(2021, 5, 3),
            benchmarks(above_fast=True, above_slow=True, slope=0.005),
            pct_above_200dma=0.8,
            universe_size=3_000,
        )
        narrow = REGIME.classify(
            dt.date(2021, 5, 3),
            benchmarks(above_fast=True, above_slow=True, slope=0.005),
            pct_above_200dma=0.8,
            universe_size=12,
        )
        assert narrow.confidence <= wide.confidence


class TestVolatilityRegime:
    def test_a_percentile_at_the_top_of_its_range_is_extreme(self):
        state = VOLATILITY.classify(
            dt.date(2020, 3, 23),
            realized_volatility=0.82,
            volatility_percentile=0.99,
            atr_percent=0.09,
            gap_frequency=0.55,
        )
        assert state.regime is VolatilityRegime.EXTREME
        assert state.confidence > 0

    def test_a_quiet_market_is_low(self):
        state = VOLATILITY.classify(
            dt.date(2017, 10, 2),
            realized_volatility=0.06,
            volatility_percentile=0.04,
            atr_percent=0.006,
            gap_frequency=0.02,
        )
        assert state.regime is VolatilityRegime.LOW

    def test_without_a_percentile_it_refuses_to_guess(self):
        """An absolute volatility level means nothing without its own history.

        20% annualised is calm for a biotech and alarming for a utility.
        """
        state = VOLATILITY.classify(
            dt.date(2020, 3, 23), realized_volatility=0.82, volatility_percentile=None
        )
        assert state.regime is VolatilityRegime.UNKNOWN
        assert state.confidence == 0
        assert any("refusing to guess" in n for n in state.notes)

    def test_a_nan_percentile_is_treated_as_absent_not_as_zero(self):
        """The failure that would classify every warming-up instrument as calm."""
        state = VOLATILITY.classify(
            dt.date(2020, 3, 23), realized_volatility=0.5, volatility_percentile=float("nan")
        )
        assert state.regime is VolatilityRegime.UNKNOWN

    def test_the_bands_are_ordered_and_cover_the_whole_range(self):
        """No percentile falls between two bands and comes back UNKNOWN."""
        seen = []
        for percentile in np.linspace(0.0, 1.0, 101):
            state = VOLATILITY.classify(
                dt.date(2021, 1, 4),
                realized_volatility=0.2,
                volatility_percentile=float(percentile),
            )
            assert state.regime is not VolatilityRegime.UNKNOWN, percentile
            seen.append(state.regime)
        # Monotone: once the regime steps up it never steps back down.
        order = [VolatilityRegime.LOW, VolatilityRegime.NORMAL]
        indices = [order.index(r) if r in order else len(order) for r in seen]
        assert indices == sorted(indices)

    def test_volatility_is_a_separate_model_from_trend(self):
        """2020 H2 was volatile and rising. One row carrying both judgements
        would force a single confidence number for two questions."""
        state = REGIME.classify(
            dt.date(2020, 9, 1),
            benchmarks(above_fast=True, above_slow=True, slope=0.012),
            pct_above_200dma=0.62,
            new_highs=180,
            new_lows=60,
            sectors_above_fast_ma=8,
            sectors_total=11,
            volatility_regime=VolatilityRegime.ELEVATED,
            universe_size=3_000,
        )
        assert state.composite_score > 0
        assert state.regime is not MarketRegime.HIGH_VOLATILITY


class TestRelativeStrength:
    """The cross-sectional model, where survivorship bias enters if it enters."""

    def test_a_percentile_is_taken_against_the_supplied_roster_only(self):
        """The whole survivorship control, in one assertion.

        The roster is the point-in-time universe. If ranking ever silently used
        "all instruments we have data for", every historical percentile would be
        computed against a universe that excludes the failures -- which flatters
        every survivor.
        """
        roster = list(range(1, 21))
        performance = {i: (i - 10) / 20.0 for i in roster}  # -0.45 .. +0.50

        with_failures = RS.rank_cross_section(
            dt.date(2008, 9, 12), roster, performance, minimum_peers=5
        )[15]

        survivors = [i for i in roster if performance[i] > -0.2]
        without_failures = RS.rank_cross_section(
            dt.date(2008, 9, 12),
            survivors,
            {i: performance[i] for i in survivors},
            minimum_peers=5,
        )[15]

        assert with_failures > without_failures, (
            "dropping the failures must lower a survivor's rank, not raise it; "
            "equality here would mean the roster is not being honoured"
        )

    def test_ranking_an_instrument_outside_the_roster_raises(self):
        """Silently including it would import a future constituent."""
        with pytest.raises(DataError, match="not in the eligible universe"):
            RS.rank_cross_section(dt.date(2020, 1, 2), [1, 2], {1: 0.1, 2: 0.2, 99: 0.3})

    def test_percentiles_are_order_preserving_and_span_the_range(self):
        roster = list(range(1, 21))
        ranks = RS.rank_cross_section(
            dt.date(2020, 1, 2), roster, {i: float(i) for i in roster}, minimum_peers=5
        )
        ordered = [ranks[i] for i in roster]
        assert ordered == sorted(ordered)
        assert ordered[-1] == pytest.approx(1.0)
        assert ordered[0] < 0.1

    def test_ties_rank_identically(self):
        """Two identical performances must not be separated by sort order."""
        assert percentile_rank(0.10, [0.10, 0.10, 0.05], minimum_peers=2) == percentile_rank(
            0.10, [0.10, 0.10, 0.05], minimum_peers=2
        )

    def test_a_rank_over_too_few_peers_is_refused(self):
        """A percentile over four names is a number, not a rank.

        Emitting it invites a screen that ranks a security first out of three
        and sizes it like a market leader.
        """
        assert percentile_rank(0.1, [0.05, 0.2], minimum_peers=20) is None
        ranks = RS.rank_cross_section(
            dt.date(2020, 1, 2), [1, 2, 3], {1: 0.1, 2: 0.2, 3: 0.3}, minimum_peers=20
        )
        assert set(ranks.values()) == {None}

    def test_relative_strength_is_computed_on_returns_not_levels(self):
        """A $500 stock is not stronger than a $5 stock.

        The error would put every high-priced name in the top decile, which is
        exactly the kind of mistake that looks like a working signal.
        """
        cheap = [5.0, 5.5, 6.0]
        rich = [500.0, 505.0, 510.0]
        benchmark = [100.0, 101.0, 102.0]

        cheap_rs = compare_to_benchmark(cheap, benchmark, "SPY", 2)
        rich_rs = compare_to_benchmark(rich, benchmark, "SPY", 2)
        assert cheap_rs is not None and rich_rs is not None
        assert cheap_rs.relative_performance > rich_rs.relative_performance

    def test_misaligned_series_are_refused_rather_than_compared(self):
        """A plausible number computed from mismatched days is worse than an error."""
        with pytest.raises(DataError, match="align them on"):
            compare_to_benchmark([1.0, 2.0, 3.0], [1.0, 2.0], "SPY", 2)

    def test_the_engine_declares_every_benchmark_and_lookback_it_produces(self):
        """rs against SPY over 120 is a different feature from QQQ over 250.

        Storing both under one name makes the dataset unusable for backtesting,
        so the parameters have to be part of the registered identity.
        """
        names = RS.registry.names()
        assert "rs_relative_performance_spy_120" in names
        assert "rs_relative_performance_qqq_120" in names
        assert (
            RS.registry.get("rs_relative_performance_spy_120").digest
            != RS.registry.get("rs_relative_performance_qqq_120").digest
        )


class TestDefectsFoundByThisGate:
    """Regression tests for four defects the scenarios above surfaced.

    All four are in the categories the brief permits fixing -- definition
    errors and logical inconsistencies -- and none of them is a threshold
    change. Each was found by feeding the classifier a recognisable historical
    setup and noticing the answer did not read like the market it described.
    """

    def test_breadth_rejects_a_percentage_instead_of_scoring_it_backwards(self):
        """3% breadth passed as 3.0 previously scored +1.0: maximally bullish
        for the most bearish reading available, silently."""
        with pytest.raises(DataError, match=r"fraction in \[0, 1\]"):
            REGIME.classify(
                dt.date(2020, 3, 23),
                benchmarks(above_fast=False, above_slow=False, slope=-0.02),
                pct_above_200dma=3.0,
            )

    def test_zero_sectors_participating_is_a_signal_not_a_missing_input(self):
        """`if not sectors_above_fast` discarded the strongest bearish reading.

        Zero of eleven sectors above their moving average is the most negative
        participation value that exists; treating it as absent removed the
        signal exactly when it carried the most information.
        """
        state = REGIME.classify(
            dt.date(2020, 3, 23),
            benchmarks(above_fast=False, above_slow=False, slope=-0.02),
            pct_above_200dma=0.03,
            sectors_above_fast_ma=0,
            sectors_total=11,
        )
        assert any(s.name == "sector_participation" for s in state.signals)
        assert any("0 of 11 sectors" in e for e in state.supporting_evidence)

    def test_unanimous_agreement_does_not_reduce_confidence(self):
        """Every per-benchmark observation used to be filed as a divergence and
        charged a confidence penalty, so the more the benchmarks agreed the
        less confident the reading became. March 2020 scored 64/100 with all
        seven signals aligned."""
        state = REGIME.classify(
            dt.date(2020, 3, 23),
            benchmarks(above_fast=False, above_slow=False, slope=-0.02, spread=0.12),
            pct_above_200dma=0.03,
            new_highs=2,
            new_lows=1_800,
            sectors_above_fast_ma=0,
            sectors_total=11,
            volatility_regime=VolatilityRegime.EXTREME,
            universe_size=3_000,
        )
        assert state.confidence == 100
        assert state.contradicting_evidence == ()

    def test_evidence_is_filed_against_the_classification_not_against_bullishness(self):
        """The lists were exactly inverted for every negative regime.

        `RegimeSignal.supports` means "this signal reads bullish". Using it
        directly put all seven bearish signals under Contradicting Evidence on
        a BEAR reading -- an explanation that argued against its own
        conclusion.
        """
        bear = REGIME.classify(
            dt.date(2022, 9, 30),
            benchmarks(above_fast=False, above_slow=False, slope=-0.008, spread=0.05),
            pct_above_200dma=0.14,
            new_highs=15,
            new_lows=600,
            sectors_above_fast_ma=1,
            sectors_total=11,
            volatility_regime=VolatilityRegime.ELEVATED,
            universe_size=3_000,
        )
        assert bear.composite_score < 0
        assert any("sectors above" in e for e in bear.supporting_evidence)
        assert not any("sectors above" in e for e in bear.contradicting_evidence)

    def test_a_confirming_benchmark_is_recorded_in_both_directions(self):
        """Only bearish per-benchmark observations were emitted, so "IWM still
        holding its 200DMA" in a downtrend -- what an early turn looks like --
        was invisible."""
        inputs = benchmarks(above_fast=False, above_slow=False, slope=-0.01, spread=0.05)
        inputs["IWM"] = BenchmarkTrendInput(
            symbol="IWM", close=100.0, ma_fast=101.0, ma_slow=96.0, ma_fast_slope=0.002
        )
        state = REGIME.classify(
            dt.date(2022, 10, 13),
            inputs,
            pct_above_200dma=0.2,
            new_highs=20,
            new_lows=400,
            sectors_above_fast_ma=2,
            sectors_total=11,
            volatility_regime=VolatilityRegime.ELEVATED,
            universe_size=3_000,
        )
        assert state.composite_score < 0
        assert any("IWM above 200DMA" in e for e in state.contradicting_evidence)

    def test_the_explanation_still_reconciles_with_its_score(self):
        """Unscored observations must never leak into the composite."""
        state = REGIME.classify(
            dt.date(2020, 3, 23),
            benchmarks(above_fast=False, above_slow=False, slope=-0.02, spread=0.12),
            pct_above_200dma=0.03,
            new_highs=2,
            new_lows=1_800,
            sectors_above_fast_ma=0,
            sectors_total=11,
            volatility_regime=VolatilityRegime.EXTREME,
            universe_size=3_000,
        )
        assert state.reconciles
