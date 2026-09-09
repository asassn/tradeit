"""Tests for signal research.

Two of these decide whether the module is worth having. The overlap correction
must actually change the answer -- a t-statistic computed on raw counts is too
large by roughly sqrt(horizon), which is the difference between noise and a
finding. And a signal with a genuine edge but a five-session horizon must fail
the economic test on turnover costs, because that is the case the whole
statistical-versus-economic distinction exists for.
"""

from __future__ import annotations

import datetime as dt
import math
import random

import pytest

from tradeit.signals.study import (
    Observation,
    PromotionRule,
    SignalStudy,
    SignalVerdict,
    StudyTarget,
    TargetKind,
    rank_signals,
)
from tradeit.strategy.config import CostConfig

START = dt.date(2020, 1, 1)
COSTS = CostConfig()
PRICE = 50.0
RULE = PromotionRule(min_observations=100, min_abs_t_statistic=2.0, quantile_fraction=0.2)


def _target(horizon: int = 5, kind: TargetKind = TargetKind.FORWARD_RETURN) -> StudyTarget:
    return StudyTarget(kind=kind, horizon_sessions=horizon)


def _study(
    pairs: list[tuple[float, float]],
    *,
    horizon: int = 5,
    name: str = "s",
) -> SignalStudy:
    return SignalStudy(
        name=name,
        target=_target(horizon),
        observations=tuple(
            Observation(
                session_date=START + dt.timedelta(days=i),
                instrument_id=1,
                signal=signal,
                outcome=outcome,
            )
            for i, (signal, outcome) in enumerate(pairs)
        ),
    )


def _predictive(n: int, *, strength: float, seed: int = 3, scale: float = 0.02) -> SignalStudy:
    """A signal that genuinely predicts, with tunable strength."""
    rng = random.Random(seed)
    pairs = []
    for _ in range(n):
        signal = rng.gauss(0, 1)
        noise = rng.gauss(0, 1)
        pairs.append((signal, scale * (strength * signal + (1 - strength) * noise)))
    return _study(pairs)


class TestTheTarget:
    def test_a_target_must_have_a_horizon(self) -> None:
        with pytest.raises(ValueError, match="at least one session"):
            StudyTarget(kind=TargetKind.FORWARD_RETURN, horizon_sessions=0)

    def test_the_horizon_sets_the_rebalance_frequency(self) -> None:
        assert _target(5).round_trips_per_year == pytest.approx(50.4)
        assert _target(63).round_trips_per_year == pytest.approx(4.0)

    def test_a_study_cannot_be_retargeted(self) -> None:
        """Choosing the target after seeing what worked is invisible in the result."""
        study = _predictive(200, strength=0.3)
        with pytest.raises(AttributeError):
            study.target = _target(20)  # type: ignore[misc]

    def test_the_target_labels_itself_for_the_record(self) -> None:
        assert _target(5).label == "forward_return@5s"


class TestTheOverlapCorrection:
    def test_effective_observations_divide_by_the_horizon(self) -> None:
        study = _predictive(200, strength=0.2)
        assert study.count == 200
        assert study.effective_observations == pytest.approx(40.0)  # 200 / 5

    def test_the_correction_shrinks_the_t_statistic_by_root_horizon(self) -> None:
        """Skipping it inflates t by sqrt(horizon): 4.5x at a 20-session horizon.

        Measured on a large sample, because the exact ratio is
        sqrt((n - 2) / (n/h - 2)) and the -2 only becomes negligible once the
        *effective* count is large. At 400 observations it is 4.70, not 4.47 --
        the approximation is asymptotic and the docstring says "roughly".
        """
        pairs = [(o.signal, o.outcome) for o in _predictive(4000, strength=0.2).observations]
        daily = _study(pairs, horizon=1)
        twenty = _study(pairs, horizon=20)
        assert daily.t_statistic() is not None and twenty.t_statistic() is not None
        ratio = daily.t_statistic() / twenty.t_statistic()  # type: ignore[operator]
        assert ratio == pytest.approx(math.sqrt(20), rel=0.05)

    def test_a_marginal_signal_fails_once_corrected(self) -> None:
        """The correction is meant to change verdicts, not decorate reports."""
        pairs = [(o.signal, o.outcome) for o in _predictive(400, strength=0.16).observations]
        uncorrected = _study(pairs, horizon=1)
        corrected = _study(pairs, horizon=20)
        assert abs(uncorrected.t_statistic() or 0) > 2.0
        assert abs(corrected.t_statistic() or 0) < 2.0


