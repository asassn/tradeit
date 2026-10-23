"""What a strategy must show, and where every number in that list came from.

The roadmap is unusually blunt about this phase: the measurement contracts
already exist, and *"what does not exist is the framework that turns those
numbers into a decision, and it is the deliverable here."* This module is the
list of criteria. :mod:`tradeit.readiness.assess` applies it.

Two rules shape the design, and both are from the roadmap rather than invented
here:

**No single metric decides.** A criterion set, evaluated as a conjunction, with
every criterion's verdict reported individually — because "it passed" and "it
passed on nine of ten criteria" are different findings and the second is the
one worth reading.

**No threshold may be chosen after seeing the result it would judge.** That is
easy to write and hard to enforce, because the natural way to pick a number is
to look at the output and choose one that separates good from bad. So every
threshold here carries a :class:`ThresholdSource` and a citation, and a
threshold whose source is :attr:`ThresholdSource.UNAPPROVED` **blocks promotion
rather than being applied**. The framework refuses to judge with a number
nobody chose.

That last decision is the `UNRESOLVED`-is-first-class discipline turned on the
framework itself. It also means this module can ship complete and honest while
several of its numbers are still awaiting the owner's approval: the gate says
exactly which ones and why, instead of quietly using a default somebody made
up.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tradeit.strategy.lifecycle import LADDER, StrategyState

__all__ = [
    "DEMOTION_CRITERIA",
    "PROMOTION_CRITERIA",
    "Comparison",
    "Criterion",
    "Threshold",
    "ThresholdSource",
    "criteria_for",
    "demotion_criteria_for",
]


class ThresholdSource(StrEnum):
    """Where a threshold's value came from. Not decoration — it gates use."""

    #: Traceable to a written decision already in this repository: an ADR, the
    #: roadmap, a config field with its own recorded rationale, or a scoreboard
    #: entry. Quotable.
    WRITTEN = "written"
    #: Arithmetic on a WRITTEN value, with the arithmetic stated. A derived
    #: threshold is only as good as its parent and says so.
    DERIVED = "derived"
    #: Chosen by nobody. **Blocks promotion.** Present so the criterion can be
    #: declared and measured while its number is still owed, rather than being
    #: left out of the framework and silently unenforced.
    UNAPPROVED = "unapproved"


class Comparison(StrEnum):
    """Which direction passes."""

    AT_LEAST = "at_least"
    AT_MOST = "at_most"


@dataclass(frozen=True, slots=True)
class Threshold:
    """A number, and the written decision it comes from."""

    value: float
    source: ThresholdSource
    citation: str

    def __post_init__(self) -> None:
        if not self.citation.strip():
            raise ValueError(
                "a threshold without a citation is a number nobody chose wearing the "
                "appearance of one that was; state where it came from, or mark it "
                "UNAPPROVED and say what is owed"
            )

    @property
    def usable(self) -> bool:
        return self.source is not ThresholdSource.UNAPPROVED


@dataclass(frozen=True, slots=True)
class Criterion:
    """One thing a strategy must show, and the rung from which it is asked.

    ``metric`` names the field on the evidence that answers it. Named rather
    than passed as a callable so a report can say *which* measurement was
    missing when one is, instead of reporting an opaque failure.
    """

    name: str
    question: str
    metric: str
    comparison: Comparison
    threshold: Threshold
    #: The first rung at which this criterion applies. A criterion is asked of
    #: every transition INTO that state or above it, so the bar rises with the
    #: ladder rather than every criterion applying from DRAFT.
    applies_from: StrategyState

    def satisfied_by(self, measured: float) -> bool:
        if self.comparison is Comparison.AT_LEAST:
            return measured >= self.threshold.value
        return measured <= self.threshold.value


def _rung(state: StrategyState) -> int:
    return LADDER.index(state)


# ---------------------------------------------------------------------------
# Promotion criteria.
#
# Grouped by what they protect against, because a flat list reads as a
# checklist and these are not interchangeable.
# ---------------------------------------------------------------------------

