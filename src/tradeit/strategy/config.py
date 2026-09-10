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


class BenchmarkConfig(Section):
    """Benchmark instruments and sector proxies.

    Relative strength is measured against *several* benchmarks, not one. A
    growth name that beats SPY but lags QQQ is a different proposition from one
    that beats both, and collapsing that into a single number early discards the
    distinction. ``comparison_symbols`` is the set every security is measured
    against; ``primary_symbol`` is only the default when one must be chosen.

    ``sector_proxies`` maps a GICS-style sector to a tradable ETF. Proxies are a
    fallback for sector strength when historical constituent classifications are
    unavailable -- the ETF's own price history is a legitimate point-in-time
    series, whereas today's constituent list applied to 2015 is not.
    """

    primary_symbol: str = "SPY"
    growth_symbol: str = "QQQ"
    small_cap_symbol: str = "IWM"
    comparison_symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM")
    volatility_symbol: str | None = "VIX"
    sector_proxies: dict[str, str] = Field(
        default_factory=lambda: {
            "Communication Services": "XLC",
            "Consumer Discretionary": "XLY",
            "Consumer Staples": "XLP",
            "Energy": "XLE",
            "Financials": "XLF",
            "Health Care": "XLV",
            "Industrials": "XLI",
            "Information Technology": "XLK",
            "Materials": "XLB",
            "Real Estate": "XLRE",
            "Utilities": "XLU",
        }
    )
    classification_scheme: str = "GICS"

    @model_validator(mode="after")
    def _validate_benchmarks(self) -> Self:
        if not self.comparison_symbols:
            raise ConfigError("at least one comparison benchmark is required")
        if self.primary_symbol not in self.comparison_symbols:
            raise ConfigError(
                f"primary_symbol {self.primary_symbol!r} must appear in comparison_symbols"
            )
        return self


class UniverseConfig(Section):
    """Which instruments are eligible before any filtering.

    ETFs are deliberately absent from ``asset_classes``. They are needed as
    benchmarks, sector proxies, breadth constituents and research subjects, and
    they are loaded and analysed as such -- but an ETF is not an individual
    stock opportunity, and including them in the scanner would put SPY in
    competition with the companies it contains.
    """

    name: str = "us_equity"
    exchanges: tuple[str, ...] = ("XNYS", "XNAS", "XASE")
    asset_classes: tuple[str, ...] = ("common_stock", "adr", "reit")
    exclude_asset_classes: tuple[str, ...] = (
        "warrant",
        "unit",
        "preferred",
        "closed_end_fund",
        "etf",
    )
    include_adrs: bool = True
    include_reits: bool = True
    #: ETFs available for benchmarks, sectors, breadth and research, but never
    #: in the individual-stock opportunity scanner.
    etf_universe_name: str = "us_etf"
    exclude_otc: bool = True
    exclude_leveraged_etfs: bool = True
    exclude_inverse_etfs: bool = True
    exclude_pre_merger_spacs: bool = True
    country: str = "US"

    @model_validator(mode="after")
    def _validate_universe(self) -> Self:
        overlap = set(self.asset_classes) & set(self.exclude_asset_classes)
        if overlap:
            raise ConfigError(f"asset classes both included and excluded: {sorted(overlap)}")
        if self.include_adrs and "adr" not in self.asset_classes:
            raise ConfigError("include_adrs is set but 'adr' is not in asset_classes")
        if self.include_reits and "reit" not in self.asset_classes:
            raise ConfigError("include_reits is set but 'reit' is not in asset_classes")
        return self