class TestInformationCoefficient:
    def test_a_predictive_signal_has_a_positive_ic(self) -> None:
        ic = _predictive(500, strength=0.5).information_coefficient()
        assert ic is not None and ic > 0.3

    def test_an_unpredictive_signal_has_an_ic_near_zero(self) -> None:
        ic = _predictive(500, strength=0.0).information_coefficient()
        assert ic is not None and abs(ic) < 0.15

    def test_it_is_rank_based_so_one_outlier_cannot_carry_it(self) -> None:
        """A monotone but non-linear relationship is still a good signal."""
        pairs = [(float(i), float(i) ** 3) for i in range(1, 51)]
        assert _study(pairs).information_coefficient() == pytest.approx(1.0)

    def test_a_constant_column_has_no_correlation(self) -> None:
        assert _study([(1.0, 0.5)] * 50).information_coefficient() is None

    def test_too_few_observations_yield_nothing(self) -> None:
        assert _study([(1.0, 0.1), (2.0, 0.2)]).information_coefficient() is None


class TestQuantileSpread:
    def test_the_spread_is_top_bucket_minus_bottom(self) -> None:
        pairs = [(float(i), float(i)) for i in range(100)]
        # Top 10 average 94.5, bottom 10 average 4.5.
        assert _study(pairs).quantile_spread(0.1) == pytest.approx(90.0)

    def test_an_overlapping_fraction_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"\(0, 0.5\]"):
            _study([(1.0, 1.0)] * 10).quantile_spread(0.75)

    def test_too_few_observations_to_fill_a_bucket_yield_nothing(self) -> None:
        assert _study([(1.0, 1.0), (2.0, 2.0)]).quantile_spread(0.2) is None


class TestEconomicsBeatStatistics:
    def test_turnover_costs_scale_with_the_horizon(self) -> None:
        pairs = [(o.signal, o.outcome) for o in _predictive(500, strength=0.4).observations]
        fast = _study(pairs, horizon=1)
        slow = _study(pairs, horizon=63)
        fast_drag = fast.annual_cost_drag(COSTS, average_price=PRICE)
        slow_drag = slow.annual_cost_drag(COSTS, average_price=PRICE)
        assert fast_drag > slow_drag * 50

    def test_a_detectable_signal_can_still_be_unprofitable(self) -> None:
        """The case the whole distinction exists for, and only the second matters."""
        # A real but small edge, sampled daily, rebalanced every session.
        pairs = [
            (o.signal, o.outcome)
            for o in _predictive(4000, strength=0.5, scale=0.0004).observations
        ]
        study = _study(pairs, horizon=1)
        assert abs(study.t_statistic() or 0) > 2.0  # statistically detectable
        verdict, reason = study.verdict(RULE, COSTS, average_price=PRICE)
        assert verdict is SignalVerdict.DETECTABLE_NOT_PROFITABLE
        assert "round trips a year cost" in reason

    def test_a_slow_strong_signal_survives_its_costs(self) -> None:
        pairs = [
            (o.signal, o.outcome) for o in _predictive(4000, strength=0.5, scale=0.05).observations
        ]
        study = _study(pairs, horizon=63)
        verdict, _ = study.verdict(RULE, COSTS, average_price=PRICE)
        assert verdict is SignalVerdict.ECONOMICALLY_USEFUL

    def test_a_zero_price_cannot_express_commission_as_a_rate(self) -> None:
        with pytest.raises(ValueError, match="average price must be positive"):
            _predictive(200, strength=0.3).annual_cost_drag(COSTS, average_price=0.0)


