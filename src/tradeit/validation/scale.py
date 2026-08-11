"""Which computations survive a uniform rescaling of the price series, and which do not.

**Why this exists.** The universe acquisition produced 78 instruments of
split-adjusted daily bars, of which 34 have a verified split schedule and 44 do
not — an entitlement answer from the split source, not a defect in the prices.
Discarding 44 instruments of real market data would be the wrong response, and so
would using them for everything.

The right response is the one this module makes checkable: **a split adjustment
is a piecewise rescaling of the price series**, so any computation whose output
is unchanged when every price is multiplied by a positive constant gives the
same answer on adjusted prices as it would on raw ones — within each adjustment
regime — while a computation that reads an absolute price level does not.

**Declared, then proved.** A classification asserted in a docstring is a
comment. Every rule here is exercised by
:func:`scale_invariance_report`, which runs the real engine twice — once on a bar
series, once on the same series with every price multiplied by *k* — and compares
the outputs feature by feature. A feature declared invariant that moves is a test
failure, and a feature declared sensitive that does *not* move is reported too,
because an over-cautious classification quietly shrinks the usable sample.

**What this is not.** It is not a claim that split-adjusted prices are as good as
raw ones. Adjustment is *piecewise* constant — the factor changes at each split —
so a window straddling a split is rescaled non-uniformly and even an invariant
feature reads a series no one could have seen. That is a separate limitation,
recorded separately, and this module makes no claim about it. What it establishes
is narrower and still worth having: for the 44 instruments whose raw series
cannot be recovered, the scale-invariant analytics are usable and the
scale-sensitive ones are not.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

import numpy as np

from tradeit.core.models import OhlcvBar

#: Relative tolerance when comparing a feature computed on rescaled prices with
#: the same feature on the original.
#:
#: Not zero. Multiplying a float series by *k* and averaging is not bit-identical
#: to averaging and then multiplying by *k*, and demanding exact equality would
#: report ordinary IEEE-754 rounding as a broken invariance claim. 1e-9 relative
#: is roughly six orders of magnitude tighter than any real difference a
#: misclassified feature would produce.
SCALE_TOLERANCE = 1e-9

#: Absolute floor, for features whose values sit near zero where a relative
#: tolerance means nothing.
SCALE_ATOL = 1e-12

#: Factors the property test sweeps.
#:
#: Powers of two are exact in binary floating point and so isolate a genuine
#: classification error from arithmetic noise; the awkward ones make sure a
#: feature is not passing only because the factor was benign. A reverse-split
#: factor below 1 is included because "invariant" must mean invariant in both
#: directions.
DEFAULT_SCALE_FACTORS: tuple[float, ...] = (2.0, 0.5, 8.0, 0.125, 3.7, 1000.0)


class ScaleSensitivity(StrEnum):
    """Whether a computation's answer survives a uniform price rescaling."""

    #: Multiplying every price by *k > 0* leaves the output unchanged. Safe on
    #: split-adjusted prices without a verified raw reconstruction.
    INVARIANT = "scale_invariant"
    #: The output moves with the price level. Requires prices whose absolute
    #: scale is the one that traded, so it needs a verified raw series — or must
    #: be reported as limited.
    SENSITIVE = "scale_sensitive"
    #: Not established. Treated as sensitive at the point of use, because
    #: assuming invariance is the failure that produces a confident wrong answer.
    UNKNOWN = "unknown"

    @property
    def usable_without_raw_prices(self) -> bool:
        return self is ScaleSensitivity.INVARIANT


@dataclass(frozen=True, slots=True)
class ScaleRule:
    """One classification, its reason, and the names it covers."""

    pattern: str
    sensitivity: ScaleSensitivity
    rationale: str

    def matches(self, name: str) -> bool:
        return bool(re.fullmatch(self.pattern, name))


