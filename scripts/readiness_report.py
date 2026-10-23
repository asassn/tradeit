#!/usr/bin/env python
"""The readiness framework applied to this project's own evidence.

Phase 12's framework is only worth having if it gives an honest answer about
the thing it is pointed at, so it is pointed at the moving-average baseline --
the only strategy this project has run end to end -- using measurements that
already exist rather than any computed for this report.

**The expected answer is HOLD**, and that is the point. There is no validated
out-of-sample edge after 144 ledger trials, so a framework reporting anything
else would be broken. What the report is for is *which* bars it clears, which
it does not, and which it cannot judge at all because the threshold is still
owed.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from tradeit.readiness import Decision, assess_demotion, assess_promotion
from tradeit.strategy.lifecycle import LADDER, StrategyState

# Every figure below is quoted from a recorded measurement, with its source.
# Nothing here was computed for this report -- a readiness assessment that
# generated its own inputs could also quietly choose them.
EVIDENCE: dict[str, object] = {
    # PHASE_11_AUDIT_2026-10-18.md, the equivalence run.
    "trade_count": 802,
    "trustworthy": True,
    "has_manifest": True,
    "paper_backtest_agreement": 1.0,
    # CLAUDE.md: the gate reads MATERIALLY_SURVIVORSHIP_CORRECTED, with four
    # named limitations that travel with every result.
    "survivorship_corrected": True,
    # SIGNAL_SCOREBOARD §52/§53: both held-out tests WERE pre-registered.
    "pre_registered": True,
    # SIGNAL_SCOREBOARD §52 and §53: both FAILED their held-out tests.
    "baselines_beaten": False,
    # PHASE_11_AUDIT third addendum: 143,331.79 from 100,000 with no drawdown
    # anywhere near the halt. Max drawdown from the backtester's own metrics.
    "max_drawdown_pct": 0.03,
    # Not measured. The cost-sensitivity run has never been done.
    #
    # **This comment used to say more and is now less of an excuse.** It read
    # that market_impact was inert in every backtest this project had produced
    # (PHASE_11_AUDIT second addendum), so a doubling test would double only
    # spread and slippage. ADR-0028 switched impact on as of 2026-10-23, so a
    # doubling test now doubles the size penalty too and is worth more than it
    # would have been -- which makes this absence a thing to go and measure
    # rather than a thing to explain.
    # "survives_double_costs": deliberately absent
    # ADR-0004: no authorisation file exists on this machine.
    "live_authorised": False,
}

DEMOTION_EVIDENCE: dict[str, object] = {
    "live_drawdown_pct": 0.03,
    # 2,516 of 2,516 sessions reconciled clean.
    "reconciles": True,
}


def main() -> int:
    print("the ma_cross_baseline strategy, against the Phase 12 criteria\n")
    for index, target in enumerate(LADDER):
        if index == 0:
            continue
        report = assess_promotion(LADDER[index - 1], target, EVIDENCE)
        marker = "PROMOTE" if report.decision is Decision.PROMOTE else "hold"
        print(
            f"{target.value:<24} {marker:<8} "
            f"{len(report.passes)} pass / {len(report.failures)} fail / "
            f"{len(report.unresolved)} unresolved"
        )

    print("\n--- the first rung with criteria that it does not clear ---\n")
    # Rungs with NO criteria are skipped rather than treated as the stop.
    # VALIDATED is one: `strategy/validate.py` already performs DRAFT ->
    # VALIDATED against the pattern vocabulary, the detector registry and the
    # mandate, and a second implementation of that rule here is how two rules
    # diverge. The framework correctly declines to say PROMOTE for a rung it
    # asks nothing of -- but that is a deferral, not a refusal, and a report
    # that showed it as the stopping point would mislead.
    stopped_at = None
    for index, target in enumerate(LADDER):
        if index == 0:
            continue
        report = assess_promotion(LADDER[index - 1], target, EVIDENCE)
        if not report.verdicts:
            print(f"  {target.value}: no criteria here; deferred to strategy/validate.py")
            continue
        if report.decision is not Decision.PROMOTE:
            stopped_at = report
            break
    if stopped_at is None:
        raise SystemExit(
            "every rung reported PROMOTE, including LIVE. With no validated "
            "out-of-sample edge after 144 trials that cannot be right, and reporting "
            "it would be worse than reporting nothing."
        )
    print(stopped_at.describe())

    print("\n--- demotion, were it running ---\n")
    print(assess_demotion(StrategyState.PAPER_TRADING, DEMOTION_EVIDENCE).describe())

    print(
        "\nHOLD is the correct answer and the framework reaching it is the check.\n"
        "The informative part is WHERE it stops and WHY: a failed criterion needs\n"
        "better evidence, an unresolved one needs a measurement, and an unapproved\n"
        "threshold needs a decision that no amount of further work will supply."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
