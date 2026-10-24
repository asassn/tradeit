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
    MIN_WINDOWS_FOR_A_VERDICT,
    PROMOTION_CRITERIA,
    Comparison,
    Criterion,
    Decision,
    Threshold,
    ThresholdSource,
    Verdict,
    VerdictOutcome,
    assess_demotion,
    assess_promotion,
    consistency_evidence,
    criteria_for,
    demotion_criteria_for,
    hurdle_evidence,
)
from tradeit.readiness.assess import _evaluate
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


def test_an_unapproved_threshold_blocks_whatever_is_measured() -> None:
    """The roadmap's rule, enforced structurally rather than by discipline.

    **Built on a synthetic criterion rather than on whichever one happens to be
    owed.** This test twice named a live criterion as its example --
    `out_of_sample_clears_hurdle`, then `walk_forward_consistency` -- and twice
    had to move when that criterion was approved. Only
    `out_of_sample_degradation` is still unapproved, so a third move would
    leave the test one approval away from having no example at all and going
    vacuous, which CLAUDE.md records as having happened here twice before.

    So the mechanism is tested directly: a criterion whose threshold nobody
    approved returns UNRESOLVED however good the measurement is, and still
    reports what was measured.
    """
    owed = Criterion(
        name="synthetic_owed",
        question="does a bar nobody set judge anything?",
        metric="synthetic_metric",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=0.0,
            source=ThresholdSource.UNAPPROVED,
            citation="OWED: constructed by a test to exercise the blocking path",
        ),
        applies_from=StrategyState.BACKTESTING,
    )
    verdict = _evaluate(owed, {"synthetic_metric": 99.0})
    assert verdict.outcome is VerdictOutcome.UNRESOLVED
    assert verdict.reason == "threshold not approved"
    assert verdict.measured == 99.0, "the measurement is still reported, just not judged"


def test_whatever_is_still_owed_is_reported_as_owed() -> None:
    """The live set, asserted generically rather than by name.

    Says nothing about *which* thresholds are unapproved -- that changes as
    decisions are taken, and three have been taken this week. It asserts the
    relationship: an unapproved threshold appears in
    `blocked_on_unapproved_thresholds` and holds the decision, and if none are
    unapproved it says so rather than passing silently.
    """
    unapproved = {
        c.name for c in PROMOTION_CRITERIA if c.threshold.source is ThresholdSource.UNAPPROVED
    }
    if not unapproved:
        pytest.skip("every promotion threshold is approved; this test has nothing to exercise")

    report = assess_promotion(
        StrategyState.OUT_OF_SAMPLE_TESTING, StrategyState.PAPER_TRADING, CLEAN
    )
    reported = {v.criterion.name for v in report.blocked_on_unapproved_thresholds}
    applicable = {
        c.name
        for c in criteria_for(StrategyState.PAPER_TRADING)
        if c.threshold.source is ThresholdSource.UNAPPROVED
    }
    assert reported == applicable, (
        "a threshold nobody approved was not reported as owed, so a reader would "
        "take it for a measurement that is merely missing"
    )
    assert report.decision is Decision.HOLD


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


# ---------------------------------------------------------------------------
# The hurdle criterion: a ratio against the ledger, not a constant.
# ---------------------------------------------------------------------------


def _hurdle_verdict(t: float, *, hurdle: float) -> Verdict:
    report = assess_promotion(
        StrategyState.OUT_OF_SAMPLE_TESTING,
        StrategyState.PAPER_TRADING,
        CLEAN | hurdle_evidence(t, hurdle=hurdle),
    )
    return next(v for v in report.verdicts if v.criterion.name == "out_of_sample_clears_hurdle")


def test_clearing_the_ledger_hurdle_passes_and_missing_it_fails() -> None:
    """1.0 means exactly "equal to the hurdle the ledger computed"."""
    assert _hurdle_verdict(2.6563, hurdle=2.6563).outcome is VerdictOutcome.PASS
    assert _hurdle_verdict(2.6562, hurdle=2.6563).outcome is VerdictOutcome.FAIL


def test_the_ratio_is_stricter_than_any_standing_minimum_would_have_been() -> None:
    """**The reason this is a ratio and not a number.**

    The hurdle rises with the trial count, so a fixed bar that looked
    conservative when written becomes weaker than the ledger as trials
    accumulate. Measured against the real `small_sample_hurdle` rather than a
    remembered figure: a t of 2.3 clears a plausible standing minimum of 2.0
    and clears the hurdle at a handful of trials, and **fails it at this
    project's actual trial count** -- so a constant would have promoted a
    result the ledger rejects.
    """
    from tradeit.backtesting.overfitting import small_sample_hurdle

    blocks = 12
    early = small_sample_hurdle(trials=3, blocks=blocks)
    now = small_sample_hurdle(trials=144, blocks=blocks)
    assert now > early, "the hurdle must rise with trials or this criterion is pointless"

    t = 2.3
    assert t > 2.0, "the standing minimum this test contrasts against"
    assert _hurdle_verdict(t, hurdle=early).outcome is VerdictOutcome.PASS
    assert _hurdle_verdict(t, hurdle=now).outcome is VerdictOutcome.FAIL, (
        f"t={t} passed against a hurdle of {now:.4f} at 144 trials; the criterion is "
        "not comparing against the ledger"
    )


