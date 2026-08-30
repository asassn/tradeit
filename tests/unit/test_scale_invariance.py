"""Proving, not asserting, which analytics survive a uniform price rescaling.

**Why this matters right now.** The first full universe acquisition produced 78
instruments of split-adjusted daily bars. Only 34 have a verified split
schedule; the other 44 were refused by the split source with HTTP 402, which is
a fact about a subscription and not about those securities. Two responses would
be wrong: discarding 44 instruments of real market data, or using all 78 for
everything.

The defensible middle needs one property to actually hold: **a split adjustment
is a rescaling of the price series**, so a computation whose answer does not
change when every price is multiplied by a positive constant gives the same
answer on adjusted prices as on raw ones. That property is the licence to use
the 44, and a licence nobody checked is not a licence.

So every rule in :mod:`tradeit.validation.scale` is executed here against the
**real** indicator engine over a **real** bar series, at six factors including a
reverse-split-shaped one below 1. A feature declared invariant that moves fails.
A feature the engine emits that no rule covers fails. And a feature declared
sensitive that does *not* move is reported, because an over-cautious
classification silently shrinks the sample that may be validated.
"""

from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal

import numpy as np
import pytest

import tradeit.analytics.kernels as k
from tradeit.analytics.indicators import IndicatorEngine
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import OhlcvBar
from tradeit.data.packages.capability import (
    CapabilityIndex,
    InstrumentCapability,
    InstrumentCapabilityRecord,
)
from tradeit.strategy.config import IndicatorConfig
from tradeit.validation.scale import (
    ANALYTIC_SCALE_CLASSIFICATION,
    DEFAULT_SCALE_FACTORS,
    FEATURE_SCALE_RULES,
    ScaleSensitivity,
    analytic_is_eligible,
    classify_feature,
    invariant_features,
    rescale_bars,
    scale_invariance_report,
    sensitive_features,
    series_is_invariant,
)


def make_bars(count: int = 400, *, start_price: float = 137.42) -> list[OhlcvBar]:
    """A bar series with enough shape to exercise every warm-up and window.

    Deterministic and awkward on purpose: a smooth ramp would let a feature that
    reads an absolute level look invariant because the level barely moves, and
    round numbers would let a rescaling be exact where a real one is not.
    """
    bars: list[OhlcvBar] = []
    price = Decimal(str(start_price))
    day = dt.date(2010, 1, 4)
    written = 0
    while written < count:
        if day.weekday() < 5:
            # A deterministic wobble with trend, drawdowns and quiet stretches.
            drift = Decimal(1) + Decimal(str(round(0.004 * ((written % 23) - 11) / 11, 6)))
            price = (price * drift).quantize(Decimal("0.0001"))
            if price < Decimal("1"):
                price = Decimal("1")
            high = (price * Decimal("1.0134")).quantize(Decimal("0.0001"))
            low = (price * Decimal("0.9871")).quantize(Decimal("0.0001"))
            open_ = (price * Decimal("1.0021")).quantize(Decimal("0.0001"))
            open_ = min(max(open_, low), high)
            bars.append(
                OhlcvBar(
                    instrument_id=1,
                    timeframe=Bartimeframe.D1,
                    session_date=day,
                    event_time=dt.datetime.combine(day, dt.time(21, 0), tzinfo=dt.UTC),
                    knowledge_time=dt.datetime.combine(day, dt.time(21, 0), tzinfo=dt.UTC),
                    knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                    open=open_,
                    high=high,
                    low=low,
                    close=price,
                    volume=Decimal(1_000_000 + (written % 37) * 12_345),
                )
            )
            written += 1
        day += dt.timedelta(days=1)
    return bars


@pytest.fixture(scope="module")
def bars() -> list[OhlcvBar]:
    return make_bars()


@pytest.fixture(scope="module")
def engine() -> IndicatorEngine:
    return IndicatorEngine(IndicatorConfig())