#: The declared classification, most specific first.
#:
#: Ordering matters: ``atr_percent`` must be read before ``atr_\\d+``, and
#: ``distance_from_sma_20`` before ``sma_20``. Each entry says *why*, because a
#: reviewer's job is to disagree with the reasoning rather than to re-derive it.
FEATURE_SCALE_RULES: tuple[ScaleRule, ...] = (
    # -- normalised measures of the same quantities -------------------------
    ScaleRule(
        r"atr_percent",
        ScaleSensitivity.INVARIANT,
        "ATR divided by price: a ratio of two quantities that scale together",
    ),
    ScaleRule(
        r"distance_from_.*",
        ScaleSensitivity.INVARIANT,
        "fractional distance (v - r) / r; numerator and denominator scale alike",
    ),
    ScaleRule(
        r".*_contraction",
        ScaleSensitivity.INVARIANT,
        "ratio of a short window to a long window of the same series",
    ),
    ScaleRule(
        r"bollinger_bandwidth",
        ScaleSensitivity.INVARIANT,
        "band width divided by the middle band",
    ),
    ScaleRule(
        r"sma_\d+_slope|obv_slope",
        ScaleSensitivity.INVARIANT,
        "slope normalised by its own starting level and by the lookback",
    ),
    # -- oscillators and ratios --------------------------------------------
    ScaleRule(
        r"rsi_\d+",
        ScaleSensitivity.INVARIANT,
        "ratio of average gains to average losses; both scale by k",
    ),
    ScaleRule(
        r"adx_\d+|plus_di|minus_di",
        ScaleSensitivity.INVARIANT,
        "directional movement divided by true range",
    ),
    ScaleRule(
        r"realized_volatility_\d+",
        ScaleSensitivity.INVARIANT,
        "standard deviation of fractional returns",
    ),
    ScaleRule(
        r"volatility_percentile",
        ScaleSensitivity.INVARIANT,
        "percentile rank of an invariant series within its own history",
    ),
    ScaleRule(
        r"roc_\d+|momentum_\d+",
        ScaleSensitivity.INVARIANT,
        "fractional change over a lookback",
    ),
    ScaleRule(
        r"relative_volume|volume_momentum",
        ScaleSensitivity.INVARIANT,
        "computed from volume alone, which a price rescaling does not touch",
    ),
    ScaleRule(
        r"obv",
        ScaleSensitivity.INVARIANT,
        "signed volume; the sign depends on the direction of price change, "
        "which a positive rescaling preserves",
    ),
    # -- price levels and money --------------------------------------------
    ScaleRule(
        r"sma_\d+|ema_\d+|bollinger_(middle|upper|lower)|vwap_\d+",
        ScaleSensitivity.SENSITIVE,
        "a price level, in the currency the prices are quoted in",
    ),
    ScaleRule(
        r"rolling_(high|low)_\d+",
        ScaleSensitivity.SENSITIVE,
        "an extreme price level",
    ),
    ScaleRule(
        r"atr_\d+",
        ScaleSensitivity.SENSITIVE,
        "average true range in currency units. Use atr_percent instead when the "
        "absolute scale is not trustworthy",
    ),
    ScaleRule(
        r"macd_(line|signal|histogram)",
        ScaleSensitivity.SENSITIVE,
        "difference of two exponential averages of price, so it carries the "
        "price's units and scales with it",
    ),
    ScaleRule(
        r"avg_dollar_volume_\d+",
        ScaleSensitivity.SENSITIVE,
        "price multiplied by share volume: money, and money does not survive a "
        "rescaling of one of its factors",
    ),
)