PROMOTION_CRITERIA: tuple[Criterion, ...] = (
    # -- is there enough to interpret at all --------------------------------
    Criterion(
        name="trade_count",
        question="Are there enough trades for these ratios to mean anything?",
        metric="trade_count",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=30,
            source=ThresholdSource.WRITTEN,
            citation=(
                "PerformanceMetrics.statistically_meaningful, backtesting/base.py: "
                "'Thirty is a low bar, deliberately'"
            ),
        ),
        applies_from=StrategyState.BACKTESTING,
    ),
    Criterion(
        name="result_trustworthy",
        question="Did the run complete on backtest-grade data with no caveats?",
        metric="trustworthy",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1,
            source=ThresholdSource.WRITTEN,
            citation=(
                "BacktestResult.trustworthy, backtesting/base.py: exists 'so that "
                "reports cannot present a caveated result as a clean one by omission'"
            ),
        ),
        applies_from=StrategyState.BACKTESTING,
    ),
    Criterion(
        name="reproducible",
        question="Does a manifest exist that could reproduce this result?",
        metric="has_manifest",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1,
            source=ThresholdSource.WRITTEN,
            citation=(
                'ROADMAP Phase 12: \'no result of the form "this made 42%" can exist '
                "without the means to reproduce it'"
            ),
        ),
        applies_from=StrategyState.BACKTESTING,
    ),
    # -- is the corpus honest enough to rest a decision on ------------------
    Criterion(
        name="survivorship_corrected",
        question="Was this measured on a corpus that does not lie about survivorship?",
        metric="survivorship_corrected",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1,
            source=ThresholdSource.WRITTEN,
            citation=(
                "strategy/admissibility.py and CLAUDE.md: a version may enter "
                "BACKTESTING on a survivor-biased corpus and may not climb out of it"
            ),
        ),
        applies_from=StrategyState.OUT_OF_SAMPLE_TESTING,
    ),
    # -- does it survive being tested properly ------------------------------
    Criterion(
        name="out_of_sample_clears_hurdle",
        question="Does the held-out result clear the trial-ledger hurdle?",
        metric="out_of_sample_t_over_hurdle",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1.0,
            source=ThresholdSource.DERIVED,
            citation=(
                "The ledger hurdle at assessment time, expressed as a ratio. The "
                "metric is t divided by small_sample_hurdle(trials, blocks), so "
                "1.0 means exactly 'clears the hurdle the ledger computed for this "
                "test' and **no constant is encoded** -- which is what the question "
                "recorded here required, since a fixed number would contradict the "
                "ledger. DERIVED, not WRITTEN: the parent is the ledger hurdle, "
                "and this threshold is only as good as that. "
                "`readiness.hurdle_evidence` does the division and refuses a "
                "non-positive hurdle. "
                "Rejected: a standing minimum. The hurdle RISES with trials -- it "
                "reads 2.6563 at 144 -- so any fixed bar becomes weaker than the "
                "ledger as trials accumulate, and would eventually pass a result "
                "the ledger rejects. That is the failure mode this criterion "
                "exists to prevent, arrived at by a constant that looked "
                "conservative when it was written. This is a wiring decision "
                "rather than a new bar: it declines to choose any bar other than "
                "the one already written down."
            ),
        ),
        applies_from=StrategyState.OUT_OF_SAMPLE_TESTING,
    ),
    Criterion(
        name="pre_registered",
        question="Was the held-out test registered before the window was read?",
        metric="pre_registered",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1,
            source=ThresholdSource.WRITTEN,
            citation=(
                "research01/holdout.py: the held-out window is refused without a "
                "committed pre-registration"
            ),
        ),
        applies_from=StrategyState.OUT_OF_SAMPLE_TESTING,
    ),
    Criterion(
        name="beats_baselines",
        question="Does it beat buy-and-hold, the benchmark and a naive rule?",
        metric="baselines_beaten",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1,
            source=ThresholdSource.WRITTEN,
            citation=(
                "ROADMAP Phase 9: 'a sophisticated model that cannot beat them "
                "robustly is not promoted for being sophisticated'"
            ),
        ),
        applies_from=StrategyState.OUT_OF_SAMPLE_TESTING,
    ),
    Criterion(
        name="walk_forward_consistency",
        question="Does it hold up across walk-forward windows rather than one?",
        metric="walk_forward_consistency",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=0.0,
            source=ThresholdSource.UNAPPROVED,
            citation=(
                "OWED: backtesting/walkforward.py computes `consistency`; no written "
                "decision says what value is good enough. Picking one now, after "
                "this project's walk-forward results exist, is exactly the "
                "after-the-fact threshold the roadmap forbids."
            ),
        ),
        applies_from=StrategyState.PAPER_TRADING,
    ),
    Criterion(
        name="out_of_sample_degradation",
        question="How much of the in-sample edge survives out of sample?",
        metric="degradation",
        comparison=Comparison.AT_MOST,
        threshold=Threshold(
            value=0.0,
            source=ThresholdSource.UNAPPROVED,
            citation=(
                "OWED. Measured twice: SIGNAL_SCOREBOARD §52 and §53 both kept their "
                "direction and lost roughly two thirds of magnitude. Two observations "
                "describe what happened; they do not establish an acceptable limit, "
                "and setting one from them would be fitting the bar to the data."
            ),
        ),
        applies_from=StrategyState.PAPER_TRADING,
    ),
    # -- would it survive contact with a market -----------------------------
    Criterion(
        name="cost_sensitivity",
        question="Does the edge survive a doubling of modelled costs?",
        metric="survives_double_costs",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1,
            source=ThresholdSource.WRITTEN,
            citation=(
                "execution/base.py: 'A strategy that trades often can be profitable "
                "at 2 basis points of slippage and unprofitable at 15' -- the model "
                "is swappable so the assumption can be stress-tested"
            ),
        ),
        applies_from=StrategyState.PAPER_TRADING,
    ),
    Criterion(
        name="paper_divergence",
        question="Do paper and backtest agree on the same orders?",
        metric="paper_backtest_agreement",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1.0,
            source=ThresholdSource.DERIVED,
            citation=(
                "PHASE_11_AUDIT_2026-10-18.md: 1,457 of 1,457 fills agreed exactly "
                "once splits and order ids were fixed. Exact agreement is the "
                "demonstrated state of the system, so anything less is a regression "
                "rather than a tolerance -- which is why this is DERIVED and 1.0"
            ),
        ),
        applies_from=StrategyState.ELIGIBLE_FOR_CAPITAL,
    ),
    Criterion(
        name="max_drawdown",
        question="Did it stay inside the drawdown the portfolio would halt at?",
        metric="max_drawdown_pct",
        comparison=Comparison.AT_MOST,
        threshold=Threshold(
            value=0.15,
            source=ThresholdSource.WRITTEN,
            citation="RiskConfig.drawdown_halt_pct, strategy/config.py (default 0.15)",
        ),
        applies_from=StrategyState.PAPER_TRADING,
    ),
    Criterion(
        name="live_authorised",
        question="Is there a machine-local authorisation for live trading?",
        metric="live_authorised",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1,
            source=ThresholdSource.WRITTEN,
            citation="ADR-0004 and config.py: the live interlock requires an authorisation file",
        ),
        applies_from=StrategyState.LIVE,
    ),
)