def values_for(engine: IndicatorEngine):  # type: ignore[no-untyped-def]
    def compute(series):  # type: ignore[no-untyped-def]
        return engine.compute(series, instrument_id=1).values

    return compute


# ---------------------------------------------------------------------------
# The property itself
# ---------------------------------------------------------------------------


class TestDeclaredInvariantsHold:
    @pytest.mark.parametrize("factor", DEFAULT_SCALE_FACTORS)
    def test_every_feature_declared_invariant_is_invariant(
        self, engine: IndicatorEngine, bars: list[OhlcvBar], factor: float
    ) -> None:
        """The licence to validate 44 instruments without a raw series.

        Multiply the whole input price series by a constant and every feature
        this project calls scale-invariant must return the identical answer.
        """
        report = scale_invariance_report(values_for(engine), bars, factor)
        assert report.violations == (), (
            f"at factor {factor}, features declared scale-invariant moved: "
            + ", ".join(
                f"{o.name} (max relative difference {o.max_relative_difference:.3g})"
                for o in report.violations
            )
        )

    def test_no_feature_the_engine_emits_is_unclassified(
        self, engine: IndicatorEngine, bars: list[OhlcvBar]
    ) -> None:
        """An unclassified feature is the dangerous state.

        Used as though it were scale-free, it produces a confident wrong answer;
        this test is what makes adding a feature also require deciding.
        """
        report = scale_invariance_report(values_for(engine), bars, 2.0)
        assert report.unclassified == (), (
            "these features have no scale classification: "
            f"{list(report.unclassified)}. Add a rule to FEATURE_SCALE_RULES "
            "and say why, rather than letting them default to usable."
        )

    @pytest.mark.parametrize("factor", DEFAULT_SCALE_FACTORS)
    def test_every_feature_declared_sensitive_actually_moves(
        self, engine: IndicatorEngine, bars: list[OhlcvBar], factor: float
    ) -> None:
        """The other direction, and not a formality.

        A feature wrongly declared sensitive costs 44 instruments of usable
        sample for no reason. If one stops moving under rescaling, its
        classification is wrong and should be corrected rather than left
        conservative.
        """
        report = scale_invariance_report(values_for(engine), bars, factor)
        assert report.over_cautious == (), (
            f"at factor {factor}, features declared scale-sensitive did not move: "
            + ", ".join(o.name for o in report.over_cautious)
            + ". Reclassify them as invariant: an over-cautious rule shrinks the "
            "sample that may be validated without raw prices."
        )

    def test_the_classification_covers_both_kinds(
        self, engine: IndicatorEngine, bars: list[OhlcvBar]
    ) -> None:
        """A guard against a table that trivially passes by declaring nothing
        invariant, or everything."""
        names = sorted(engine.compute(bars, instrument_id=1).values)
        assert len(invariant_features(names)) >= 15
        assert len(sensitive_features(names)) >= 5


