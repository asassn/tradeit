"""The feature registry: identity, availability, and null semantics.

The registry is what makes a stored feature value interpretable two years later.
These tests pin the properties that matter for that: a changed definition must
change the digest, a dependency that warms up later than its dependent must be
refused, and a null must carry a reason.
"""

from __future__ import annotations

import pytest

from tradeit.analytics.breadth import BreadthEngine
from tradeit.analytics.indicators import IndicatorEngine
from tradeit.analytics.regime import MarketRegimeEngine
from tradeit.analytics.registry import (
    FeatureKind,
    FeatureRegistry,
    FeatureSpec,
    NullBehaviour,
    OutputType,
    merge,
)
from tradeit.analytics.relative_strength import RelativeStrengthEngine
from tradeit.analytics.sectors import SectorStrengthEngine
from tradeit.analytics.volatility import VolatilityRegimeEngine
from tradeit.core.enums import ArtifactKind, Bartimeframe
from tradeit.errors import ConfigError
from tradeit.strategy.config import StrategyConfig

CONFIG = StrategyConfig(name="baseline")


def spec(name: str, **overrides) -> FeatureSpec:
    payload = {
        "name": name,
        "version": 1,
        "description": f"{name} for testing",
        "kind": FeatureKind.TIME_SERIES,
        "timeframe": Bartimeframe.D1,
        "output_type": OutputType.RATIO,
        "null_behaviour": NullBehaviour.WARMUP,
        "input_datasets": ("ohlcv_bars",),
        "warmup_periods": 20,
    }
    payload.update(overrides)
    return FeatureSpec(**payload)


class TestSpecIdentity:
    def test_parameters_are_part_of_the_identity(self):
        """rs_score over 120 sessions is a different feature from one over 250.

        Storing both under one name makes the resulting dataset unusable for
        backtesting and actively dangerous for model training.
        """
        short = spec("rs_score", parameters={"lookback": 120})
        long = spec("rs_score", parameters={"lookback": 250})
        assert short.digest != long.digest

    def test_description_is_not_part_of_the_identity(self):
        """Prose is documentation, not definition."""
        assert spec("x").digest == spec("x", description="rewritten entirely").digest

    def test_calculation_version_changes_the_identity(self):
        """A bug fix with unchanged parameters is still a different feature;
        corrected values must not silently mix with the old ones."""
        assert spec("x").digest != spec("x", calculation_version=2).digest

    def test_the_qualified_name_carries_both_versions(self):
        assert spec("x", version=2, calculation_version=3).qualified_name == "x@v2.3"

    def test_a_feature_must_declare_its_inputs(self):
        """Without them the pipeline cannot tell which ingestion runs gate it
        or which data-quality issues invalidate it."""
        with pytest.raises(ConfigError, match="input datasets"):
            spec("x", input_datasets=())

    def test_negative_warmup_is_refused(self):
        with pytest.raises(ConfigError, match="warmup_periods"):
            spec("x", warmup_periods=-1)

    def test_availability_is_reported_against_history_length(self):
        feature = spec("x", warmup_periods=200)
        assert not feature.is_available(199)
        assert feature.is_available(200)


class TestRegistry:
    def test_duplicate_registration_is_refused(self):
        """Shadowing would silently change what a name means mid-run."""
        registry = FeatureRegistry([spec("x")])
        with pytest.raises(ConfigError, match="already registered"):
            registry.register(spec("x"))

    def test_unknown_lookup_fails_clearly(self):
        with pytest.raises(ConfigError, match="unknown feature"):
            FeatureRegistry().get("nope")

    def test_max_warmup_is_the_history_a_backtest_must_skip(self):
        registry = FeatureRegistry([spec("a", warmup_periods=50), spec("b", warmup_periods=252)])
        assert registry.max_warmup == 252

    def test_deprecated_features_leave_the_active_set(self):
        registry = FeatureRegistry([spec("a"), spec("b", deprecated=True, warmup_periods=500)])
        assert registry.names() == ["a", "b"]
        assert [s.name for s in registry.active()] == ["a"]
        assert registry.max_warmup == 20, "a deprecated feature must not gate warm-up"

    def test_required_datasets_are_aggregated(self):
        registry = FeatureRegistry(
            [
                spec("a", input_datasets=("ohlcv_bars",)),
                spec("b", input_datasets=("sectors", "universe_memberships")),
            ]
        )
        assert registry.required_datasets() == ["ohlcv_bars", "sectors", "universe_memberships"]

    def test_a_dangling_dependency_is_detected(self):
        registry = FeatureRegistry([spec("a", depends_on=("ghost",))])
        with pytest.raises(ConfigError, match="unknown feature 'ghost'"):
            registry.validate()

    def test_a_dependency_cycle_is_detected(self):
        registry = FeatureRegistry([spec("a", depends_on=("b",)), spec("b", depends_on=("a",))])
        with pytest.raises(ConfigError, match="cycle"):
            registry.validate()

    def test_a_dependent_warming_up_sooner_than_its_input_is_refused(self):
        """It would compute from undefined inputs and look like a data problem
        rather than a definition problem."""
        registry = FeatureRegistry(
            [
                spec("slow_input", warmup_periods=200),
                spec("fast_output", warmup_periods=50, depends_on=("slow_input",)),
            ]
        )
        with pytest.raises(ConfigError, match="would compute from undefined inputs"):
            registry.validate()

    def test_a_valid_dependency_chain_passes(self):
        registry = FeatureRegistry(
            [
                spec("base", warmup_periods=50),
                spec("derived", warmup_periods=60, depends_on=("base",)),
            ]
        )
        registry.validate()


