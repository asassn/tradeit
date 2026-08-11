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

import pytest

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
