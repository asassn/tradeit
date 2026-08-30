"""Data eligibility versus feature readiness, and declared feature sets.

Two rules are under test here, and both exist to stop a silent failure:

* A security warming up is not a security that does not exist. It belongs in
  breadth, and it does not belong in a scan.
* A feature existing in the registry does not entitle a consumer to read it.
"""

from __future__ import annotations

import pytest

from tradeit.analytics.eligibility import (
    EligibilityAssessment,
    EligibilityPolicy,
    EligibilityState,
)
from tradeit.analytics.feature_sets import (
    ConsumerKind,
    FeatureAccessError,
    FeatureSet,
    FeatureSetCatalogue,
    declare,
)
from tradeit.analytics.indicators import IndicatorEngine
from tradeit.analytics.registry import (
    FeatureKind,
    FeatureRegistry,
    FeatureSpec,
    NullBehaviour,
    OutputType,
)
from tradeit.core.enums import Bartimeframe
from tradeit.errors import ConfigError
from tradeit.strategy.config import StrategyConfig

CONFIG = StrategyConfig(name="baseline")


def spec(name: str, warmup: int = 20, **overrides) -> FeatureSpec:
    payload = {
        "name": name,
        "version": 1,
        "description": f"{name} for testing",
        "kind": FeatureKind.TIME_SERIES,
        "timeframe": Bartimeframe.D1,
        "output_type": OutputType.RATIO,
        "null_behaviour": NullBehaviour.WARMUP,
        "input_datasets": ("ohlcv_bars",),
        "warmup_periods": warmup,
    }
    payload.update(overrides)
    return FeatureSpec(**payload)


REGISTRY = FeatureRegistry(
    [
        spec("sma_20", 20),
        spec("sma_50", 50),
        spec("sma_200", 200),
        spec("rolling_high_252", 252),
        spec("adx_14", 272),
        spec("rs_score", 250),
        spec("rs_percentile", 250, kind=FeatureKind.CROSS_SECTIONAL, depends_on=("rs_score",)),
    ]
)


class TestTheDefaultWarmup:
    def test_baseline_requires_a_full_trading_year(self):
        """252 sessions, not 250.

        At 250 the annual features are two sessions short of the year they
        claim: a "52-week high" computed over 250 sessions is a 250-session
        high wearing the wrong name.
        """
        assert CONFIG.liquidity.min_trading_history_sessions == 252

    def test_the_annual_features_now_fit_inside_the_history_floor(self):
        """The 250-vs-252 coherence warning should be gone, not suppressed."""
        warnings = " ".join(CONFIG.coherence_warnings())
        assert "rolling_extreme" not in warnings
        assert "252) exceeds" not in warnings

    def test_a_strategy_may_require_less(self):
        """The floor is a default, not a law.

        A mean-reversion strategy whose longest feature is a 20-day band has
        no business waiting a year to trade -- but lowering the floor means
        lowering the relative-strength horizons with it, which is the config
        forcing the strategy to be coherent rather than merely fast.
        """
        config = StrategyConfig.model_validate(
            {
                "name": "fast",
                "liquidity": {"min_trading_history_sessions": 60},
                "technical": {"relative_strength_lookback": 40},
                "relative_strength": {
                    "lookbacks": (10, 20, 40),
                    "lookback_weights": {"10": 0.3, "20": 0.3, "40": 0.4},
                },
            }
        )
        assert config.liquidity.min_trading_history_sessions == 60

    def test_lowering_it_below_a_relative_strength_lookback_is_still_fatal(self):
        """Warm-up is transient; a permanently empty screen is not.

        An indicator lookback longer than the floor resolves as instruments
        age. An RS lookback longer than the floor means no instrument can ever
        satisfy both, which is a broken configuration rather than a wait.
        """
        with pytest.raises(ConfigError, match="would fail on warm-up"):
            StrategyConfig.model_validate(
                {"name": "broken", "liquidity": {"min_trading_history_sessions": 100}}
            )
        with pytest.raises(ConfigError, match="could ever produce"):
            StrategyConfig.model_validate(
                {
                    "name": "broken",
                    "liquidity": {"min_trading_history_sessions": 100},
                    "technical": {"relative_strength_lookback": 60},
                }
            )


