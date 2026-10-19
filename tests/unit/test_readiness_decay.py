"""Has it stopped working, and could we tell if it had?

The failure this module is built around, and what these tests protect: **a
decay monitor reporting "consistent" on twelve trades is worse than no
monitor**, because it converts ignorance into reassurance and a green light
will be believed.

So the tests check two things that are easy to get wrong in opposite
directions: that too little evidence produces no verdict rather than a
comforting one, and that a `CONSISTENT` verdict carries the minimum detectable
shortfall so it cannot be read as proof.
"""

from __future__ import annotations

import pytest

from tradeit.readiness import DecayVerdict, Expectation, assess_decay
from tradeit.readiness.decay import MIN_TRADES_FOR_A_VERDICT

EXPECTED = Expectation(
    mean_trade_return=0.012,
    stdev_trade_return=0.05,
    trade_count=802,
    source="ma_cross_baseline 2010-2019",
)


def _spread(mean: float, n: int, width: float = 0.04) -> list[float]:
    """n returns centred on `mean` with a real spread, so the standard error
    is computable and the test is not relying on a degenerate sample."""
    return [mean + ((i % 3) - 1) * width for i in range(n)]


def test_too_few_trades_gives_no_verdict_not_a_reassuring_one() -> None:
    assessment = assess_decay(EXPECTED, _spread(-0.03, 12))
    assert assessment.verdict is DecayVerdict.INSUFFICIENT_EVIDENCE
    assert not assessment.actionable
    assert assessment.z is None
    assert "cannot distinguish" in assessment.note


def test_a_catastrophic_live_record_still_gives_no_verdict_below_the_minimum() -> None:
    """The uncomfortable half of the same rule.

    Twelve disastrous trades are not evidence of decay either, and a monitor
    that fired on them would also fire on twelve unlucky ones. The honest
    answer is the same: not enough.
    """
    assessment = assess_decay(EXPECTED, _spread(-0.50, 12))
    assert assessment.verdict is DecayVerdict.INSUFFICIENT_EVIDENCE
    assert assessment.realised_mean is not None, "the mean is still reported"


def test_the_minimum_is_the_same_number_used_elsewhere_in_the_codebase() -> None:
    """Thirty, matching PerformanceMetrics.statistically_meaningful, so that
    "enough trades to interpret" means one thing rather than two."""
    assert MIN_TRADES_FOR_A_VERDICT == 30


def test_performance_on_target_is_consistent() -> None:
    assessment = assess_decay(EXPECTED, _spread(0.012, 60))
    assert assessment.verdict is DecayVerdict.CONSISTENT
    assert assessment.actionable


def test_a_consistent_verdict_always_carries_what_it_could_not_have_detected() -> None:
    """Without this number, CONSISTENT reads as proof."""
    assessment = assess_decay(EXPECTED, _spread(0.012, 60))
    assert assessment.minimum_detectable_shortfall is not None
    assert assessment.minimum_detectable_shortfall > 0
    assert "shortfall below" in assessment.describe()


def test_a_consistent_verdict_says_so_when_it_cannot_see_the_whole_edge() -> None:
    """The case that makes a green light meaningless: the monitor cannot detect
    a shortfall smaller than the entire expected edge, so "consistent" is
    compatible with the edge having vanished."""
    assessment = assess_decay(EXPECTED, _spread(0.012, 40, width=0.08))
    assert assessment.verdict is DecayVerdict.CONSISTENT
    assert assessment.minimum_detectable_shortfall is not None
    assert assessment.minimum_detectable_shortfall > abs(EXPECTED.mean_trade_return)
    assert "edge having disappeared" in assessment.note
    assert "not evidence the" in assessment.note


def test_a_vanished_edge_on_enough_trades_is_underperforming() -> None:
    assessment = assess_decay(EXPECTED, _spread(0.0, 60, width=0.01))
    assert assessment.verdict is DecayVerdict.UNDERPERFORMING
    assert assessment.z is not None
    assert assessment.z < -2.0


def test_beating_the_backtest_is_flagged_not_celebrated() -> None:
    """A strategy beating its backtest usually means the backtest was wrong."""
    assessment = assess_decay(EXPECTED, _spread(0.08, 60, width=0.01))
    assert assessment.verdict is DecayVerdict.OUTPERFORMING
    assert "backtest was wrong" in assessment.note


def test_identical_returns_give_no_verdict_rather_than_infinite_confidence() -> None:
    """Zero spread means no standard error, so the z would be infinite.

    Reported as insufficient evidence with the real reason: dozens of identical
    returns is itself implausible and worth investigating before it is
    interpreted.
    """
    assessment = assess_decay(EXPECTED, [0.012] * 60)
    assert assessment.verdict is DecayVerdict.INSUFFICIENT_EVIDENCE
    assert "spread is zero" in assessment.note


def test_the_realised_spread_is_used_not_the_expectations() -> None:
    """Using the backtest's spread would import the assumption that live
    behaves like the backtest, which is the thing under test.

    Two runs with the same realised mean and different realised spreads must
    reach different confidence; if the expectation's stdev were used they would
    not.
    """
    tight = assess_decay(EXPECTED, _spread(0.0, 60, width=0.005))
    loose = assess_decay(EXPECTED, _spread(0.0, 60, width=0.20))
    assert tight.z is not None and loose.z is not None
    assert abs(tight.z) > abs(loose.z)
    assert tight.verdict is DecayVerdict.UNDERPERFORMING
    assert loose.verdict is DecayVerdict.CONSISTENT, (
        "a noisy live record must not be called decayed on the same mean"
    )


def test_an_expectation_needs_a_source() -> None:
    with pytest.raises(ValueError, match="without a source"):
        Expectation(mean_trade_return=0.01, stdev_trade_return=0.05, trade_count=100, source="  ")


def test_an_expectation_resting_on_no_trades_is_refused() -> None:
    with pytest.raises(ValueError, match="not an expectation"):
        Expectation(mean_trade_return=0.01, stdev_trade_return=0.05, trade_count=0, source="x")


def test_a_negative_stdev_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        Expectation(mean_trade_return=0.01, stdev_trade_return=-0.01, trade_count=10, source="x")


def test_an_empty_live_record_reports_no_mean() -> None:
    assessment = assess_decay(EXPECTED, [])
    assert assessment.verdict is DecayVerdict.INSUFFICIENT_EVIDENCE
    assert assessment.realised_mean is None
    assert assessment.live_trade_count == 0


def test_the_confidence_width_sets_both_the_verdict_and_the_detectable_floor() -> None:
    """They are computed from one number so they cannot disagree -- a monitor
    whose alarm and whose stated sensitivity came from different places would
    report a sensitivity it did not use."""
    strict = assess_decay(EXPECTED, _spread(0.0, 60, width=0.02), confidence_z=3.0)
    lax = assess_decay(EXPECTED, _spread(0.0, 60, width=0.02), confidence_z=1.0)
    assert strict.minimum_detectable_shortfall is not None
    assert lax.minimum_detectable_shortfall is not None
    assert strict.minimum_detectable_shortfall > lax.minimum_detectable_shortfall
