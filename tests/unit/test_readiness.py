"""Phase 12: turning numbers into a decision, and refusing to invent one.

The roadmap's two hard rules are what these tests protect:

* **No single metric decides.** Promotion is a conjunction, and every
  criterion's verdict is reported individually.
* **No threshold may be chosen after seeing the result it would judge.**
  Enforced structurally: a threshold whose source is UNAPPROVED blocks instead
  of being applied, so the framework cannot quietly judge against a number
  nobody chose.

The specific failure guarded against throughout is the plausible pass — a
report that says PROMOTE because something was not measured, or because a
default stood in for a decision.
"""

from __future__ import annotations

import pytest

from tradeit.readiness import (
    DEMOTION_CRITERIA,
    PROMOTION_CRITERIA,
    Comparison,
    Criterion,
    Decision,
    Threshold,
    ThresholdSource,
    VerdictOutcome,
    assess_demotion,
    assess_promotion,
    criteria_for,
    demotion_criteria_for,
)
from tradeit.strategy.lifecycle import LADDER, StrategyState

CLEAN: dict[str, object] = {
    "trade_count": 802,
    "trustworthy": True,
    "has_manifest": True,
    "survivorship_corrected": True,
    "pre_registered": True,
    "baselines_beaten": True,
    "survives_double_costs": True,
    "paper_backtest_agreement": 1.0,
    "max_drawdown_pct": 0.03,
    "live_authorised": True,
}


def test_an_unmeasured_criterion_is_unresolved_not_failed() -> None:
    """Different findings. Collapsing them would make "run the test" look like
    "improve the strategy"."""
    report = assess_promotion(
        StrategyState.VALIDATED, StrategyState.BACKTESTING, {"trade_count": 802}
    )
    outcomes = {v.criterion.name: v.outcome for v in report.verdicts}
    assert outcomes["trade_count"] is VerdictOutcome.PASS
    assert outcomes["result_trustworthy"] is VerdictOutcome.UNRESOLVED
    assert report.unresolved[0].reason == "not measured"
    assert report.decision is Decision.HOLD


def test_an_unresolved_criterion_blocks_exactly_as_a_failure_does() -> None:
    """Fail closed. An unmeasured strategy must not promote."""
    assert (
        assess_promotion(
            StrategyState.VALIDATED, StrategyState.BACKTESTING, {"trade_count": 802}
        ).decision
        is Decision.HOLD
    )


def test_an_unapproved_threshold_blocks_and_says_no_measurement_will_help() -> None:
    """The roadmap's rule, enforced structurally rather than by discipline."""
    report = assess_promotion(
        StrategyState.OUT_OF_SAMPLE_TESTING,
        StrategyState.PAPER_TRADING,
        CLEAN | {"out_of_sample_t": 9.0, "walk_forward_consistency": 1.0, "degradation": 0.0},
    )
    owed = {v.criterion.name for v in report.blocked_on_unapproved_thresholds}
    assert "out_of_sample_clears_hurdle" in owed
    assert "walk_forward_consistency" in owed
    assert report.decision is Decision.HOLD, (
        "an excellent measurement passed an unapproved threshold, which is the "
        "after-the-fact judgement the framework exists to prevent"
    )


def test_a_passing_measurement_cannot_rescue_an_unapproved_threshold() -> None:
    """Even a perfect number is not judged against a bar nobody set."""
    report = assess_promotion(
        StrategyState.OUT_OF_SAMPLE_TESTING,
        StrategyState.PAPER_TRADING,
        CLEAN | {"out_of_sample_t": 99.0},
    )
    verdict = next(v for v in report.verdicts if v.criterion.name == "out_of_sample_clears_hurdle")
    assert verdict.outcome is VerdictOutcome.UNRESOLVED
    assert verdict.measured == 99.0, "the measurement is still reported, just not judged"


def test_every_criterion_must_pass_for_promotion() -> None:
    """No single metric decides, and no single metric rescues."""
    report = assess_promotion(
        StrategyState.VALIDATED,
        StrategyState.BACKTESTING,
        {"trade_count": 29, "trustworthy": True, "has_manifest": True},
    )
    assert report.decision is Decision.HOLD
    assert [v.criterion.name for v in report.failures] == ["trade_count"]
    assert len(report.passes) == 2, "the other two still pass and are still reported"


def test_criteria_are_cumulative_up_the_ladder() -> None:
    """A criterion asked at BACKTESTING is still asked at LIVE.

    Otherwise a version could climb past evidence it no longer has.
    """
    early = {c.name for c in criteria_for(StrategyState.BACKTESTING)}
    late = {c.name for c in criteria_for(StrategyState.LIVE)}
    assert early
    assert early < late, "the early bar stopped being checked higher up the ladder"


def test_the_bar_rises_with_the_ladder() -> None:
    counts = [len(criteria_for(state)) for state in LADDER]
    assert counts == sorted(counts), f"the criterion count is not monotonic: {counts}"
    assert counts[-1] > counts[0]