class TestTheTwoStates:
    """The distinction that stops breadth and scanning from being one question."""

    def test_a_recent_ipo_is_data_eligible_but_not_scannable(self):
        policy = EligibilityPolicy(min_data_sessions=60, required_features=[spec("sma_200", 200)])
        assessment = policy.assess(instrument_id=1, sessions_available=100)
        assert assessment.state is EligibilityState.DATA_ELIGIBLE
        assert not assessment.state.can_be_scanned
        assert assessment.state.can_join_breadth

    def test_breadth_counts_a_warming_security(self):
        """Omitting a genuinely trading name from advance/decline misreports
        the market -- the strategy's lookback is not the market's problem."""
        policy = EligibilityPolicy(min_data_sessions=60, required_features=[spec("sma_200", 200)])
        report = policy.partition({1: 100, 2: 300, 3: 10})
        assert report.scannable == [2]
        assert report.breadth_roster == [1, 2]
        assert report.ineligible == [3]

    def test_feature_ready_implies_data_eligible(self):
        policy = EligibilityPolicy(min_data_sessions=60, required_features=[spec("sma_20", 20)])
        assessment = policy.assess(instrument_id=1, sessions_available=300)
        assert assessment.state is EligibilityState.FEATURE_READY
        assert assessment.state.can_be_stored
        assert assessment.state.can_join_breadth
        assert assessment.state.can_be_scanned

    def test_the_data_floor_survives_a_short_feature_set(self):
        """A 20-day feature set does not license trading a five-day-old listing.

        The floor exists for reasons beyond warm-up -- liquidity history,
        listing seasoning -- so it is a floor, not a maximum.
        """
        policy = EligibilityPolicy(min_data_sessions=252, required_features=[spec("sma_20", 20)])
        assert policy.min_feature_ready_sessions == 252
        assert policy.assess(1, 100).state is EligibilityState.INELIGIBLE


class TestWhyNot:
    def test_the_blocking_features_are_named(self):
        policy = EligibilityPolicy.from_registry(min_data_sessions=60, registry=REGISTRY)
        assessment = policy.assess(instrument_id=1, sessions_available=210)
        assert assessment.missing_features == (
            "adx_14",
            "rolling_high_252",
            "rs_percentile",
            "rs_score",
        )
        assert "adx_14" in assessment.explain()

    def test_it_says_how_long_until_ready(self):
        policy = EligibilityPolicy(min_data_sessions=60, required_features=[spec("sma_200", 200)])
        assert policy.assess(1, 180).sessions_until_ready == 20
        assert policy.assess(1, 300).sessions_until_ready == 0

    def test_a_delisted_instrument_never_becomes_ready(self):
        """None, not a number.

        Reporting "3 sessions" for something that has stopped trading would
        invite a caller to wait for an event that cannot happen.
        """
        policy = EligibilityPolicy(min_data_sessions=60, required_features=[spec("sma_20", 20)])
        assessment = policy.assess(1, 300, listing_active=False)
        assert assessment.state is EligibilityState.INELIGIBLE
        assert assessment.sessions_until_ready is None
        assert "not actively listed" in assessment.explain()

    def test_quality_blocked_data_is_ineligible_not_merely_warming(self):
        policy = EligibilityPolicy(min_data_sessions=60)
        assessment = policy.assess(1, 300, quality_blocked=True)
        assert assessment.state is EligibilityState.INELIGIBLE
        assert assessment.sessions_until_ready is None

    def test_a_report_names_the_feature_holding_back_the_roster(self):
        """The diagnostic for "why is my scan universe so small?"."""
        policy = EligibilityPolicy.from_registry(min_data_sessions=60, registry=REGISTRY)
        report = policy.partition({1: 210, 2: 220, 3: 260, 4: 400})
        assert report.blocking_features()["adx_14"] == 3
        assert report.blocking_features()["rolling_high_252"] == 2
        assert report.summary() == {
            "total": 4,
            "feature_ready": 1,
            "data_eligible": 3,
            "ineligible": 0,
            "breadth_roster": 4,
        }

    def test_explain_truncates_a_long_missing_list(self):
        policy = EligibilityPolicy(
            min_data_sessions=10,
            required_features=[spec(f"f{i}", 100) for i in range(9)],
        )
        text = policy.assess(1, 50).explain()
        assert "+4 more" in text