#: Concepts outside the indicator engine, classified for the same reason and
#: kept here so the whole judgement lives in one place. These are not feature
#: names; they are the categories the request named, mapped onto how this
#: platform computes them.
ANALYTIC_SCALE_CLASSIFICATION: Mapping[str, tuple[ScaleSensitivity, str]] = {
    "percentage_returns": (
        ScaleSensitivity.INVARIANT,
        "p1/p0 - 1 cancels the factor exactly",
    ),
    "relative_strength": (
        ScaleSensitivity.INVARIANT,
        "a ratio of two return series; each is already invariant",
    ),
    "trend_geometry": (
        ScaleSensitivity.INVARIANT,
        "expressed as normalised slopes and fractional distances, never as "
        "absolute gradients in currency per bar",
    ),
    "moving_average_relationships": (
        ScaleSensitivity.INVARIANT,
        "whether one average is above another, and by what fraction. The "
        "averages themselves are scale-sensitive; the *relationship* is not",
    ),
    "pattern_geometry_percentages": (
        ScaleSensitivity.INVARIANT,
        "depths, widths and tolerances expressed as fractions of the pattern's own price levels",
    ),
    "contraction_percentages": (
        ScaleSensitivity.INVARIANT,
        "a ratio of a recent window to a longer baseline of the same series",
    ),
    "breakout_percentage": (
        ScaleSensitivity.INVARIANT,
        "penetration measured as a fraction of the boundary level",
    ),
    "normalized_volatility": (
        ScaleSensitivity.INVARIANT,
        "volatility divided by price, or computed from fractional returns",
    ),
    "absolute_price_filter": (
        ScaleSensitivity.SENSITIVE,
        "a rule such as 'price above $5' names a level in currency, and a "
        "rescaled series crosses it at a different time or not at all",
    ),
    "raw_quoted_historical_price": (
        ScaleSensitivity.SENSITIVE,
        "the number that was quoted. Nothing derived can stand in for it",
    ),
    "absolute_dollar_atr": (
        ScaleSensitivity.SENSITIVE,
        "range in currency units",
    ),
    "raw_share_volume": (
        ScaleSensitivity.SENSITIVE,
        "a split changes the share count, so the printed volume before a split "
        "is not comparable with the adjusted figure. Invariant to a *price* "
        "rescaling and NOT invariant to the split that caused it — which is why "
        "this is classified by what it needs, not by what the test measures",
    ),
    "dollar_volume": (
        ScaleSensitivity.SENSITIVE,
        "price times volume",
    ),
    "position_sizing_on_historical_price": (
        ScaleSensitivity.SENSITIVE,
        "share counts derived from an absolute price. A rescaled price produces "
        "a different position and a different risk",
    ),
    "exact_historical_raw_price_replay": (
        ScaleSensitivity.SENSITIVE,
        "the point of the exercise is the actual printed series",
    ),
}


def classify_feature(name: str) -> tuple[ScaleSensitivity, str]:
    """The declared sensitivity of one feature, with its reason.

    Returns :attr:`ScaleSensitivity.UNKNOWN` for a name no rule covers. Unknown
    is deliberately not a synonym for invariant: an unclassified feature used as
    though it were scale-free is exactly how a wrong answer acquires
    confidence, and a coverage test fails when the engine grows a feature this
    table does not mention.
    """
    for rule in FEATURE_SCALE_RULES:
        if rule.matches(name):
            return rule.sensitivity, rule.rationale
    return ScaleSensitivity.UNKNOWN, "no rule covers this feature name"


def invariant_features(names: Sequence[str]) -> tuple[str, ...]:
    return tuple(n for n in names if classify_feature(n)[0] is ScaleSensitivity.INVARIANT)


def sensitive_features(names: Sequence[str]) -> tuple[str, ...]:
    return tuple(n for n in names if classify_feature(n)[0] is not ScaleSensitivity.INVARIANT)


# ---------------------------------------------------------------------------
# Proving it
# ---------------------------------------------------------------------------


def rescale_bars(bars: Sequence[OhlcvBar], factor: float | Decimal) -> list[OhlcvBar]:
    """Multiply every price by ``factor``, leaving volume alone.

    Volume is untouched on purpose. This models a **price** rescaling, which is
    what an adjustment factor does to the price columns; a split additionally
    changes the share count, and conflating the two would let a feature that
    reads volume appear invariant for the wrong reason. Keeping volume fixed
    means ``avg_dollar_volume`` moves — as it must — and ``relative_volume``
    does not.

    OHLC ordering is preserved because a positive factor is monotonic, so the
    rescaled bars still validate.
    """
    multiplier = Decimal(str(factor))
    if multiplier <= 0:
        raise ValueError(f"scale factor {factor} must be positive")
    return [
        bar.model_copy(
            update={
                "open": bar.open * multiplier,
                "high": bar.high * multiplier,
                "low": bar.low * multiplier,
                "close": bar.close * multiplier,
                "vwap": bar.vwap * multiplier if bar.vwap is not None else None,
            }
        )
        for bar in bars
    ]