class LiquidityConfig(Section):
    """Tradability floors, applied before anything expensive is computed."""

    min_price: float = Field(default=5.0, gt=0)
    max_price: float = Field(default=10_000.0, gt=0)
    min_market_cap: float = Field(default=500_000_000, ge=0)
    min_avg_dollar_volume: float = Field(default=10_000_000, ge=0)
    dollar_volume_lookback: int = Field(default=20, ge=1)
    #: One full trading year. 252 rather than 250 because the longest annual
    #: features (52-week range, 252-session percentile) need a full year of
    #: sessions to mean what their names say; at 250 they are two sessions short
    #: and quietly report a 250-session high as a 52-week high.
    #:
    #: This is a *default*, not a law. A strategy whose longest feature is a
    #: 20-day Bollinger band has no business waiting a year, and may lower this
    #: in its own TOML. :class:`~tradeit.analytics.eligibility.EligibilityPolicy`
    #: derives the real requirement per strategy from its declared feature set.
    min_trading_history_sessions: int = Field(default=252, ge=0)
    #: Cap on our share of a session's volume. The single most important
    #: liquidity parameter, because it bounds how badly a backtest can lie about
    #: fills in thin names.
    max_participation_rate: float = Field(default=0.02, gt=0, le=1.0)

    @model_validator(mode="after")
    def _validate_price_band(self) -> Self:
        if self.max_price <= self.min_price:
            raise ConfigError("max_price must exceed min_price")
        return self


class ScannerProfile(Section):
    """A named liquidity/size preset.

    Profiles exist so that "institutional growth" and "broad opportunity" are
    two configurations of one strategy rather than two forks of it. Each profile
    overrides only the floors; everything else comes from the base config.
    """

    min_market_cap: float = Field(ge=0)
    min_avg_dollar_volume: float = Field(ge=0)
    min_price: float | None = Field(default=None, gt=0)
    description: str = ""


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


class IndicatorConfig(Section):
    """Periods for the technical indicator engine.

    Every period is here rather than defaulted in an indicator's signature. An
    ``SMA(period=50)`` default inside a function is a strategy constant hiding
    in a type annotation (ADR-0008).
    """

    sma_periods: tuple[int, ...] = (10, 20, 50, 150, 200)
    ema_periods: tuple[int, ...] = (8, 21, 50)
    rsi_period: int = Field(default=14, ge=2)
    macd_fast: int = Field(default=12, ge=2)
    macd_slow: int = Field(default=26, ge=3)
    macd_signal: int = Field(default=9, ge=2)
    adx_period: int = Field(default=14, ge=2)
    atr_period: int = Field(default=14, ge=2)
    bollinger_period: int = Field(default=20, ge=2)
    bollinger_stdev: float = Field(default=2.0, gt=0)
    vwap_period: int = Field(default=20, ge=2)
    relative_volume_period: int = Field(default=20, ge=2)
    volatility_periods: tuple[int, ...] = (20, 60)
    momentum_periods: tuple[int, ...] = (20, 60, 120, 250)
    roc_periods: tuple[int, ...] = (5, 20, 60)
    ma_slope_period: int = Field(default=20, ge=2)
    ma_slope_lookback: int = Field(default=10, ge=1)
    rolling_extreme_periods: tuple[int, ...] = (20, 52, 252)
    dollar_volume_period: int = Field(default=20, ge=2)
    #: Contraction compares a recent window against a longer baseline. A ratio
    #: below 1 means the recent window is quieter -- the signature of a base.
    contraction_short_window: int = Field(default=10, ge=2)
    contraction_long_window: int = Field(default=50, ge=5)
    #: Trading days per year, for annualising volatility.
    annualisation_factor: int = Field(default=252, ge=1)

    @model_validator(mode="after")
    def _validate_periods(self) -> Self:
        if self.macd_slow <= self.macd_fast:
            raise ConfigError("macd_slow must exceed macd_fast")
        if self.contraction_long_window <= self.contraction_short_window:
            raise ConfigError("contraction_long_window must exceed contraction_short_window")
        return self


class TimeframeConfig(Section):
    """Which timeframes are constructed, and from what.

    Higher timeframes are aggregated from a base timeframe rather than fetched
    separately, so a weekly bar is by construction consistent with the daily
    bars it contains -- and, more importantly, so its completeness is something
    the calendar can determine rather than something a vendor asserts.
    """

    base_timeframe: str = "1d"
    enabled: tuple[str, ...] = ("1d", "1w")
    intraday_enabled: tuple[str, ...] = ("15m", "1h", "4h")
    intraday_base: str = "1m"
    #: An incomplete higher-timeframe bar is never emitted as a feature. This is
    #: the single most important multi-timeframe rule: at Wednesday noon, the
    #: current week's bar does not exist yet.
    emit_incomplete_bars: bool = False
    week_anchor: str = "friday"