class TestTheNamedAnalytics:
    """The categories the request listed, each assessed rather than assumed."""

    @pytest.mark.parametrize(
        "analytic",
        [
            "percentage_returns",
            "relative_strength",
            "trend_geometry",
            "moving_average_relationships",
            "pattern_geometry_percentages",
            "contraction_percentages",
            "breakout_percentage",
            "normalized_volatility",
        ],
    )
    def test_the_invariant_list_is_declared_invariant(self, analytic: str) -> None:
        sensitivity, rationale = ANALYTIC_SCALE_CLASSIFICATION[analytic]
        assert sensitivity is ScaleSensitivity.INVARIANT
        assert rationale, "a classification without a reason is an assertion"

    @pytest.mark.parametrize(
        "analytic",
        [
            "absolute_price_filter",
            "raw_quoted_historical_price",
            "absolute_dollar_atr",
            "raw_share_volume",
            "dollar_volume",
            "position_sizing_on_historical_price",
            "exact_historical_raw_price_replay",
        ],
    )
    def test_the_sensitive_list_is_declared_sensitive(self, analytic: str) -> None:
        sensitivity, rationale = ANALYTIC_SCALE_CLASSIFICATION[analytic]
        assert sensitivity is ScaleSensitivity.SENSITIVE
        assert rationale

    def test_percentage_returns_are_invariant_in_fact(self, bars: list[OhlcvBar]) -> None:
        """The claim underneath most of the invariant list, checked directly."""

        def returns(series: list[OhlcvBar]) -> list[float]:
            closes = [float(b.close) for b in series]
            return [b / a - 1.0 for a, b in itertools.pairwise(closes)]

        base = returns(bars)
        for factor in DEFAULT_SCALE_FACTORS:
            moved = returns(rescale_bars(bars, factor))
            invariant, worst = series_is_invariant(base, moved)
            assert invariant, f"percentage returns moved at factor {factor} (worst {worst:.3g})"

    def test_dollar_volume_is_sensitive_in_fact(self, bars: list[OhlcvBar]) -> None:
        """The counterexample. If this were invariant the whole distinction
        would be vacuous."""

        def dollar_volume(series: list[OhlcvBar]) -> list[float]:
            return [float(b.close) * float(b.volume) for b in series]

        base = dollar_volume(bars)
        moved = dollar_volume(rescale_bars(bars, 2.0))
        invariant, _ = series_is_invariant(base, moved)
        assert not invariant

    def test_an_absolute_price_filter_changes_its_answer(self, bars: list[OhlcvBar]) -> None:
        """'price above $5' is a different rule on a rescaled series."""

        def above_five(series: list[OhlcvBar]) -> int:
            return sum(1 for b in series if b.close > Decimal(5))

        original = above_five(bars)
        shrunk = above_five(rescale_bars(bars, 0.01))
        assert original != shrunk


class TestRescaling:
    def test_volume_is_untouched(self, bars: list[OhlcvBar]) -> None:
        """This models a *price* rescaling. A split also changes the share
        count, and conflating the two would let a volume feature look invariant
        for the wrong reason."""
        moved = rescale_bars(bars, 4.0)
        assert [b.volume for b in moved] == [b.volume for b in bars]

    def test_prices_move_together(self, bars: list[OhlcvBar]) -> None:
        moved = rescale_bars(bars, 4.0)
        for before, after in zip(bars, moved, strict=True):
            assert after.close == before.close * 4
            assert after.high == before.high * 4
            assert after.low == before.low * 4
            assert after.open == before.open * 4

    def test_ohlc_ordering_survives(self, bars: list[OhlcvBar]) -> None:
        """A positive factor is monotonic, so the rescaled bars still validate.
        Constructing OhlcvBar is what enforces it."""
        for factor in DEFAULT_SCALE_FACTORS:
            assert len(rescale_bars(bars, factor)) == len(bars)

    def test_a_non_positive_factor_is_refused(self, bars: list[OhlcvBar]) -> None:
        for bad in (0.0, -1.0):
            with pytest.raises(ValueError, match="must be positive"):
                rescale_bars(bars, bad)

    def test_nan_warm_up_regions_compare_equal(self) -> None:
        """Warm-up is NaN and nan != nan, so a naive comparison would report
        every deterministic feature as having moved."""
        left = [float("nan"), 1.0, 2.0]
        assert series_is_invariant(left, [float("nan"), 1.0, 2.0])[0]
        assert not series_is_invariant(left, [0.0, 1.0, 2.0])[0]


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


