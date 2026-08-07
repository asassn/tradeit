"""Versioned, file-backed strategy configuration.

The rule this module exists to enforce: **no number that affects a trading
decision may be written in Python.** Not a default argument, not a module
constant, not a magic literal inside a detector. Every threshold, weight,
lookback and limit lives in a configuration file, is validated on load, and is
identified by the hash of its contents.

Three consequences follow, and each is the point rather than a side effect:

* A backtest and a live session that share a config hash provably ran the same
  rules. Nothing depends on anyone remembering what was changed.
* Changing a threshold is a reviewable diff, not a code change buried in a
  commit that also refactors something.
* Parameter sweeps and walk-forward optimisation generate configurations rather
  than patching globals, so every variation tried is enumerable — which is the
  only defence against reporting the best of two hundred attempts as if it were
  the first of one.

TOML is used because ``tomllib`` is in the standard library and because its
strictness about types is a feature here: a threshold silently parsed as a
string is a bug that surfaces months later.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradeit.core.enums import ArtifactKind
from tradeit.errors import ConfigError
from tradeit.reproducibility.versioning import ArtifactVersion, content_hash


class Section(BaseModel):
    """Base for configuration sections.

    ``extra="forbid"`` is doing real work: a typo'd key in a config file would
    otherwise be silently ignored, leaving the strategy running on defaults
    while its author believes it is running on the value they typed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class UniverseConfig(Section):
    """Which instruments are eligible before any filtering."""

    name: str = "us_equity"
    exchanges: tuple[str, ...] = ("XNYS", "XNAS", "XASE")
    asset_classes: tuple[str, ...] = ("common_stock", "adr", "reit")
    exclude_asset_classes: tuple[str, ...] = ("warrant", "unit", "preferred")
    benchmark_symbol: str = "SPY"


class LiquidityConfig(Section):
    """Tradability floors, applied before anything expensive is computed."""

    min_price: float = Field(default=5.0, gt=0)
    max_price: float = Field(default=10_000.0, gt=0)
    min_avg_dollar_volume: float = Field(default=5_000_000, ge=0)
    dollar_volume_lookback: int = Field(default=50, ge=1)
    min_trading_history_sessions: int = Field(default=250, ge=0)
    #: Cap on our share of a session's volume. The single most important
    #: liquidity parameter, because it bounds how badly a backtest can lie about
    #: fills in thin names.
    max_participation_rate: float = Field(default=0.02, gt=0, le=1.0)

    @model_validator(mode="after")
    def _validate_price_band(self) -> Self:
        if self.max_price <= self.min_price:
            raise ConfigError("max_price must exceed min_price")
        return self


class FundamentalConfig(Section):
    """Quality and growth screens, evaluated on as-filed data only."""

    require_positive_revenue_growth: bool = True
    min_revenue_growth_yoy: float = 0.10
    min_earnings_growth_yoy: float = 0.15
    require_positive_earnings: bool = False
    max_debt_to_equity: float | None = 2.0
    min_return_on_equity: float | None = 0.10
    #: Quarters of history required before fundamental screens apply. Young
    #: listings are not rejected for being young; they are simply not eligible
    #: for screens that need a trend.
    min_quarters_of_history: int = Field(default=4, ge=1)


class TechnicalConfig(Section):
    """Trend and relative-strength requirements."""

    trend_ma_periods: tuple[int, ...] = (50, 150, 200)
    require_price_above_ma: tuple[int, ...] = (50, 200)
    require_ma_stack: bool = True
    min_pct_off_52w_low: float = 0.30
    max_pct_below_52w_high: float = 0.25
    relative_strength_lookback: int = Field(default=126, ge=20)
    min_relative_strength_rank: float = Field(default=0.70, ge=0, le=1)
    atr_period: int = Field(default=14, ge=2)
    volume_baseline_period: int = Field(default=50, ge=5)


class PatternConfig(Section):
    """Base and consolidation geometry."""

    enabled_patterns: tuple[str, ...] = (
        "flat_base",
        "cup_with_handle",
        "volatility_contraction",
        "ascending_triangle",
    )
    min_base_sessions: int = Field(default=25, ge=5)
    max_base_sessions: int = Field(default=150, ge=10)
    max_base_depth_pct: float = Field(default=0.35, gt=0, lt=1)
    min_base_depth_pct: float = Field(default=0.08, gt=0, lt=1)
    max_volatility_contraction_ratio: float = Field(default=0.60, gt=0, lt=1)
    pivot_buffer_pct: float = Field(default=0.001, ge=0)

    @model_validator(mode="after")
    def _validate_ranges(self) -> Self:
        if self.max_base_sessions <= self.min_base_sessions:
            raise ConfigError("max_base_sessions must exceed min_base_sessions")
        if self.max_base_depth_pct <= self.min_base_depth_pct:
            raise ConfigError("max_base_depth_pct must exceed min_base_depth_pct")
        return self