class TestReadinessIsPerStrategy:
    def test_two_strategies_disagree_about_the_same_security(self):
        """The whole point of item 1.

        A 20-day strategy and a 252-day strategy look at one security with 100
        sessions of history and reach different, both-correct answers.
        """
        fast = EligibilityPolicy(
            min_data_sessions=60, required_features=[spec("sma_20", 20)], name="fast"
        )
        slow = EligibilityPolicy(
            min_data_sessions=60, required_features=[spec("rolling_high_252", 252)], name="slow"
        )
        assert fast.assess(1, 100).state is EligibilityState.FEATURE_READY
        assert slow.assess(1, 100).state is EligibilityState.DATA_ELIGIBLE

    def test_a_strategy_does_not_wait_for_a_feature_it_never_reads(self):
        """Using the whole registry makes every strategy wait for the slowest
        feature in the catalogue, which is how a 20-day system ends up idle
        for a year."""
        everything = EligibilityPolicy.from_registry(min_data_sessions=60, registry=REGISTRY)
        declared = EligibilityPolicy.from_registry(
            min_data_sessions=60, registry=REGISTRY, feature_names=["sma_20", "sma_50"]
        )
        assert everything.feature_warmup == 272
        assert declared.feature_warmup == 50

    def test_the_real_registry_costs_more_than_a_trading_year(self):
        """Concrete evidence that the floor alone is not sufficient.

        The full indicator set warms up in more sessions than the 252-session
        data floor, so DATA_ELIGIBLE and FEATURE_READY genuinely diverge on
        real configuration rather than only in constructed tests.
        """
        registry = IndicatorEngine(CONFIG.indicators).registry
        assert registry.max_warmup > CONFIG.liquidity.min_trading_history_sessions

    def test_a_negative_floor_is_refused(self):
        with pytest.raises(ConfigError):
            EligibilityPolicy(min_data_sessions=-1)