# ---------------------------------------------------------------------------
# Demotion criteria. **Any one of these firing is enough.**
#
# The asymmetry is deliberate and is the roadmap's: promotion is a conjunction
# because every piece of evidence must hold, and demotion is a disjunction
# because one broken thing is sufficient reason to stop. A framework that
# required unanimity to demote would keep a decayed strategy running.
# ---------------------------------------------------------------------------

DEMOTION_CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        name="drawdown_breached",
        question="Has the running drawdown passed the halt?",
        metric="live_drawdown_pct",
        comparison=Comparison.AT_MOST,
        threshold=Threshold(
            value=0.15,
            source=ThresholdSource.WRITTEN,
            citation="RiskConfig.drawdown_halt_pct, strategy/config.py",
        ),
        applies_from=StrategyState.PAPER_TRADING,
    ),
    Criterion(
        name="reconciliation_clean",
        question="Does our book still agree with the broker's?",
        metric="reconciles",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=1,
            source=ThresholdSource.WRITTEN,
            citation=(
                "execution/service.py: a disagreement halts, because every decision "
                "after one is computed from a book that does not exist"
            ),
        ),
        applies_from=StrategyState.PAPER_TRADING,
    ),
    Criterion(
        name="live_matches_expectation",
        question="Is live performance within what the evidence predicted?",
        metric="live_vs_expected",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=-2.0,
            source=ThresholdSource.DERIVED,
            citation=(
                "ADR-0029, approved by the owner 2026-10-23. Derived, not chosen: "
                "-2.0 is the same `confidence_z` that `readiness/decay.py` already "
                "uses to define 'more than sampling noise', so the bar on the "
                "strategy and the function measuring it cannot disagree. The z is "
                "computed against the LIVE sample's own spread, so the number comes "
                "from the data at assessment time rather than from a figure fitted "
                "to results already seen. At -2 a working strategy comes down on "
                "ordinary noise roughly one time in forty; the zero floor below is "
                "what covers the opposite failure, a bleed too slow to reach "
                "significance."
            ),
        ),
        applies_from=StrategyState.LIVE,
    ),
    Criterion(
        name="live_is_not_losing_money",
        question="Over a verdict-sized live record, is the mean trade return above zero?",
        metric="live_mean_trade_return",
        comparison=Comparison.AT_LEAST,
        threshold=Threshold(
            value=0.0,
            source=ThresholdSource.WRITTEN,
            citation=(
                "ADR-0029, approved by the owner 2026-10-23. **Zero is a natural "
                "boundary, not an invented threshold** -- the only number on this "
                "ladder that needs no justification beyond its own meaning, which "
                "is why it can be set without the after-the-fact problem that keeps "
                "`walk_forward_consistency` and `out_of_sample_degradation` "
                "UNAPPROVED. It exists because the statistical test above is slow "
                "on a noisy strategy: a strategy can lose money for a long time "
                "without the shortfall becoming significant. A strategy that is "
                "losing money live, over a verdict-sized record, comes down whether "
                "or not the loss is distinguishable from noise. "
                "`decay.demotion_evidence` omits this metric below "
                "MIN_TRADES_FOR_A_VERDICT trades, so it reports UNRESOLVED rather "
                "than demoting on a handful of trades."
            ),
        ),
        applies_from=StrategyState.LIVE,
    ),
)


def criteria_for(target: StrategyState) -> tuple[Criterion, ...]:
    """Every promotion criterion that applies to entering ``target``.

    Cumulative: a criterion that applies from BACKTESTING is still asked at
    LIVE. A framework whose early criteria stopped being checked would let a
    strategy climb past evidence it no longer has.
    """
    if target not in LADDER:
        return ()
    ceiling = _rung(target)
    return tuple(
        criterion
        for criterion in PROMOTION_CRITERIA
        if criterion.applies_from in LADDER and _rung(criterion.applies_from) <= ceiling
    )


def demotion_criteria_for(current: StrategyState) -> tuple[Criterion, ...]:
    """Every demotion criterion that applies to a version sitting at ``current``."""
    if current not in LADDER:
        return ()
    ceiling = _rung(current)
    return tuple(
        criterion
        for criterion in DEMOTION_CRITERIA
        if criterion.applies_from in LADDER and _rung(criterion.applies_from) <= ceiling
    )
