from __future__ import annotations

from pathlib import Path

import pytest

from tradeit.errors import ConfigError
from tradeit.strategy.config import StrategyConfig

BASELINE = Path("config/strategies/baseline.toml")


@pytest.fixture
def config() -> StrategyConfig:
    return StrategyConfig.from_toml(BASELINE)


class TestLoading:
    def test_the_shipped_baseline_loads(self, config):
        assert config.name == "baseline"
        assert config.risk.max_positions > 0

    def test_the_file_matches_the_code_defaults(self, config):
        """Guards against the file and the model drifting apart.

        If a default changes in code but not in the file, a deployment reading
        the file and a test constructing defaults are running different
        strategies while appearing to run one.
        """
        assert config.digest == StrategyConfig(name="baseline").digest

    def test_loading_is_deterministic(self, config):
        assert config.digest == StrategyConfig.from_toml(BASELINE).digest

    def test_a_missing_file_is_a_clear_error(self):
        with pytest.raises(ConfigError, match="not found"):
            StrategyConfig.from_toml("config/strategies/nope.toml")

    def test_an_unknown_key_is_rejected_not_ignored(self):
        """A typo'd key silently ignored leaves the strategy on defaults while
        its author believes otherwise."""
        payload = StrategyConfig(name="x").model_dump(mode="json")
        payload["sizing"]["risk_per_trade_pcnt"] = 0.01
        with pytest.raises(Exception, match=r"[Ee]xtra"):
            StrategyConfig.from_mapping(payload)


class TestIdentity:
    def test_changing_a_threshold_changes_the_digest(self, config):
        tweaked = config.with_overrides(**{"breakout.min_volume_ratio": 2.0})
        assert tweaked.digest != config.digest
        assert config.differs_from(tweaked) == ["breakout.min_volume_ratio"]

    def test_editing_the_description_does_not_change_the_strategy(self, config):
        """Prose is not a rule. Treating it as one would break the link between
        a live session and the backtest that validated it."""
        reworded = config.model_copy(update={"description": "completely rewritten"})
        assert reworded.digest == config.digest

    def test_renaming_changes_identity_because_the_name_is_hashed(self, config):
        assert config.model_copy(update={"name": "other"}).digest != config.digest

    def test_weights_are_normalised_before_hashing(self):
        """Two weight sets in the same proportion are the same strategy."""
        base = StrategyConfig(name="w")
        doubled = base.model_copy(
            update={
                "scoring": base.scoring.model_copy(
                    update={"weights": {k: v * 2 for k, v in base.scoring.weights.items()}}
                )
            }
        )
        assert doubled.digest == base.digest

    def test_overrides_return_a_new_object(self, config):
        tweaked = config.with_overrides(**{"risk.max_positions": 20})
        assert config.risk.max_positions == 12
        assert tweaked.risk.max_positions == 20

    def test_an_unknown_override_key_is_refused(self, config):
        with pytest.raises(ConfigError, match="unknown configuration key"):
            config.with_overrides(**{"risk.max_postions": 20})

    def test_an_unknown_override_section_is_refused(self, config):
        with pytest.raises(ConfigError, match="unknown configuration section"):
            config.with_overrides(**{"rsk.max_positions": 20})


class TestSectionValidation:
    def test_negative_risk_per_trade_is_refused(self):
        with pytest.raises(Exception):
            StrategyConfig(name="x").with_overrides(**{"sizing.risk_per_trade_pct": -0.01})

    def test_an_inverted_price_band_is_refused(self):
        with pytest.raises(ConfigError, match="max_price must exceed"):
            StrategyConfig(name="x").with_overrides(**{"liquidity.max_price": 1.0})

    def test_an_inverted_base_length_range_is_refused(self):
        with pytest.raises(ConfigError, match="max_base_sessions"):
            StrategyConfig(name="x").with_overrides(**{"patterns.max_base_sessions": 10})

    def test_empty_scoring_weights_are_refused(self):
        with pytest.raises(ConfigError, match="cannot be empty"):
            StrategyConfig(name="x").with_overrides(**{"scoring.weights": {}})

    def test_participation_rate_above_one_is_refused(self):
        with pytest.raises(Exception):
            StrategyConfig(name="x").with_overrides(**{"liquidity.max_participation_rate": 1.5})


class TestCrossSectionCoherence:
    """Constraints no single section can enforce alone.

    A configuration can satisfy every field constraint and still be incoherent
    as a whole; these catch that.
    """

    def test_per_trade_risk_exceeding_portfolio_heat_is_refused(self):
        with pytest.raises(ConfigError, match="no position could ever be opened"):
            StrategyConfig(name="x").with_overrides(
                **{"sizing.risk_per_trade_pct": 0.04, "risk.max_portfolio_heat_pct": 0.02}
            )

    def test_a_trailing_stop_looser_than_breakeven_is_refused(self):
        with pytest.raises(ConfigError, match="looser than the breakeven stop"):
            StrategyConfig(name="x").with_overrides(
                **{"exits.move_stop_to_breakeven_at_r": 2.0, "exits.activate_trailing_at_r": 1.0}
            )

    def test_a_position_larger_than_gross_exposure_is_refused(self):
        with pytest.raises(ConfigError, match="gross exposure limit"):
            StrategyConfig(name="x").with_overrides(
                **{"sizing.max_position_pct_of_equity": 0.9, "risk.max_gross_exposure_pct": 0.5}
            )

    def test_a_lookback_longer_than_required_history_is_refused(self):
        """Otherwise every instrument passing the liquidity filter fails on
        warm-up, and the screen silently returns nothing."""
        with pytest.raises(ConfigError, match="would fail on warm-up"):
            StrategyConfig(name="x").with_overrides(
                **{
                    "technical.relative_strength_lookback": 300,
                    "liquidity.min_trading_history_sessions": 250,
                }
            )

    def test_the_baseline_satisfies_every_cross_section_rule(self, config):
        assert config.digest  # construction already ran the validators