def test_a_negative_t_cannot_clear_the_hurdle() -> None:
    """A result in the wrong direction is not rescued by a small hurdle."""
    assert _hurdle_verdict(-5.0, hurdle=2.6563).outcome is VerdictOutcome.FAIL


def test_a_non_positive_hurdle_is_refused_rather_than_divided_by() -> None:
    """A zero bar would pass everything and look identical to one that works."""
    with pytest.raises(ValueError, match="is not a bar"):
        hurdle_evidence(9.0, hurdle=0.0)
    with pytest.raises(ValueError, match="is not a bar"):
        hurdle_evidence(9.0, hurdle=-1.0)


def test_the_hurdle_threshold_is_approved_and_names_its_parent() -> None:
    """DERIVED, not WRITTEN: it is only as good as the ledger hurdle."""
    criterion = next(
        c
        for c in criteria_for(StrategyState.PAPER_TRADING)
        if c.name == "out_of_sample_clears_hurdle"
    )
    assert criterion.threshold.usable
    assert criterion.threshold.value == 1.0
    assert criterion.threshold.source is ThresholdSource.DERIVED
    assert "small_sample_hurdle" in criterion.threshold.citation


# ---------------------------------------------------------------------------
# Walk-forward consistency: tested against chance, not against a fraction.
# ---------------------------------------------------------------------------


def _consistency_verdict(positive: int, *, total: int) -> Verdict:
    report = assess_promotion(
        StrategyState.OUT_OF_SAMPLE_TESTING,
        StrategyState.PAPER_TRADING,
        CLEAN | consistency_evidence(positive, total_windows=total),
    )
    return next(v for v in report.verdicts if v.criterion.name == "walk_forward_consistency")


def test_the_consistency_bar_rises_as_the_windows_thin() -> None:
    """**The property a fixed fraction cannot have, and the reason for this design.**

    The criterion's earlier citation said picking a fraction now -- after this
    project's walk-forward results exist -- was the after-the-fact threshold the
    roadmap forbids. So no fraction is picked: the share is standardised against
    chance and compared to the framework's existing 2.0.

    The bar in consistency-units then falls out of the window count. Fourteen
    windows need 11 positive (79%); seven need all seven (100%). **Less
    evidence demands a cleaner record**, which is the right direction and
    exactly backwards from what a constant like "60% of windows" would do.
    """
    assert _consistency_verdict(11, total=14).outcome is VerdictOutcome.PASS
    assert _consistency_verdict(10, total=14).outcome is VerdictOutcome.FAIL
    assert _consistency_verdict(7, total=7).outcome is VerdictOutcome.PASS
    assert _consistency_verdict(6, total=7).outcome is VerdictOutcome.FAIL, (
        "six of seven windows passed; at that little evidence the bar should be perfect"
    )


def test_a_coin_flip_record_does_not_pass_at_any_size() -> None:
    """Half the windows positive is what no edge looks like.

    Checked across sizes because a z that drifted with n would let a bigger
    sample launder a 50% record into a pass.
    """
    for total in (4, 10, 14, 40, 100):
        assert _consistency_verdict(total // 2, total=total).outcome is VerdictOutcome.FAIL


def test_too_few_windows_is_refused_rather_than_scored() -> None:
    """At n windows a perfect record scores only sqrt(n).

    Below four, even a flawless record cannot reach 2.0, so a z would imply a
    verdict was available when none is. Refused, mirroring
    `decay.MIN_TRADES_FOR_A_VERDICT`.
    """
    assert MIN_WINDOWS_FOR_A_VERDICT == 4
    with pytest.raises(ValueError, match="cannot support a verdict"):
        consistency_evidence(3, total_windows=3)
    # Four is the boundary and a perfect record there scores exactly 2.0.
    assert consistency_evidence(4, total_windows=4)["walk_forward_consistency_z"] == 2.0


def test_a_count_outside_the_window_total_is_refused() -> None:
    """More positives than windows means the caller is passing a share."""
    with pytest.raises(ValueError, match="is not a count"):
        consistency_evidence(15, total_windows=14)
    with pytest.raises(ValueError, match="is not a count"):
        consistency_evidence(-1, total_windows=14)


def test_the_consistency_threshold_names_its_parent_and_its_assumption() -> None:
    """DERIVED from the decay monitor's z, with the coin-flip null declared.

    The assumption is the one thing here that could be wrong, so the citation
    has to carry it: a sufficiently skewed return distribution would not
    produce positive windows half the time under no edge.
    """
    criterion = next(
        c for c in criteria_for(StrategyState.PAPER_TRADING) if c.name == "walk_forward_consistency"
    )
    assert criterion.threshold.usable
    assert criterion.threshold.value == 2.0
    assert criterion.threshold.source is ThresholdSource.DERIVED
    assert "coin flip" in criterion.threshold.citation
    assert "ADR-0029" in criterion.threshold.citation