@dataclass(frozen=True, slots=True)
class FeatureScaleOutcome:
    """What one feature actually did when the prices were rescaled."""

    name: str
    declared: ScaleSensitivity
    observed_invariant: bool
    max_relative_difference: float
    rationale: str = ""

    @property
    def agrees(self) -> bool:
        """Whether the declaration matched the measurement.

        A declared-invariant feature that moved is a defect. A declared-
        sensitive feature that did *not* move is also reported, because an
        over-cautious classification silently shrinks the sample that may be
        validated without raw prices.
        """
        if self.declared is ScaleSensitivity.INVARIANT:
            return self.observed_invariant
        if self.declared is ScaleSensitivity.SENSITIVE:
            return not self.observed_invariant
        return False


@dataclass(frozen=True, slots=True)
class ScaleInvarianceReport:
    """The outcome of one rescaling sweep over one instrument's bars."""

    factor: float
    outcomes: tuple[FeatureScaleOutcome, ...] = ()
    #: Features the engine produced that no rule covers.
    unclassified: tuple[str, ...] = ()

    @property
    def violations(self) -> tuple[FeatureScaleOutcome, ...]:
        """Declared invariant, observed to move. The failure that matters."""
        return tuple(
            o
            for o in self.outcomes
            if o.declared is ScaleSensitivity.INVARIANT and not o.observed_invariant
        )

    @property
    def over_cautious(self) -> tuple[FeatureScaleOutcome, ...]:
        """Declared sensitive, observed invariant."""
        return tuple(
            o
            for o in self.outcomes
            if o.declared is ScaleSensitivity.SENSITIVE and o.observed_invariant
        )

    @property
    def holds(self) -> bool:
        return not self.violations and not self.unclassified

    def to_payload(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "features_checked": len(self.outcomes),
            "declared_invariant": sum(
                1 for o in self.outcomes if o.declared is ScaleSensitivity.INVARIANT
            ),
            "violations": [o.name for o in self.violations],
            "over_cautious": [o.name for o in self.over_cautious],
            "unclassified": list(self.unclassified),
        }


def series_is_invariant(
    base: Any,
    rescaled: Any,
    *,
    rtol: float = SCALE_TOLERANCE,
    atol: float = SCALE_ATOL,
) -> tuple[bool, float]:
    """Whether two feature series agree, treating NaN as a value.

    Warm-up regions are NaN and ``nan != nan``, so a plain comparison would
    report every deterministic feature as having moved.
    """
    left = np.asarray(base, dtype=np.float64)
    right = np.asarray(rescaled, dtype=np.float64)
    if left.shape != right.shape:
        return False, float("inf")
    both_nan = np.isnan(left) & np.isnan(right)
    if not np.array_equal(np.isnan(left), np.isnan(right)):
        return False, float("inf")
    comparable = ~both_nan
    if not comparable.any():
        return True, 0.0
    a = left[comparable]
    b = right[comparable]
    scale = np.maximum(np.abs(a), np.abs(b))
    with np.errstate(divide="ignore", invalid="ignore"):
        relative = np.where(scale > atol, np.abs(a - b) / np.where(scale > 0, scale, 1.0), 0.0)
    worst = float(np.max(relative)) if relative.size else 0.0
    return bool(np.allclose(a, b, rtol=rtol, atol=atol)), worst