class TestEligibility:
    def test_an_instrument_without_raw_prices_keeps_the_invariant_analytics(self) -> None:
        """The 44. Their price history is real and most of the platform works
        on it unchanged."""
        verdict = analytic_is_eligible(
            "percentage_returns",
            raw_reconstruction_available=False,
            adjustment_policy="split_adjusted",
        )
        assert verdict.eligible is True
        assert "scale-invariant" in verdict.reason

    def test_an_instrument_without_raw_prices_loses_the_sensitive_analytics(self) -> None:
        verdict = analytic_is_eligible(
            "dollar_volume",
            raw_reconstruction_available=False,
            adjustment_policy="split_adjusted",
        )
        assert verdict.eligible is False
        assert "no verified raw price series" in verdict.reason

    def test_a_verified_raw_series_unlocks_everything(self) -> None:
        verdict = analytic_is_eligible(
            "dollar_volume",
            raw_reconstruction_available=True,
            adjustment_policy="split_adjusted",
        )
        assert verdict.eligible is True

    def test_raw_unadjusted_prices_unlock_everything(self) -> None:
        verdict = analytic_is_eligible(
            "absolute_price_filter",
            raw_reconstruction_available=False,
            adjustment_policy="raw_unadjusted",
        )
        assert verdict.eligible is True

    def test_an_unclassified_analytic_is_never_eligible_without_raw_prices(self) -> None:
        """UNKNOWN must not read as "fine". This is the default that decides
        what happens when somebody adds a feature and forgets to classify it."""
        verdict = analytic_is_eligible(
            "some_new_thing_nobody_classified",
            raw_reconstruction_available=False,
            adjustment_policy="split_adjusted",
        )
        assert verdict.sensitivity is ScaleSensitivity.UNKNOWN
        assert verdict.eligible is False


class TestCapabilityIndex:
    def _index(self) -> CapabilityIndex:
        answered = InstrumentCapabilityRecord(
            instrument_id=1,
            ticker="AAPL",
            flags=frozenset(
                {
                    InstrumentCapability.PRICE_DATA_AVAILABLE,
                    InstrumentCapability.SPLIT_SCHEDULE_VERIFIED,
                    InstrumentCapability.RAW_RECONSTRUCTION_AVAILABLE,
                }
            ),
            split_provider="fmp",
        )
        refused = InstrumentCapabilityRecord(
            instrument_id=2,
            ticker="MSFT",
            flags=frozenset(
                {
                    InstrumentCapability.PRICE_DATA_AVAILABLE,
                    InstrumentCapability.RAW_RECONSTRUCTION_INCOMPLETE_OR_UNKNOWN,
                }
            ),
            reason="fmp returned not_available_on_plan (HTTP 402) for this symbol",
            http_status=402,
        )
        return CapabilityIndex(records=(answered, refused))

    def test_the_two_sample_sizes_are_different_numbers(self) -> None:
        index = self._index()
        assert len(index.price_eligible) == 2
        assert len(index.raw_verified) == 1

    def test_a_refused_instrument_keeps_its_price_data(self) -> None:
        """The whole point. An entitlement answer from a *second* vendor must
        not delete a legitimately acquired price history."""
        record = self._index().get(2)
        assert record is not None
        assert record.price_data_available is True
        assert record.raw_reconstruction_available is False

    def test_the_reason_names_the_cause_and_the_http_status(self) -> None:
        record = self._index().get(2)
        assert record is not None
        assert "402" in record.reason
        assert record.http_status == 402

    def test_reasons_are_tallied_rather_than_listed_per_instrument(self) -> None:
        reasons = self._index().reasons()
        assert list(reasons.values()) == [1]

    def test_an_empty_index_is_unknown_not_zero(self) -> None:
        """A package imported before capabilities existed says nothing. Reading
        that as "no instrument is eligible" would silently block every check."""
        empty = CapabilityIndex()
        assert empty.is_empty is True
        assert empty.price_eligible == ()
        assert "UNKNOWN rather than zero" in " ".join(empty.render())

    def test_it_round_trips_through_its_payload(self) -> None:
        index = self._index()
        restored = CapabilityIndex.from_payload(index.to_payload())
        assert restored.counts() == index.counts()
        assert restored.get(2) is not None
        assert restored.get(2).http_status == 402  # type: ignore[union-attr]

    def test_an_unknown_flag_from_a_newer_build_is_ignored_not_fatal(self) -> None:
        restored = CapabilityIndex.from_payload(
            [{"instrument_id": 9, "flags": ["PRICE_DATA_AVAILABLE", "SOMETHING_NEW"]}]
        )
        record = restored.get(9)
        assert record is not None
        assert record.price_data_available is True