class RelativeStrengthConfig(Section):
    """Benchmark-relative and cross-sectional strength.

    Note: this is *relative strength* in the market-structure sense -- a
    security's performance against a benchmark and against its peers. It is
    unrelated to RSI, which is a momentum oscillator on a single series and is
    configured under indicators.
    """

    lookbacks: tuple[int, ...] = (20, 60, 120, 250)
    #: Weights for combining lookbacks into the 0-100 score. Longer horizons
    #: dominate because a name that has led for a year is a stronger statement
    #: than one that led for a month.
    lookback_weights: dict[str, float] = Field(
        default_factory=lambda: {"20": 0.15, "60": 0.25, "120": 0.30, "250": 0.30}
    )
    benchmarks: tuple[str, ...] = ("SPY", "QQQ", "IWM")
    score_benchmark: str = "SPY"
    rank_against_sector: bool = True
    rank_against_industry: bool = True
    #: Minimum eligible peers before a percentile rank is emitted at all. A
    #: percentile computed over four names is a number, not a rank.
    min_universe_for_rank: int = Field(default=20, ge=2)
    min_sector_peers_for_rank: int = Field(default=5, ge=2)

    @model_validator(mode="after")
    def _validate_weights(self) -> Self:
        missing = {str(lb) for lb in self.lookbacks} - set(self.lookback_weights)
        if missing:
            raise ConfigError(f"lookback_weights missing entries for {sorted(missing)}")
        if self.score_benchmark not in self.benchmarks:
            raise ConfigError("score_benchmark must be one of benchmarks")
        if sum(self.lookback_weights.values()) <= 0:
            raise ConfigError("lookback_weights must sum to a positive number")
        return self

    def normalised_lookback_weights(self) -> dict[str, float]:
        active = {str(lb): self.lookback_weights[str(lb)] for lb in self.lookbacks}
        total = sum(active.values())
        return {k: v / total for k, v in sorted(active.items())}


class SectorStrengthConfig(Section):
    """Sector aggregation. Weights are configurable and later backtestable."""

    scheme: str = "GICS"
    breadth_ma_periods: tuple[int, ...] = (20, 50, 200)
    momentum_lookbacks: tuple[int, ...] = (20, 60)
    #: Minimum classified members before a sector is scored. Below this the
    #: aggregate is noise, and emitting it anyway invites a rotation signal
    #: driven by two stocks.
    min_members: int = Field(default=5, ge=1)
    use_etf_proxy_when_unavailable: bool = True
    factor_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "relative_return": 0.25,
            "absolute_return": 0.15,
            "relative_momentum": 0.20,
            "breadth_above_50dma": 0.20,
            "breadth_above_200dma": 0.10,
            "participation": 0.10,
        }
    )

    @model_validator(mode="after")
    def _validate_weights(self) -> Self:
        if not self.factor_weights or sum(self.factor_weights.values()) <= 0:
            raise ConfigError("sector factor_weights must be non-empty and sum positive")
        if any(w < 0 for w in self.factor_weights.values()):
            raise ConfigError("sector factor_weights must be non-negative")
        return self

    def normalised_factor_weights(self) -> dict[str, float]:
        total = sum(self.factor_weights.values())
        return {k: v / total for k, v in sorted(self.factor_weights.items())}


class BreadthConfig(Section):
    """Market breadth. Every measure declares the universe it was computed on."""

    ma_periods: tuple[int, ...] = (20, 50, 200)
    new_high_low_lookback: int = Field(default=252, ge=20)
    #: Below this, breadth percentages are too noisy to act on and are emitted
    #: with a low-confidence flag rather than silently trusted.
    min_universe_size: int = Field(default=50, ge=2)
    thrust_lookback: int = Field(default=10, ge=2)
    advance_threshold_pct: float = Field(default=0.0, ge=-1.0, le=1.0)