class TestVerdicts:
    def _strong(self) -> SignalStudy:
        pairs = [
            (o.signal, o.outcome) for o in _predictive(4000, strength=0.5, scale=0.05).observations
        ]
        return _study(pairs, horizon=63)

    def test_too_few_observations_is_insufficient_evidence(self) -> None:
        verdict, reason = _predictive(50, strength=0.5).verdict(RULE, COSTS, average_price=PRICE)
        assert verdict is SignalVerdict.INSUFFICIENT_EVIDENCE
        assert "100 minimum" in reason

    def test_noise_is_not_detectable(self) -> None:
        study = _predictive(2000, strength=0.0)
        verdict, reason = study.verdict(RULE, COSTS, average_price=PRICE)
        assert verdict is SignalVerdict.NOT_DETECTABLE
        assert "effective observations" in reason

    def test_a_useful_signal_that_loses_to_a_baseline_is_not_promoted(self) -> None:
        """Sophistication is not a reason to promote it."""
        study = self._strong()
        net = study.net_annual_spread(0.2, COSTS, average_price=PRICE)
        assert net is not None
        verdict, reason = study.verdict(
            RULE, COSTS, average_price=PRICE, baselines={"buy_and_hold": net + 0.05}
        )
        assert verdict is SignalVerdict.BEATEN_BY_BASELINE
        assert "buy_and_hold" in reason
        assert "sophistication is not a reason" in reason

    def test_beating_every_baseline_is_the_only_pass(self) -> None:
        study = self._strong()
        verdict, reason = study.verdict(
            RULE,
            COSTS,
            average_price=PRICE,
            baselines={"buy_and_hold": 0.05, "moving_average": 0.02},
        )
        assert verdict is SignalVerdict.ECONOMICALLY_USEFUL
        assert "2 baseline(s)" in reason

    def test_one_failed_baseline_is_enough_to_block(self) -> None:
        study = self._strong()
        net = study.net_annual_spread(0.2, COSTS, average_price=PRICE)
        assert net is not None
        verdict, _ = study.verdict(
            RULE,
            COSTS,
            average_price=PRICE,
            baselines={"easy": 0.01, "hard": net + 1.0},
        )
        assert verdict is SignalVerdict.BEATEN_BY_BASELINE


class TestPromotionRule:
    def test_an_overlapping_quantile_fraction_is_refused(self) -> None:
        with pytest.raises(ValueError, match="compares a set with itself"):
            PromotionRule(min_observations=100, min_abs_t_statistic=2.0, quantile_fraction=0.6)


class TestRanking:
    def test_signals_are_ranked_by_what_survives_costs(self) -> None:
        """Not by information coefficient, which orders them differently."""
        fast = _study(
            [
                (o.signal, o.outcome)
                for o in _predictive(4000, strength=0.9, scale=0.0004).observations
            ],
            horizon=1,
            name="fast_and_useless",
        )
        slow = _study(
            [
                (o.signal, o.outcome)
                for o in _predictive(4000, strength=0.35, scale=0.05, seed=9).observations
            ],
            horizon=63,
            name="slow_and_useful",
        )
        # The fast signal has the better correlation.
        assert (fast.information_coefficient() or 0) > (slow.information_coefficient() or 0)
        ranked = rank_signals([fast, slow], RULE, COSTS, average_price=PRICE)
        assert ranked[0][0].name == "slow_and_useful"
        assert ranked[0][1] is SignalVerdict.ECONOMICALLY_USEFUL
        assert ranked[-1][0].name == "fast_and_useless"
