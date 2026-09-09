"""Measuring whether a signal predicts anything worth trading.

The roadmap places this in Phase 9 rather than with the dashboard, because it
needs out-of-sample separation, walk-forward windows and realistic costs. Those
exist now. What this module adds is the measurement and the discipline around
it.

The target is declared first, and cannot be changed
----------------------------------------------------

*"Will the stock go up"* is not a target, and on a platform with three separate
horizons it is not even a question. A :class:`StudyTarget` names the outcome and
the horizon — five-session forward return, probability of the stop being hit
before the target, three-month excess return — and a :class:`SignalStudy` is
constructed around one. **There is deliberately no way to retarget a study.**
That is the entire discipline: choosing the target after seeing which one worked
is how a signal is discovered in noise, and it is invisible in the result.

Statistically detectable and economically useful are different results
----------------------------------------------------------------------

A signal can pass the first and fail the second, **and only the second
matters.** :meth:`SignalStudy.information_coefficient` answers whether the
relationship is there at all; :meth:`SignalStudy.net_annual_spread` answers
whether anything is left after spread, slippage, commission and the turnover the
horizon forces. A signal with a beautiful IC and a five-session horizon pays its
round-trip costs fifty times a year, and usually that is the whole edge.

Overlapping observations inflate significance, and this corrects for it
-----------------------------------------------------------------------

Sampling a 20-session forward return every session produces observations that
share nineteen twentieths of their window. They are not independent, and the
usual t-statistic — which assumes they are — is too large by roughly the square
root of the horizon. For a 20-session horizon that is a factor of **4.5**,
which is the difference between noise and a publishable finding.

:meth:`SignalStudy.effective_observations` divides by the horizon, the standard
non-overlapping adjustment, and the t-statistic uses it. The correction is
approximate and conservative in the direction that matters: it makes marginal
signals fail. The alternative — quoting the raw count — has produced more false
discoveries in this field than any other single mistake.

Beating baselines is required, and sophistication is not a defence
------------------------------------------------------------------

A candidate is compared against buy-and-hold, the benchmark, a moving-average
rule, a naive classifier. **A signal that cannot beat them is not promoted for
being clever.** ``verdict`` returns ``BEATEN_BY_BASELINE`` and names which one.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from tradeit.strategy.config import CostConfig

__all__ = [
    "Observation",
    "PromotionRule",
    "SignalStudy",
    "SignalVerdict",
    "StudyTarget",
    "TargetKind",
]

_BPS = 10000.0


class TargetKind(StrEnum):
    """What a study is predicting. Named before the study runs."""

    FORWARD_RETURN = "forward_return"
    EXCESS_RETURN = "excess_return"
    STOP_BEFORE_TARGET = "stop_before_target"
    BREAKOUT_SUCCESS = "breakout_success"


class SignalVerdict(StrEnum):
    """The outcome of assessing one signal. Only one of these is a pass."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_DETECTABLE = "not_detectable"
    DETECTABLE_NOT_PROFITABLE = "detectable_not_profitable"
    BEATEN_BY_BASELINE = "beaten_by_baseline"
    ECONOMICALLY_USEFUL = "economically_useful"


@dataclass(frozen=True, slots=True)
class StudyTarget:
    """The outcome being predicted, and over what horizon."""

    kind: TargetKind
    horizon_sessions: int
    description: str = ""

    def __post_init__(self) -> None:
        if self.horizon_sessions < 1:
            raise ValueError("a target horizon must be at least one session")

    @property
    def round_trips_per_year(self) -> float:
        """How often holding for this horizon forces a round trip.

        The number that turns a per-observation edge into an annual cost bill,
        and the reason short-horizon signals so often fail the economic test
        after passing the statistical one.
        """
        return 252.0 / self.horizon_sessions

    @property
    def label(self) -> str:
        return f"{self.kind}@{self.horizon_sessions}s"


@dataclass(frozen=True, slots=True)
class Observation:
    """One signal reading and the outcome that followed it.

    ``outcome`` is expressed in the target's own units: a return as a fraction,
    a binary outcome as 0.0 or 1.0.
    """

    session_date: dt.date
    instrument_id: int
    signal: float
    outcome: float


@dataclass(frozen=True, slots=True)
class PromotionRule:
    """What a signal must clear to be called useful.

    No defaults. These are the thresholds that decide what gets traded, and a
    default here would be a research standard nobody agreed to.
    """

    min_observations: int
    min_abs_t_statistic: float
    quantile_fraction: float

    def __post_init__(self) -> None:
        if not 0 < self.quantile_fraction <= 0.5:
            raise ValueError(
                "quantile_fraction must be in (0, 0.5]; above a half the top and "
                "bottom buckets overlap and the spread compares a set with itself"
            )