def test_live_asks_for_the_interlock() -> None:
    names = {c.name for c in criteria_for(StrategyState.LIVE)}
    assert "live_authorised" in names
    report = assess_promotion(
        StrategyState.ELIGIBLE_FOR_CAPITAL, StrategyState.LIVE, CLEAN | {"live_authorised": False}
    )
    assert any(
        v.criterion.name == "live_authorised" and v.outcome is VerdictOutcome.FAIL
        for v in report.verdicts
    )


def test_a_target_with_no_criteria_holds_rather_than_promoting() -> None:
    """A gap in the criterion list must not read as an approval."""
    report = assess_promotion(StrategyState.LIVE, StrategyState.PAUSED, CLEAN)
    assert report.verdicts == ()
    assert report.decision is Decision.HOLD


def test_demotion_fires_on_any_single_criterion() -> None:
    """The asymmetry. Requiring unanimity would keep a decayed strategy running."""
    report = assess_demotion(StrategyState.LIVE, {"live_drawdown_pct": 0.22, "reconciles": True})
    assert report.decision is Decision.DEMOTE
    assert [v.criterion.name for v in report.failures] == ["drawdown_breached"]


def test_a_broken_reconciliation_demotes() -> None:
    report = assess_demotion(
        StrategyState.PAPER_TRADING, {"live_drawdown_pct": 0.01, "reconciles": False}
    )
    assert report.decision is Decision.DEMOTE


def test_an_unmeasured_demotion_criterion_does_not_demote() -> None:
    """A broken dashboard must not become a trading decision.

    It is surfaced instead, and a caller that wants to treat unmonitored as
    unsafe reads `unresolved` and decides -- which is policy and belongs to the
    caller.
    """
    report = assess_demotion(StrategyState.LIVE, {})
    assert report.decision is Decision.HOLD
    assert len(report.unresolved) == len(demotion_criteria_for(StrategyState.LIVE))


def test_nothing_demotes_a_healthy_version() -> None:
    report = assess_demotion(
        StrategyState.PAPER_TRADING, {"live_drawdown_pct": 0.02, "reconciles": True}
    )
    assert report.decision is Decision.HOLD


def test_every_threshold_carries_a_citation() -> None:
    """A number without a source is a number nobody chose, dressed as one that was."""
    for criterion in PROMOTION_CRITERIA + DEMOTION_CRITERIA:
        assert criterion.threshold.citation.strip(), criterion.name
        if criterion.threshold.source is ThresholdSource.UNAPPROVED:
            assert "OWED" in criterion.threshold.citation, (
                f"{criterion.name} is unapproved but its citation does not say what is owed"
            )


def test_a_threshold_without_a_citation_is_refused() -> None:
    with pytest.raises(ValueError, match="nobody chose"):
        Threshold(value=1.0, source=ThresholdSource.WRITTEN, citation="  ")


def test_a_boolean_measurement_compares_as_a_number() -> None:
    """`bool` is an `int` subclass and is handled explicitly, so the criterion
    list needs no parallel boolean path."""
    report = assess_promotion(
        StrategyState.VALIDATED,
        StrategyState.BACKTESTING,
        {"trade_count": 50, "trustworthy": False, "has_manifest": True},
    )
    verdict = next(v for v in report.verdicts if v.criterion.name == "result_trustworthy")
    assert verdict.outcome is VerdictOutcome.FAIL
    assert verdict.measured == 0.0


def test_a_non_numeric_measurement_is_unresolved_not_crashing() -> None:
    report = assess_promotion(
        StrategyState.VALIDATED,
        StrategyState.BACKTESTING,
        {"trade_count": "lots", "trustworthy": True, "has_manifest": True},
    )
    verdict = next(v for v in report.verdicts if v.criterion.name == "trade_count")
    assert verdict.outcome is VerdictOutcome.UNRESOLVED
    assert "not a number" in verdict.reason


def test_at_most_criteria_pass_below_their_threshold() -> None:
    """Both comparison directions work, or half the criteria are decoration."""
    at_most = Criterion(
        name="t",
        question="q",
        metric="m",
        comparison=Comparison.AT_MOST,
        threshold=Threshold(value=0.10, source=ThresholdSource.WRITTEN, citation="test"),
        applies_from=StrategyState.BACKTESTING,
    )
    assert at_most.satisfied_by(0.05)
    assert at_most.satisfied_by(0.10)
    assert not at_most.satisfied_by(0.11)


def test_the_report_names_what_is_owed_in_its_description() -> None:
    report = assess_promotion(
        StrategyState.OUT_OF_SAMPLE_TESTING, StrategyState.PAPER_TRADING, CLEAN
    )
    text = report.describe()
    assert "HOLD" in text
    assert "APPROVED THRESHOLD" in text
    assert "OWED" in text