class RegimeConfig(Section):
    """Market-regime classifier thresholds.

    Deliberately rule-based and transparent. Thresholds are a starting point and
    were NOT fitted to historical returns -- doing that in Phase 3 would produce
    a regime model that looks excellent on the data it was tuned on and says
    nothing about the future.
    """

    trend_ma_fast: int = Field(default=50, ge=2)
    trend_ma_slow: int = Field(default=200, ge=5)
    slope_lookback: int = Field(default=20, ge=2)
    benchmarks: tuple[str, ...] = ("SPY", "QQQ", "IWM")
    #: Signal weights. Each signal contributes a score in [-1, 1].
    signal_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "primary_trend": 0.30,
            "benchmark_agreement": 0.15,
            "ma_slope": 0.15,
            "breadth_above_200dma": 0.15,
            "new_high_low": 0.10,
            "sector_participation": 0.10,
            "volatility": 0.05,
        }
    )
    #: Composite score boundaries, descending. A score at or above a boundary
    #: takes that regime.
    thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "STRONG_BULL": 0.60,
            "BULL": 0.25,
            "NEUTRAL": -0.10,
            "WEAK": -0.35,
            "BEAR": -0.65,
        }
    )
    severe_risk_off_volatility_states: tuple[str, ...] = ("EXTREME",)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> Self:
        if not self.signal_weights or sum(self.signal_weights.values()) <= 0:
            raise ConfigError("regime signal_weights must be non-empty and sum positive")
        required = {"STRONG_BULL", "BULL", "NEUTRAL", "WEAK", "BEAR"}
        if set(self.thresholds) != required:
            raise ConfigError(f"regime thresholds must define exactly {sorted(required)}")
        ordered = [
            self.thresholds[name] for name in ("STRONG_BULL", "BULL", "NEUTRAL", "WEAK", "BEAR")
        ]
        if ordered != sorted(ordered, reverse=True):
            raise ConfigError(
                "regime thresholds must decrease from STRONG_BULL to BEAR; "
                "overlapping bands make the classification ambiguous"
            )
        return self

    def normalised_signal_weights(self) -> dict[str, float]:
        total = sum(self.signal_weights.values())
        return {k: v / total for k, v in sorted(self.signal_weights.items())}