@dataclass(frozen=True, slots=True)
class SignalStudy:
    """One signal, one declared target, and the observations between them."""

    name: str
    target: StudyTarget
    observations: tuple[Observation, ...]

    @property
    def count(self) -> int:
        return len(self.observations)

    @property
    def effective_observations(self) -> float:
        """Sample size after the overlap correction.

        Observations spaced one session apart over an N-session horizon share
        most of their window. Dividing by the horizon is the standard
        non-overlapping adjustment; see the module docstring for what skipping
        it does to the t-statistic.
        """
        return self.count / self.target.horizon_sessions

    def information_coefficient(self) -> float | None:
        """Rank correlation between the signal and the outcome.

        Rank rather than linear, because a signal that is monotonically related
        to returns but not linearly is still a good signal, and one enormous
        outlier should not carry the entire correlation.
        """
        if self.count < 3:
            return None
        signals = [o.signal for o in self.observations]
        outcomes = [o.outcome for o in self.observations]
        if len(set(signals)) < 2 or len(set(outcomes)) < 2:
            # A constant column has no correlation, defined or otherwise.
            return None
        return statistics.correlation(signals, outcomes, method="ranked")

    def t_statistic(self) -> float | None:
        """Significance of the information coefficient, overlap-corrected."""
        ic = self.information_coefficient()
        if ic is None:
            return None
        effective = self.effective_observations
        if effective < 3 or abs(ic) >= 1:
            return None
        return ic * math.sqrt(effective - 2) / math.sqrt(1 - ic * ic)

    def quantile_spread(self, fraction: float) -> float | None:
        """Mean outcome of the top bucket minus the bottom, per observation.

        The tradeable form of an information coefficient: what a strategy would
        have captured going long the strongest readings and short the weakest.
        """
        if not 0 < fraction <= 0.5:
            raise ValueError("fraction must be in (0, 0.5]")
        bucket = int(self.count * fraction)
        if bucket < 1:
            return None
        ordered = sorted(self.observations, key=lambda o: o.signal)
        bottom = statistics.fmean(o.outcome for o in ordered[:bucket])
        top = statistics.fmean(o.outcome for o in ordered[-bucket:])
        return top - bottom

    def annual_cost_drag(self, costs: CostConfig, *, average_price: float) -> float:
        """What the horizon's turnover costs per year, as a fraction of capital.

        Both legs of every round trip pay spread, slippage and commission. A
        five-session signal rebalances about fifty times a year, and fifty round
        trips at even a few basis points is most of what a small edge produces.
        """
        if average_price <= 0:
            raise ValueError("average price must be positive to express commission as a rate")
        per_leg = (costs.spread_bps / 2 + costs.slippage_bps) / _BPS
        commission_rate = costs.commission_per_share / average_price
        round_trip = 2 * (per_leg + commission_rate)
        return round_trip * self.target.round_trips_per_year

    def net_annual_spread(
        self, fraction: float, costs: CostConfig, *, average_price: float
    ) -> float | None:
        """The quantile spread annualised, less what trading it costs.

        This is the number that decides whether a signal is worth anything.
        """
        spread = self.quantile_spread(fraction)
        if spread is None:
            return None
        annualised = spread * self.target.round_trips_per_year
        return annualised - self.annual_cost_drag(costs, average_price=average_price)

    def verdict(
        self,
        rule: PromotionRule,
        costs: CostConfig,
        *,
        average_price: float,
        baselines: Mapping[str, float] | None = None,
    ) -> tuple[SignalVerdict, str]:
        """Assess the signal, in the order the checks actually matter.

        Evidence first, then detectability, then economics, then the baselines.
        A signal is only useful if it survives all four, and the reason string
        names the check that stopped it.
        """
        if self.count < rule.min_observations:
            return (
                SignalVerdict.INSUFFICIENT_EVIDENCE,
                f"{self.count} observations against a {rule.min_observations} minimum",
            )
        t_statistic = self.t_statistic()
        if t_statistic is None:
            return (
                SignalVerdict.INSUFFICIENT_EVIDENCE,
                "no t-statistic could be computed; the signal or the outcome is constant",
            )
        if abs(t_statistic) < rule.min_abs_t_statistic:
            return (
                SignalVerdict.NOT_DETECTABLE,
                f"t={t_statistic:.2f} on {self.effective_observations:.0f} effective "
                f"observations, below the {rule.min_abs_t_statistic:.2f} threshold",
            )
        net = self.net_annual_spread(rule.quantile_fraction, costs, average_price=average_price)
        if net is None:
            return (
                SignalVerdict.INSUFFICIENT_EVIDENCE,
                f"too few observations to fill a {rule.quantile_fraction:.0%} bucket",
            )
        if net <= 0:
            drag = self.annual_cost_drag(costs, average_price=average_price)
            return (
                SignalVerdict.DETECTABLE_NOT_PROFITABLE,
                f"t={t_statistic:.2f} is detectable, but {self.target.round_trips_per_year:.0f} "
                f"round trips a year cost {drag:.1%} and leave {net:.1%}",
            )
        for label, hurdle in sorted((baselines or {}).items()):
            if net <= hurdle:
                return (
                    SignalVerdict.BEATEN_BY_BASELINE,
                    f"{net:.1%} net does not beat {label} at {hurdle:.1%}; "
                    "sophistication is not a reason to promote it",
                )
        return (
            SignalVerdict.ECONOMICALLY_USEFUL,
            f"t={t_statistic:.2f}, {net:.1%} net of costs, beating "
            f"{len(baselines or {})} baseline(s)",
        )


def rank_signals(
    studies: Sequence[SignalStudy],
    rule: PromotionRule,
    costs: CostConfig,
    *,
    average_price: float,
) -> list[tuple[SignalStudy, SignalVerdict, float | None]]:
    """Order signals by what survives costs, not by what correlates best.

    Ranking by information coefficient puts the most statistically impressive
    signal first, which is a different — and, for anything with a short
    horizon, frequently opposite — ordering from the useful one. Signals that
    do not clear their verdict sort last regardless of their spread.
    """
    scored: list[tuple[SignalStudy, SignalVerdict, float | None]] = []
    for study in studies:
        verdict, _ = study.verdict(rule, costs, average_price=average_price, baselines=None)
        net = study.net_annual_spread(rule.quantile_fraction, costs, average_price=average_price)
        scored.append((study, verdict, net))
    return sorted(
        scored,
        key=lambda row: (
            row[1] is not SignalVerdict.ECONOMICALLY_USEFUL,
            -(row[2] if row[2] is not None else float("-inf")),
        ),
    )