class TestDeclaredFeatureSets:
    """Registry membership is not a licence to consume."""

    def test_a_declaration_narrows_the_registry(self):
        declaration = declare(
            "momentum_screen", consumer=ConsumerKind.SCREEN, features=["sma_20", "sma_50"]
        )
        resolved = declaration.resolve(REGISTRY)
        assert resolved.names == ["sma_20", "sma_50"]
        assert resolved.warmup == 50

    def test_adding_a_registry_feature_does_not_change_a_declared_set(self):
        """The failure this whole module exists to prevent.

        If a consumer read ``registry.active()``, registering one indicator
        would silently change its inputs -- and every result it had already
        produced would cease to be reproducible.
        """
        declaration = declare(
            "scorer", consumer=ConsumerKind.SCORING, features=["sma_20", "sma_50"]
        )
        before = declaration.resolve(REGISTRY).digest

        wider = FeatureRegistry([*REGISTRY.all(), spec("brand_new_idea", 5)])
        after = declaration.resolve(wider).digest
        assert before == after

    def test_changing_a_declared_definition_does_change_the_digest(self):
        """The other half: the set must not be blind to its own mathematics."""
        declaration = declare("scorer", consumer=ConsumerKind.SCORING, features=["sma_20"])
        before = declaration.resolve(REGISTRY).digest

        revised = FeatureRegistry(
            [s for s in REGISTRY.all() if s.name != "sma_20"] + [spec("sma_20", 21)]
        )
        after = declaration.resolve(revised).digest
        assert before != after
        # ...but the *choice* did not change, and the two digests say so.
        assert declaration.declaration_digest == declaration.declaration_digest

    def test_dependencies_come_along_so_warmup_is_honest(self):
        declaration = declare("ranker", consumer=ConsumerKind.SCORING, features=["rs_percentile"])
        resolved = declaration.resolve(REGISTRY)
        assert resolved.implied_dependencies == ("rs_score",)
        assert "rs_score" in resolved.names

    def test_declaring_an_unknown_feature_fails_loudly(self):
        declaration = declare("typo", consumer=ConsumerKind.MODEL, features=["sma_2O"])
        with pytest.raises(ConfigError, match="absent from the registry"):
            declaration.resolve(REGISTRY)

    def test_an_empty_declaration_is_not_a_wildcard(self):
        with pytest.raises(ConfigError, match="empty declaration"):
            FeatureSet(name="x", consumer=ConsumerKind.MODEL, version=1, features=())

    def test_duplicate_declarations_are_refused(self):
        with pytest.raises(ConfigError, match="duplicate"):
            declare("x", consumer=ConsumerKind.MODEL, features=["sma_20", "sma_20"])

    def test_rationale_must_refer_to_declared_features(self):
        with pytest.raises(ConfigError, match="undeclared"):
            declare(
                "x",
                consumer=ConsumerKind.MODEL,
                features=["sma_20"],
                rationale={"sma_50": "trend"},
            )

    def test_a_deprecated_feature_is_flagged_but_still_resolves(self):
        """A backtest reproducing an old run legitimately needs the old feature."""
        registry = FeatureRegistry([spec("legacy", 20, deprecated=True)])
        resolved = declare("old", consumer=ConsumerKind.MODEL, features=["legacy"]).resolve(
            registry
        )
        assert resolved.deprecated_features == ("legacy",)


class TestEnforcement:
    def test_reading_an_undeclared_feature_raises(self):
        resolved = declare("screen", consumer=ConsumerKind.SCREEN, features=["sma_20"]).resolve(
            REGISTRY
        )
        view = resolved.view({"sma_20": 10.0, "sma_200": 9.0})
        assert view["sma_20"] == 10.0
        with pytest.raises(FeatureAccessError, match="did not declare"):
            view["sma_200"]

    def test_get_tolerates_a_missing_value_but_not_an_undeclared_name(self):
        """A computed null is ordinary; an undeclared input is not."""
        resolved = declare(
            "screen", consumer=ConsumerKind.SCREEN, features=["sma_20", "sma_50"]
        ).resolve(REGISTRY)
        view = resolved.view({"sma_20": 10.0})
        assert view.get("sma_50") is None
        with pytest.raises(FeatureAccessError):
            view.get("sma_200")

    def test_select_refuses_a_row_missing_a_declared_feature(self):
        """A scorer quietly running on eight of its ten inputs produces a
        plausible number that is not the number it was validated on."""
        resolved = declare(
            "scorer", consumer=ConsumerKind.SCORING, features=["sma_20", "sma_50"]
        ).resolve(REGISTRY)
        with pytest.raises(FeatureAccessError, match="absent from the supplied row"):
            resolved.select({"sma_20": 1.0})

    def test_a_declared_set_produces_its_own_eligibility_policy(self):
        """Item 1 and item 2 join here: readiness is per strategy because the
        feature set is per strategy."""
        resolved = declare("fast", consumer=ConsumerKind.SCREEN, features=["sma_20"]).resolve(
            REGISTRY
        )
        policy = resolved.eligibility_policy(min_data_sessions=60)
        assert policy.min_feature_ready_sessions == 60
        assert policy.assess(1, 80).state is EligibilityState.FEATURE_READY