class VolatilityRegimeConfig(Section):
    """Volatility-regime thresholds, expressed as percentiles of history.

    Percentile-based rather than absolute: 20% annualised volatility means
    something different in 2017 than in 2020, and a fixed threshold silently
    reclassifies the whole market when the volatility level shifts.
    """

    realized_vol_period: int = Field(default=20, ge=5)
    percentile_lookback: int = Field(default=252, ge=60)
    atr_period: int = Field(default=14, ge=2)
    gap_lookback: int = Field(default=20, ge=5)
    gap_threshold_pct: float = Field(default=0.02, gt=0)
    #: Percentile boundaries, ascending.
    thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "LOW": 0.20,
            "NORMAL": 0.60,
            "ELEVATED": 0.80,
            "HIGH": 0.95,
        }
    )

    @model_validator(mode="after")
    def _validate_thresholds(self) -> Self:
        required = {"LOW", "NORMAL", "ELEVATED", "HIGH"}
        if set(self.thresholds) != required:
            raise ConfigError(f"volatility thresholds must define exactly {sorted(required)}")
        ordered = [self.thresholds[n] for n in ("LOW", "NORMAL", "ELEVATED", "HIGH")]
        if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
            raise ConfigError("volatility thresholds must strictly increase")
        if not all(0 < v < 1 for v in ordered):
            raise ConfigError("volatility thresholds are percentiles in (0, 1)")
        return self


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
    config that lists four factors adding to 0.70 is not silently a different
    strategy from one adding to 1.0. **That is also why dropping a factor needs
    no arithmetic here**: removing its entry renormalises the rest in the same
    proportions, and leaving the survivors at their original numbers keeps the
    declared intent legible.

    ``breakout_confirmation`` was weighted 0.20 and was **removed on 2026-09-10
    because it was the wrong instrument**, not because it was measured and
    failed. ``ConfirmationInputs`` requires post-breakout evidence, so a
    security with no breakout has *no* confirmation score rather than a low
    one -- and a slate holding any such security could never satisfy the
    uniform-coverage rule in :mod:`tradeit.strategy.factors` while it carried
    weight. It is now a gate:
    :func:`tradeit.opportunity.gates.breakout_confirmation_gate`, reading the
    verdict ``ProfileConfig`` and the breakout engine already produce.

    ``sector_strength`` was weighted 0.10 and was **removed on 2026-09-10, on
    measured evidence** -- the only factor here retired for a reason rather than
    a revision. Tested on two independent decades with the direction declared in
    advance, it came out significant in *both* directions: strong sectors
    outperformed in 2000-2009 at t = +2.93 and underperformed in 2015-2024 at
    t = -2.48, with both horizons agreeing inside each decade and disagreeing
    across them. A factor that measures nothing is useless; one that is
    significant in both directions depending on the decade requires knowing its
    sign in advance, which is the thing the weight was supposed to supply.
    ``docs/SIGNAL_SCOREBOARD.md`` holds the runs.

    **This does not retire sector strength as a measurement.**
    :class:`SectorStrengthConfig`, the ``sector_strength`` table and
    :class:`~tradeit.analytics.sectors.SectorStrengthEngine` are untouched, and
    breakout eligibility still uses it as a gate. What changed is that it no
    longer carries weight in the opportunity score.

    **The four survivors are weighted equally, and that is derived rather than
    lazy.** On 2026-09-10 the weights were re-examined against the evidence
    instead of being left at whatever the two retirements happened to leave
    behind -- which had put ``relative_strength`` at 36% by inheritance.

    What the evidence supports is *no differentiation*:

    * **No surviving factor produced an established tradeable spread** at any
      specification tried. Every quantile spread was ``OUTLIER_DEPENDENT``,
      ``NOT_DETECTABLE`` or ``SPREAD_NOT_ESTABLISHED``.
    * The largest information-coefficient t-statistics among them
      (``momentum_252`` at +3.88, ``momentum_126`` at +3.00) sit against a
      multiple-testing hurdle of 1.90 for the twenty trials actually run, and
      the largest of those **flipped sign across specifications**.
    * So the *ordering* among the four is not distinguishable from noise, and
      **ordering by noise manufactures confidence**. Equal weight is the unique
      weighting consistent with "no factor has been shown superior to another".

    Three alternatives were considered and rejected, recorded here because each
    looks reasonable:

    * **Weight by t-statistic.** The ordering it would produce is the ordering
      of numbers that do not clear their own hurdle.
    * **Weight by sign stability.** Only ``relative_strength`` has a proxy whose
      sign held across all four specifications, so this concentrates the score
      in one factor on evidence far too weak to carry it -- reproducing exactly
      the 36% artefact this change removes.
    * **Zero the factors whose proxies failed.** Three of the four were measured
      only by *proxy* -- price kernels standing in for engines that have never
      been run. ``pattern_quality``'s real detector is not distance-from-SMA, and
      punishing a factor for its substitute's failure is not evidence about the
      factor.

    One asymmetry is worth naming because it argues against ever weighting on
    evidence quality: ``fundamental_quality`` is the only survivor measured
    **directly**, and it returned a null. Down-weighting it for that, while
    leaving unmeasured factors higher, would mean **measuring a factor is
    punished relative to leaving it alone** -- a poor property for a research
    loop. The answer to its null is to measure the other three directly, not to
    reshuffle weights.

    Equal weight is therefore a *statement of ignorance*, held deliberately and
    labelled as such. It is not evidence that these four are equally good.
    """

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "relative_strength": 0.25,
            "pattern_quality": 0.25,
            "fundamental_quality": 0.25,
            "volume_accumulation": 0.25,
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
    benchmarks: BenchmarkConfig = Field(default_factory=BenchmarkConfig)
    liquidity: LiquidityConfig = Field(default_factory=LiquidityConfig)
    scanner_profiles: dict[str, ScannerProfile] = Field(
        default_factory=lambda: {
            "institutional_growth": ScannerProfile(
                min_market_cap=2_000_000_000,
                min_avg_dollar_volume=25_000_000,
                description="Large, heavily traded names only.",
            ),
            "broad_opportunity": ScannerProfile(
                min_market_cap=500_000_000,
                min_avg_dollar_volume=10_000_000,
                description="The default breadth of coverage.",
            ),
        }
    )
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    timeframes: TimeframeConfig = Field(default_factory=TimeframeConfig)
    relative_strength: RelativeStrengthConfig = Field(default_factory=RelativeStrengthConfig)
    sector_strength: SectorStrengthConfig = Field(default_factory=SectorStrengthConfig)
    breadth: BreadthConfig = Field(default_factory=BreadthConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    volatility_regime: VolatilityRegimeConfig = Field(default_factory=VolatilityRegimeConfig)
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
        longest_rs = max(self.relative_strength.lookbacks)
        if longest_rs > self.liquidity.min_trading_history_sessions:
            raise ConfigError(
                f"the longest relative-strength lookback ({longest_rs}) exceeds "
                f"min_trading_history_sessions ({self.liquidity.min_trading_history_sessions}); "
                "no eligible instrument could ever produce that feature"
            )
        if self.relative_strength.score_benchmark != self.benchmarks.primary_symbol and (
            self.relative_strength.score_benchmark not in self.benchmarks.comparison_symbols
        ):
            raise ConfigError("relative_strength.score_benchmark must be a configured benchmark")
        for symbol in self.relative_strength.benchmarks:
            if symbol not in self.benchmarks.comparison_symbols:
                raise ConfigError(
                    f"relative_strength benchmark {symbol!r} is not in "
                    "benchmarks.comparison_symbols"
                )
        return self

    def coherence_warnings(self) -> list[str]:
        """Configuration smells that are worth surfacing but are not errors.

        The distinction matters. A relative-strength lookback longer than the
        required trading history is fatal -- combined with the liquidity floor
        it guarantees an empty screen. But an *indicator* lookback slightly
        longer than the minimum history is ordinary warm-up: an instrument that
        just became eligible with 252 sessions genuinely has no 272-session ADX
        yet, and will have one twenty sessions later. Raising on that would be
        confusing a transient state for a broken configuration.

        That transient state is exactly the ``DATA_ELIGIBLE`` /
        ``FEATURE_READY`` split in :mod:`tradeit.analytics.eligibility`: this
        warning describes the gap, the eligibility policy enforces it.

        Surfaced by ``tradeit config`` and by the API's validation endpoint.
        """
        out: list[str] = []
        history = self.liquidity.min_trading_history_sessions
        longest = max(
            (
                *self.indicators.sma_periods,
                *self.indicators.rolling_extreme_periods,
                *self.indicators.momentum_periods,
                self.indicators.contraction_long_window,
            )
        )
        if longest > history:
            out.append(
                f"the longest indicator lookback ({longest}) exceeds "
                f"min_trading_history_sessions ({history}); newly eligible instruments "
                f"will lack that feature for their first {longest - history} sessions"
            )
        if self.indicators.bollinger_period > self.liquidity.dollar_volume_lookback * 4:
            out.append(
                "bollinger_period is much longer than dollar_volume_lookback; the "
                "liquidity screen and the volatility bands are measuring very "
                "different horizons"
            )
        proxy_sectors = set(self.benchmarks.sector_proxies)
        if len(proxy_sectors) < 11:
            out.append(
                f"only {len(proxy_sectors)} sector proxies configured; sectors without "
                "a proxy cannot fall back to an ETF when constituent classification "
                "is unavailable"
            )
        return out

    # -- identity ------------------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        """Canonical dictionary form used for hashing.

        Excludes ``description``: an edit to prose is not a change of strategy,
        and treating it as one would break the link between a live session and
        the backtest that validated it.
        """
        payload = self.model_dump(mode="json", exclude={"description"})
        # Weights are hashed in normalised form, so proportionally identical
        # weight sets are correctly recognised as the same strategy.
        payload["scoring"]["weights"] = self.scoring.normalised_weights()
        payload["relative_strength"]["lookback_weights"] = (
            self.relative_strength.normalised_lookback_weights()
        )
        payload["sector_strength"]["factor_weights"] = (
            self.sector_strength.normalised_factor_weights()
        )
        payload["regime"]["signal_weights"] = self.regime.normalised_signal_weights()
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