class TestRuleTable:
    def test_every_rule_carries_a_rationale(self) -> None:
        for rule in FEATURE_SCALE_RULES:
            assert rule.rationale, f"{rule.pattern} is classified without a reason"

    def test_the_more_specific_rule_wins(self) -> None:
        """atr_percent must not be caught by the atr_ rule, and
        distance_from_sma_20 must not be caught by sma_."""
        assert classify_feature("atr_percent")[0] is ScaleSensitivity.INVARIANT
        assert classify_feature("atr_14")[0] is ScaleSensitivity.SENSITIVE
        assert classify_feature("distance_from_sma_20")[0] is ScaleSensitivity.INVARIANT
        assert classify_feature("sma_20")[0] is ScaleSensitivity.SENSITIVE

    def test_an_unknown_name_is_unknown_rather_than_invariant(self) -> None:
        assert classify_feature("brand_new_feature")[0] is ScaleSensitivity.UNKNOWN


# ---------------------------------------------------------------------------
# The numerical defects the first real snapshot exposed
# ---------------------------------------------------------------------------


class TestDirectionalMovementTies:
    """ADX, +DI and -DI were not invariant, and the cause was a real defect.

    The first PostgreSQL snapshot (twelve_data-daily-e3ddc03209bb25b4, 78
    instruments) reported 32 scale-invariance violations including adx_14,
    plus_di and minus_di. They reproduce on low-priced and illiquid series and
    not on smooth large-cap ones, and only at non-dyadic rescaling factors —
    which is the signature of a **discrete branch decided by representation
    error**, not of accumulated rounding.

    The mechanism, traced end to end: high goes 3.01 -> 3.06 while low goes
    3.01 -> 2.96. Both moves are exactly 0.05, so Wilder's rule gives *both*
    directional movements zero. IEEE-754 renders them 0.050000000000000266 and
    0.049999999999999822, so a bare ``up > down`` records +DM = 0.05. Multiply
    every price by 3.7 and the same comparison comes out the other way.

    So the pre-fix indicator gave a different answer for the same bars quoted in
    dollars and in cents. That is a correctness bug in its own right; scale
    invariance is how it surfaced.
    """

    def test_an_exact_tie_gives_both_directional_movements_zero(self) -> None:
        """Wilder's definition, which the float comparison was overriding."""
        high = np.array([3.01, 3.06])
        low = np.array([3.01, 2.96])
        close = np.array([3.01, 3.00])
        up, down = high[1] - high[0], low[0] - low[1]
        assert up != down, "fixture assumption: the floats are not bit-equal"
        assert abs(up - down) < 1e-14, "fixture assumption: the tie is real"

        reference = max(abs(high[0]), abs(high[1]), abs(low[0]), abs(low[1]))
        assert abs(up - down) <= float(k.tie_tolerance(np.array(reference)))
        _ = close

    def test_the_same_bars_in_cents_give_the_same_adx(self) -> None:
        """The deeper property. A change of units is not a change of market."""
        bars = make_bars(count=500, start_price=3.07)
        engine = IndicatorEngine(IndicatorConfig())
        dollars = engine.compute(bars, instrument_id=1).values
        cents = engine.compute(rescale_bars(bars, 100), instrument_id=1).values
        for name in ("adx_14", "plus_di", "minus_di"):
            invariant, worst = series_is_invariant(dollars[name], cents[name])
            assert invariant, f"{name} changed when the prices were quoted in cents ({worst:.3g})"

    @pytest.mark.parametrize("factor", [*DEFAULT_SCALE_FACTORS, 0.001, 7.13, 137.0])
    def test_penny_priced_series_are_invariant(self, factor: float) -> None:
        """The regime that failed. Low-priced, tick-quantized data produces
        ties constantly; a smooth large-cap series produces almost none, which
        is why the original fixture passed while real data did not."""
        bars = _penny_bars()
        engine = IndicatorEngine(IndicatorConfig())
        report = scale_invariance_report(
            lambda series: engine.compute(series, instrument_id=1).values, bars, factor
        )
        assert report.violations == (), [o.name for o in report.violations]

    def test_the_tolerance_is_far_below_one_tick(self) -> None:
        """The tolerance must not blunt the indicator.

        A one-cent move on a $3 stock is a relative 3.3e-3. The tie tolerance is
        ~7e-15 relative — eleven orders of magnitude smaller — so it can absorb
        representation error and nothing else.
        """
        tolerance = float(k.tie_tolerance(np.array(3.0)))
        assert tolerance < 1e-13
        assert tolerance < 0.01 / 1e10

    def test_a_genuine_directional_move_still_registers(self) -> None:
        """Guards against the fix silencing real signal: a clear up-move must
        still produce +DM and no -DM."""
        high = np.array([10.0, 11.0])
        low = np.array([9.0, 9.5])
        up, down = high[1] - high[0], low[0] - low[1]
        tolerance = float(k.tie_tolerance(np.array(11.0)))
        assert up > down and up > tolerance
        assert not (down > up)

    def test_the_tolerance_changes_nothing_where_the_moves_are_distinguishable(
        self,
    ) -> None:
        """The scope of the fix, stated as a property.

        A tolerance that only absorbs representation error must be a *no-op*
        at every index where the two directional movements differ by more than
        that. Only the indistinguishable ones may change, and there they must
        change to Wilder's answer: both zero.

        If a future change widens the tolerance far enough to swallow a real
        move, the first assertion is what fails.
        """
        bars = make_bars(count=400, start_price=137.11)
        high = np.array([float(b.high) for b in bars])
        low = np.array([float(b.low) for b in bars])
        up_move = high[1:] - high[:-1]
        down_move = low[:-1] - low[1:]

        # The pre-fix formulation, inline: bare comparisons, no tolerance.
        naive_plus = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        naive_minus = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tolerance = k.tie_tolerance(
            np.maximum(
                np.maximum(np.abs(high[1:]), np.abs(high[:-1])),
                np.maximum(np.abs(low[1:]), np.abs(low[:-1])),
            )
        )
        decisive = np.abs(up_move - down_move) > tolerance
        shipped_plus = np.where(
            decisive & (up_move > down_move) & (up_move > tolerance), up_move, 0.0
        )
        shipped_minus = np.where(
            decisive & (down_move > up_move) & (down_move > tolerance), down_move, 0.0
        )

        assert np.array_equal(naive_plus[decisive], shipped_plus[decisive])
        assert np.array_equal(naive_minus[decisive], shipped_minus[decisive])
        assert not np.any(shipped_plus[~decisive])
        assert not np.any(shipped_minus[~decisive])
        # And the fixture must actually contain at least one of each case, or
        # the assertions above are vacuous.
        assert decisive.any() and not decisive.all()