class BreakoutConfig(Section):
    """Trigger and confirmation thresholds.

    The distinction between ``trigger`` and ``confirm`` is the difference
    between buying breakouts and buying failed breakouts, so both sets of
    parameters are explicit rather than one implying the other.
    """

    approach_threshold_pct: float = Field(default=0.03, gt=0)
    trigger_buffer_pct: float = Field(default=0.002, ge=0)
    min_volume_ratio: float = Field(default=1.5, ge=1.0)
    confirmation_sessions: int = Field(default=2, ge=1)
    max_extension_from_pivot_pct: float = Field(default=0.05, gt=0)
    failure_undercut_pct: float = Field(default=0.02, gt=0)
    max_sessions_to_confirm: int = Field(default=5, ge=1)


class ScoringConfig(Section):
    """Factor weights for the opportunity score.

    Weights are normalised on load rather than trusted to sum correctly, so a
    config that lists six factors adding to 0.95 is not silently a different
    strategy from one adding to 1.0.
    """

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "relative_strength": 0.25,
            "pattern_quality": 0.20,
            "breakout_confirmation": 0.20,
            "fundamental_quality": 0.15,
            "sector_strength": 0.10,
            "volume_accumulation": 0.10,
        }
    )
    min_score_to_consider: float = Field(default=0.60, ge=0, le=1)
    max_candidates_per_session: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def _validate_weights(self) -> Self:
        if not self.weights:
            raise ConfigError("scoring weights cannot be empty")
        if any(w < 0 for w in self.weights.values()):
            raise ConfigError("scoring weights must be non-negative")
        if sum(self.weights.values()) <= 0:
            raise ConfigError("scoring weights must sum to a positive number")
        return self

    def normalised_weights(self) -> dict[str, float]:
        total = sum(self.weights.values())
        return {name: weight / total for name, weight in sorted(self.weights.items())}


class SizingConfig(Section):
    """Position sizing, expressed in risk rather than dollars."""

    risk_per_trade_pct: float = Field(default=0.005, gt=0, le=0.05)
    max_position_pct_of_equity: float = Field(default=0.20, gt=0, le=1.0)
    min_position_notional: float = Field(default=500.0, ge=0)
    allow_fractional_shares: bool = False
    #: Pyramiding adds to winners only, and only with the stop already raised;
    #: adding to a loser is averaging down wearing a technical name.
    allow_pyramiding: bool = True
    max_pyramid_entries: int = Field(default=2, ge=0)
    pyramid_min_gain_pct: float = Field(default=0.03, gt=0)


class RiskConfig(Section):
    """Portfolio-level limits. Every one of these can veto a trade."""

    max_portfolio_heat_pct: float = Field(default=0.06, gt=0, le=0.50)
    max_positions: int = Field(default=12, ge=1)
    max_sector_exposure_pct: float = Field(default=0.30, gt=0, le=1.0)
    max_correlation_for_new_position: float = Field(default=0.75, gt=0, le=1.0)
    correlation_lookback_sessions: int = Field(default=126, ge=20)
    max_gross_exposure_pct: float = Field(default=1.00, gt=0, le=2.0)
    max_daily_loss_pct: float = Field(default=0.03, gt=0)
    #: Drawdown at which the system stops opening new positions. Existing
    #: positions continue to be managed -- a drawdown halt that also abandons
    #: risk management makes the drawdown worse.
    drawdown_halt_pct: float = Field(default=0.15, gt=0, le=1.0)
    reduce_risk_below_regime: tuple[str, ...] = ("bear_trending", "high_volatility")
    regime_risk_multiplier: float = Field(default=0.5, gt=0, le=1.0)


class ExitConfig(Section):
    """Stops, trailing and exits."""

    initial_stop_atr_multiple: float = Field(default=2.0, gt=0)
    max_initial_stop_pct: float = Field(default=0.08, gt=0)
    trailing_stop_atr_multiple: float = Field(default=3.0, gt=0)
    activate_trailing_at_r: float = Field(default=1.5, gt=0)
    move_stop_to_breakeven_at_r: float = Field(default=1.0, gt=0)
    time_stop_sessions: int | None = Field(default=40, ge=1)
    take_partial_profit_at_r: float | None = Field(default=2.0, gt=0)
    partial_profit_fraction: float = Field(default=0.33, gt=0, lt=1)
    #: Sessions before a scheduled earnings date at which a position is exited
    #: or an entry is vetoed. Zero disables it, which is a decision to hold
    #: through prints rather than an absence of one.
    earnings_blackout_sessions: int = Field(default=2, ge=0)


class CostConfig(Section):
    """Execution cost assumptions.

    Deliberately conservative defaults. Slippage is the assumption most likely
    to flatter a backtest, and a strategy that only works at optimistic costs
    should fail loudly at realistic ones rather than quietly pass.
    """

    commission_per_share: float = Field(default=0.005, ge=0)
    commission_minimum: float = Field(default=1.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)
    spread_bps: float = Field(default=3.0, ge=0)
    market_impact_coefficient: float = Field(default=0.1, ge=0)