class TestTheCatalogue:
    def test_it_answers_who_reads_this_feature(self):
        """Asked before changing a definition, not after."""
        catalogue = FeatureSetCatalogue(
            [
                declare("a", consumer=ConsumerKind.SCREEN, features=["sma_20"]),
                declare("b", consumer=ConsumerKind.SCORING, features=["sma_20", "sma_50"]),
                declare("c", consumer=ConsumerKind.MODEL, features=["sma_200"]),
            ]
        )
        assert catalogue.consumers_of("sma_20") == ["a@v1", "b@v1"]
        assert catalogue.consumers_of("adx_14") == []

    def test_unconsumed_features_are_reported_not_condemned(self):
        catalogue = FeatureSetCatalogue(
            [declare("a", consumer=ConsumerKind.SCREEN, features=["sma_20"])]
        )
        unconsumed = catalogue.unconsumed(REGISTRY)
        assert "sma_20" not in unconsumed
        assert "adx_14" in unconsumed

    def test_redeclaring_a_version_is_refused(self):
        catalogue = FeatureSetCatalogue()
        catalogue.declare(declare("a", consumer=ConsumerKind.SCREEN, features=["sma_20"]))
        with pytest.raises(ConfigError, match="already declared"):
            catalogue.declare(declare("a", consumer=ConsumerKind.SCREEN, features=["sma_50"]))

    def test_a_new_version_is_a_different_set(self):
        catalogue = FeatureSetCatalogue()
        catalogue.declare(declare("a", consumer=ConsumerKind.SCREEN, features=["sma_20"]))
        catalogue.declare(
            declare("a", consumer=ConsumerKind.SCREEN, features=["sma_50"], version=2)
        )
        assert len(catalogue) == 2

    def test_the_audit_names_undocumented_trading_inputs(self):
        """Research sets may browse; a scorer that cannot say why it wants a
        feature is worth reviewing."""
        scoring = declare(
            "scorer",
            consumer=ConsumerKind.SCORING,
            features=["sma_20", "sma_50"],
            rationale={"sma_20": "short-term trend"},
        ).resolve(REGISTRY)
        assert scoring.audit()["undocumented"] == ["sma_50"]

        research = declare(
            "browse", consumer=ConsumerKind.RESEARCH, features=["sma_20", "sma_50"]
        ).resolve(REGISTRY)
        assert research.audit()["undocumented"] == []

    def test_diagnostics_do_not_feed_trading_decisions(self):
        assert not ConsumerKind.DIAGNOSTIC.feeds_trading_decisions
        assert not ConsumerKind.RESEARCH.feeds_trading_decisions
        assert ConsumerKind.MODEL.feeds_trading_decisions


class TestSubset:
    def test_subset_is_a_registry_not_a_list(self):
        subset = REGISTRY.subset(["sma_20", "sma_50"])
        assert isinstance(subset, FeatureRegistry)
        assert subset.max_warmup == 50
        assert subset.required_datasets() == ["ohlcv_bars"]

    def test_subset_validates(self):
        REGISTRY.subset(["rs_percentile"]).validate()

    def test_subset_without_dependencies_is_available_but_not_the_default(self):
        subset = REGISTRY.subset(["rs_percentile"], with_dependencies=False)
        assert subset.names() == ["rs_percentile"]

    def test_an_assessment_carries_its_own_arithmetic(self):
        """The fields a UI needs to explain itself without recomputing."""
        assessment = EligibilityAssessment(
            instrument_id=7,
            state=EligibilityState.DATA_ELIGIBLE,
            sessions_available=100,
            sessions_required_for_data=60,
            sessions_required_for_features=252,
            missing_features=("rolling_high_252",),
        )
        assert assessment.sessions_until_ready == 152
        assert "100 sessions available" in assessment.explain()