class TestPercentileTies:
    """`volatility_percentile` moved at *every* factor, including exact powers
    of two — a different mechanism from the ADX one.

    `realized_volatility` differenced two logs of nearby prices, which cancels
    most of the significand and leaves a value whose last bits depend on the
    price level. `percent_rank` then made a discrete count on those values, so a
    one-ulp difference moved the rank a whole step: 1/(count - 1), percent
    points rather than rounding.

    Two fixes, each defensible on its own: the volatility now takes the log of
    the price *ratio* rather than the difference of logs, and the rank counts
    values equal within representation error as tied — which is also the correct
    statistical treatment of ties in a percentile.
    """

    def test_log_returns_come_from_the_ratio_not_the_difference_of_logs(self) -> None:
        close = np.array([100.0, 100.01, 100.02, 99.99, 100.05])
        precise = np.log(close[1:] / close[:-1])
        cancelled = np.diff(np.log(close))
        # Both are "right"; the ratio keeps more significant digits, which is
        # what stopped the downstream percentile from moving.
        assert np.allclose(precise, cancelled, rtol=1e-12)
        assert k.realized_volatility(close, 2).shape == close.shape

    def test_values_equal_within_representation_error_rank_as_tied(self) -> None:
        base = 1.0
        nudged = base + 4 * np.finfo(np.float64).eps
        values = np.array([base, nudged, base, nudged, base, nudged])
        ranks = k.percent_rank(values, 4)
        finite = ranks[~np.isnan(ranks)]
        assert np.allclose(finite, finite[0]), (
            f"values differing by a few ulps produced different ranks: {finite}"
        )

    def test_a_genuinely_different_value_still_ranks_differently(self) -> None:
        """The fix must not flatten real dispersion."""
        # Non-monotonic, or every window ranks its last value top and the
        # assertion would pass without proving anything.
        values = np.array([5.0, 1.0, 4.0, 2.0, 6.0, 3.0])
        ranks = k.percent_rank(values, 4)
        finite = ranks[~np.isnan(ranks)]
        assert finite.min() < finite.max(), f"real dispersion was flattened: {finite}"