def scale_invariance_report(
    values_for: Any,
    bars: Sequence[OhlcvBar],
    factor: float,
) -> ScaleInvarianceReport:
    """Run a feature computation twice and compare, feature by feature.

    ``values_for`` is any callable mapping a bar sequence to ``{name: series}``
    — in practice ``IndicatorEngine.compute(...).values`` — so this works for
    any layer that can express itself that way rather than only for indicators.
    """
    base: Mapping[str, Any] = values_for(bars)
    rescaled: Mapping[str, Any] = values_for(rescale_bars(bars, factor))

    outcomes: list[FeatureScaleOutcome] = []
    unclassified: list[str] = []
    for name in sorted(base):
        declared, rationale = classify_feature(name)
        if declared is ScaleSensitivity.UNKNOWN:
            unclassified.append(name)
        invariant, worst = series_is_invariant(base[name], rescaled.get(name, ()))
        outcomes.append(
            FeatureScaleOutcome(
                name=name,
                declared=declared,
                observed_invariant=invariant,
                max_relative_difference=worst,
                rationale=rationale,
            )
        )
    return ScaleInvarianceReport(
        factor=factor,
        outcomes=tuple(outcomes),
        unclassified=tuple(unclassified),
    )


@dataclass(frozen=True, slots=True)
class EligibilityVerdict:
    """Whether a named analytic may run on a given instrument's data."""

    analytic: str
    sensitivity: ScaleSensitivity
    eligible: bool
    reason: str = ""


def analytic_is_eligible(
    analytic: str,
    *,
    raw_reconstruction_available: bool,
    adjustment_policy: str,
) -> EligibilityVerdict:
    """May this analytic be trusted on this instrument's price series?

    Three cases, and the middle one is the whole reason this exists:

    * Raw prices, or a verified reconstruction of them — everything is eligible.
    * Split-adjusted prices with no verified raw series — the scale-invariant
      analytics are eligible and the scale-sensitive ones are not.
    * An unclassified analytic — never eligible without raw prices, because
      "we have not established this" must not read as "it is fine".
    """
    sensitivity, rationale = _analytic_sensitivity(analytic)
    if raw_reconstruction_available or adjustment_policy == "raw_unadjusted":
        return EligibilityVerdict(
            analytic=analytic,
            sensitivity=sensitivity,
            eligible=True,
            reason="the instrument's raw price series is available or verified",
        )
    if sensitivity is ScaleSensitivity.INVARIANT:
        return EligibilityVerdict(
            analytic=analytic,
            sensitivity=sensitivity,
            eligible=True,
            reason=(
                f"scale-invariant ({rationale}), so a uniform rescaling of the price "
                "series does not change the answer"
            ),
        )
    return EligibilityVerdict(
        analytic=analytic,
        sensitivity=sensitivity,
        eligible=False,
        reason=(
            f"{sensitivity} ({rationale}) and this instrument has no verified raw price "
            "series, so the absolute price level cannot be relied on"
        ),
    )


def _analytic_sensitivity(analytic: str) -> tuple[ScaleSensitivity, str]:
    declared = ANALYTIC_SCALE_CLASSIFICATION.get(analytic)
    if declared is not None:
        return declared
    return classify_feature(analytic)


@dataclass(slots=True)
class ScaleEligibilitySummary:
    """Sample sizes, kept apart. Never one number."""

    price_eligible: int = 0
    raw_verified: int = 0
    analytics: dict[str, EligibilityVerdict] = field(default_factory=dict)

    def render(self) -> list[str]:
        invariant = sorted(a for a, v in self.analytics.items() if v.eligible)
        limited = sorted(a for a, v in self.analytics.items() if not v.eligible)
        return [
            f"  instruments usable for scale-invariant analytics : {self.price_eligible}",
            f"  instruments with a verified raw price series     : {self.raw_verified}",
            f"  analytics eligible without raw prices            : {len(invariant)}",
            f"  analytics requiring a verified raw series        : {len(limited)}",
            *([f"    limited: {', '.join(limited)}"] if limited else []),
        ]


__all__ = [
    "ANALYTIC_SCALE_CLASSIFICATION",
    "DEFAULT_SCALE_FACTORS",
    "FEATURE_SCALE_RULES",
    "SCALE_ATOL",
    "SCALE_TOLERANCE",
    "EligibilityVerdict",
    "FeatureScaleOutcome",
    "ScaleEligibilitySummary",
    "ScaleInvarianceReport",
    "ScaleRule",
    "ScaleSensitivity",
    "analytic_is_eligible",
    "classify_feature",
    "invariant_features",
    "rescale_bars",
    "scale_invariance_report",
    "sensitive_features",
    "series_is_invariant",
]