class StrategyConfig(BaseModel):
    """A complete, versioned strategy definition.

    Immutable once loaded. Its identity is :attr:`digest` -- the hash of its
    contents -- so two configurations with the same digest are the same
    strategy, and the name is a label rather than an identifier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    schema_version: int = Field(default=1, ge=1)

    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    liquidity: LiquidityConfig = Field(default_factory=LiquidityConfig)
    fundamental: FundamentalConfig = Field(default_factory=FundamentalConfig)
    technical: TechnicalConfig = Field(default_factory=TechnicalConfig)
    patterns: PatternConfig = Field(default_factory=PatternConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    sizing: SizingConfig = Field(default_factory=SizingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    exits: ExitConfig = Field(default_factory=ExitConfig)
    costs: CostConfig = Field(default_factory=CostConfig)

    @model_validator(mode="after")
    def _validate_cross_section(self) -> Self:
        """Checks that span sections, which no single section can make.

        These are the constraints that make a configuration internally
        coherent. A config can satisfy every field constraint and still be
        nonsense as a whole.
        """
        implied_max_positions = self.risk.max_portfolio_heat_pct / self.sizing.risk_per_trade_pct
        if implied_max_positions < 1:
            raise ConfigError(
                f"risk_per_trade_pct ({self.sizing.risk_per_trade_pct:.4f}) exceeds "
                f"max_portfolio_heat_pct ({self.risk.max_portfolio_heat_pct:.4f}); "
                "no position could ever be opened"
            )
        if self.exits.move_stop_to_breakeven_at_r > self.exits.activate_trailing_at_r:
            raise ConfigError(
                "move_stop_to_breakeven_at_r must not exceed activate_trailing_at_r; "
                "the trailing stop would otherwise be looser than the breakeven stop "
                "it replaces"
            )
        if self.sizing.max_position_pct_of_equity > self.risk.max_gross_exposure_pct:
            raise ConfigError("a single position may not exceed the gross exposure limit")
        window = self.technical.relative_strength_lookback
        if window > self.liquidity.min_trading_history_sessions:
            raise ConfigError(
                f"relative_strength_lookback ({window}) exceeds "
                f"min_trading_history_sessions ({self.liquidity.min_trading_history_sessions}); "
                "every instrument passing the liquidity filter would fail on warm-up"
            )
        return self

    # -- identity ------------------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        """Canonical dictionary form used for hashing.

        Excludes ``description``: an edit to prose is not a change of strategy,
        and treating it as one would break the link between a live session and
        the backtest that validated it.
        """
        payload = self.model_dump(mode="json", exclude={"description"})
        payload["scoring"]["weights"] = self.scoring.normalised_weights()
        return payload

    @property
    def digest(self) -> str:
        return content_hash(self.to_payload())

    def version(self, created_at: dt.datetime | None = None) -> ArtifactVersion:
        return ArtifactVersion.of(
            ArtifactKind.STRATEGY_CONFIG, self.name, self.to_payload(), created_at
        )

    @property
    def label(self) -> str:
        return self.version().label

    def differs_from(self, other: StrategyConfig) -> list[str]:
        """Dotted paths whose values differ. For config-change review."""
        return sorted(_diff_paths(self.to_payload(), other.to_payload()))

    # -- loading -------------------------------------------------------------

    @classmethod
    def from_toml(cls, path: str | Path) -> StrategyConfig:
        resolved = Path(path)
        if not resolved.exists():
            raise ConfigError(f"strategy configuration not found: {resolved}")
        try:
            with resolved.open("rb") as handle:
                raw = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{resolved} is not valid TOML: {exc}") from exc
        return cls.model_validate(_tuple_ise(raw))

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> StrategyConfig:
        return cls.model_validate(_tuple_ise(payload))

    def with_overrides(self, **overrides: Any) -> StrategyConfig:
        """A new configuration with sections replaced.

        Used by parameter sweeps. Returns a new object with a new digest --
        there is no in-place mutation, because a configuration that can change
        after a run started cannot identify that run.
        """
        payload = self.model_dump(mode="json")
        for dotted, value in overrides.items():
            target = payload
            *path, leaf = dotted.split(".")
            for part in path:
                if part not in target:
                    raise ConfigError(f"unknown configuration section {part!r} in {dotted!r}")
                target = target[part]
            if leaf not in target:
                raise ConfigError(f"unknown configuration key {dotted!r}")
            target[leaf] = value
        return StrategyConfig.model_validate(_tuple_ise(payload))


def _tuple_ise(payload: Any) -> Any:
    """Convert lists to tuples so parsed TOML matches the frozen model.

    Skips ``weights``, which is a genuine mapping rather than a sequence.
    """
    if isinstance(payload, dict):
        return {key: _tuple_ise(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return tuple(_tuple_ise(item) for item in payload)
    return payload


def _diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        out: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in left or key not in right:
                out.append(path)
            else:
                out.extend(_diff_paths(left[key], right[key], path))
        return out
    return [] if left == right else [prefix]