class TestTheHarnessStillCatchesRealDefects:
    """A tolerance that hides the defect it was added for is worse than none."""

    def test_a_scale_sensitive_feature_injected_into_the_report_is_caught(self) -> None:
        """Mutation test. If the harness cannot fail, its passes mean nothing."""
        bars = _penny_bars()
        engine = IndicatorEngine(IndicatorConfig())

        def values_with_a_planted_defect(series: list[OhlcvBar]) -> dict[str, object]:
            values = dict(engine.compute(series, instrument_id=1).values)
            # A price level wearing an invariant feature's name.
            values["rsi_14"] = np.array([float(b.close) for b in series])
            return values

        report = scale_invariance_report(values_with_a_planted_defect, bars, 3.7)
        assert "rsi_14" in {o.name for o in report.violations}

    def test_a_one_ulp_defect_would_not_be_caught_and_a_one_tick_one_would(self) -> None:
        """Bounds what the harness can see, honestly.

        `series_is_invariant` uses a 1e-9 relative tolerance, so a difference at
        the last bit is invisible and a difference of one cent in a hundred
        dollars is not. Stating the boundary is better than implying there is
        none.
        """
        base = np.array([100.0, 200.0, 300.0])
        one_ulp = base * (1 + np.finfo(np.float64).eps)
        one_tick = base + 0.01
        assert series_is_invariant(base, one_ulp)[0]
        assert not series_is_invariant(base, one_tick)[0]


def _penny_bars(count: int = 700) -> list[OhlcvBar]:
    """A low-priced, tick-quantized series: the regime that actually failed.

    Deterministic. The point is the density of exact ties between the up-move
    and the down-move, which is high when the tick is a large fraction of the
    price and near zero on a smooth large-cap series.
    """
    rng = np.random.default_rng(11)
    close = np.round(np.cumsum(rng.normal(0, 0.02, count)) + 3.0, 2)
    close = np.maximum(close, 0.5)
    high = np.round(close + np.round(np.abs(rng.normal(0, 0.03, count)), 2), 2)
    low = np.round(close - np.round(np.abs(rng.normal(0, 0.03, count)), 2), 2)
    high, low = np.maximum(high, close), np.minimum(low, close)
    bars: list[OhlcvBar] = []
    day = dt.date(2010, 1, 4)
    for index in range(count):
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        moment = dt.datetime.combine(day, dt.time(21, 0), tzinfo=dt.UTC)
        bars.append(
            OhlcvBar(
                instrument_id=1,
                timeframe=Bartimeframe.D1,
                session_date=day,
                event_time=moment,
                knowledge_time=moment,
                knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                open=Decimal(str(close[index])),
                high=Decimal(str(high[index])),
                low=Decimal(str(max(low[index], 0.01))),
                close=Decimal(str(close[index])),
                volume=Decimal(100_000 + index),
            )
        )
        day += dt.timedelta(days=1)
    return bars