class TestFeatureSetDigest:
    def test_the_digest_changes_when_a_definition_changes(self):
        """The mechanism that keeps recomputed values from silently overwriting
        values computed under different rules."""
        before = FeatureRegistry([spec("a", parameters={"period": 50})])
        after = FeatureRegistry([spec("a", parameters={"period": 60})])
        assert before.digest != after.digest

    def test_the_digest_is_order_independent(self):
        left = FeatureRegistry([spec("a"), spec("b")])
        right = FeatureRegistry([spec("b"), spec("a")])
        assert left.digest == right.digest

    def test_the_digest_ignores_deprecated_features(self):
        active = FeatureRegistry([spec("a")])
        with_deprecated = FeatureRegistry([spec("a"), spec("z", deprecated=True)])
        assert active.digest == with_deprecated.digest

    def test_the_version_is_a_feature_set_artifact(self):
        version = FeatureRegistry([spec("a")]).version("analytics_v1")
        assert version.kind is ArtifactKind.FEATURE_SET
        assert version.label.startswith("analytics_v1@")

    def test_diff_reports_added_removed_and_changed(self):
        before = FeatureRegistry([spec("keep"), spec("drop"), spec("tweak", warmup_periods=10)])
        after = FeatureRegistry([spec("keep"), spec("add"), spec("tweak", warmup_periods=20)])
        assert before.diff(after) == {
            "added": ["add"],
            "removed": ["drop"],
            "changed": ["tweak"],
        }


class TestEngineRegistries:
    """Every engine must declare what it produces."""

    def test_each_engine_registers_a_valid_registry(self):
        engines = {
            "indicators": IndicatorEngine(CONFIG.indicators),
            "relative_strength": RelativeStrengthEngine(CONFIG.relative_strength),
            "sectors": SectorStrengthEngine(CONFIG.sector_strength),
            "breadth": BreadthEngine(CONFIG.breadth),
            "regime": MarketRegimeEngine(CONFIG.regime),
            "volatility": VolatilityRegimeEngine(CONFIG.volatility_regime),
        }
        for name, engine in engines.items():
            engine.registry.validate()
            assert len(engine.registry) > 0, f"{name} declares no features"

    def test_the_engines_merge_into_one_feature_set(self):
        """No two engines may claim the same feature name."""
        merged = merge(
            [
                IndicatorEngine(CONFIG.indicators).registry,
                RelativeStrengthEngine(CONFIG.relative_strength).registry,
                SectorStrengthEngine(CONFIG.sector_strength).registry,
                BreadthEngine(CONFIG.breadth).registry,
                MarketRegimeEngine(CONFIG.regime).registry,
                VolatilityRegimeEngine(CONFIG.volatility_regime).registry,
            ]
        )
        assert len(merged) > 100
        assert merged.digest

    def test_every_declared_feature_names_its_null_semantics(self):
        """A null with no stated meaning invites imputation, and imputing zero
        turns missing data into a strong signal."""
        engine = IndicatorEngine(CONFIG.indicators)
        for feature in engine.registry.all():
            assert feature.null_behaviour in set(NullBehaviour)

    def test_cross_sectional_features_are_marked_as_such(self):
        """The marker that says 'this leaks across instruments if the universe
        is not point-in-time'."""
        registry = RelativeStrengthEngine(CONFIG.relative_strength).registry
        cross = {s.name for s in registry.of_kind(FeatureKind.CROSS_SECTIONAL)}
        assert "rs_score" in cross
        assert any(name.startswith("rs_universe_percentile_") for name in cross)

    def test_the_indicator_engine_computes_exactly_what_it_declares(self):
        """A value reaching storage with no registry entry would be
        uninterpretable later; the engine raises rather than allowing it."""
        import datetime as dt
        from decimal import Decimal

        from tradeit.core.calendar import get_calendar
        from tradeit.core.enums import KnowledgeTimeSource
        from tradeit.core.models import OhlcvBar

        cal = get_calendar()
        sessions = cal.sessions_between(dt.date(2022, 1, 3), dt.date(2024, 6, 28))
        bars = [
            OhlcvBar(
                instrument_id=1,
                timeframe=Bartimeframe.D1,
                session_date=day,
                event_time=cal.close_instant(day),
                knowledge_time=cal.close_instant(day) + dt.timedelta(minutes=20),
                knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1000000"),
            )
            for day in sessions
        ]
        engine = IndicatorEngine(CONFIG.indicators)
        series = engine.compute(bars)
        assert set(series.values) == set(engine.registry.names())
        assert series.feature_set_digest == engine.registry.digest
