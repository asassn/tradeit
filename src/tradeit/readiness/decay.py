"""Has it stopped working, and could we tell if it had?

The second half of Phase 12's question — *"is this good enough to trust with
money, **and how would we know if it stopped being?**"* — and the criterion
`live_vs_expected` in :mod:`tradeit.readiness.criteria` has had nothing
reporting to it.

The failure this module is built around
---------------------------------------

**A decay monitor that reports "consistent" on twelve trades is worse than no
monitor**, because it converts ignorance into reassurance. Twelve trades cannot
distinguish a strategy performing as expected from one that has lost most of
its edge, and a green light derived from them will be believed.

So every assessment carries the **minimum detectable shortfall**: how much worse
than expected the strategy would have to be before this many trades could show
it. A `CONSISTENT` verdict alongside *"this could not have detected a shortfall
smaller than 1.4% per trade"* is honest. The same verdict alone is not.

This is the power discipline this project already applied to its own research
(`PATTERN_PROGRAM_POWER_2026-10-16.md`, where the per-trade route was abandoned
once the minimum detectable effect was computed) turned on the monitoring.

What is measured, and what is not
---------------------------------

The comparison is **the realised mean trade return against the expectation's**,
standardised by the standard error of the realised mean. That is a deliberately
plain test, and two richer ones are rejected rather than omitted:

* **A rolling-window z-score**, which would catch decay sooner and multiplies
  the looks taken at the data. Every look is a trial, and this project keeps a
  ledger precisely because unregistered looks are how a decay alarm becomes a
  coin flip.
* **A distributional test** (Kolmogorov-Smirnov or similar) on the shape of
  returns rather than their mean. More sensitive and much harder to state a
  threshold for, and the threshold is already the part nobody has approved.

**No threshold lives here.** This module measures; `criteria.py` holds the bar,
and that bar is `UNAPPROVED` — so a decay measurement currently blocks rather
than demotes, which is the correct state for a number nobody has approved.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DecayAssessment",
    "DecayVerdict",
    "Expectation",
    "assess_decay",
]

#: Below this many live trades, no verdict is offered at all.
#:
#: Thirty, matching `PerformanceMetrics.statistically_meaningful` -- not chosen
#: here, and deliberately the same number so that "enough trades to interpret"
#: means one thing across the codebase rather than two.
MIN_TRADES_FOR_A_VERDICT = 30


class DecayVerdict(StrEnum):
    """What the live record supports saying."""

    #: Too few trades for any comparison to mean anything. **Not a pass.**
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    #: Realised performance is within what the expectation predicted.
    CONSISTENT = "consistent"
    #: Realised performance is below expectation by more than sampling noise.
    UNDERPERFORMING = "underperforming"
    #: Above expectation by more than sampling noise. Reported rather than
    #: celebrated: a strategy beating its backtest is usually a sign the
    #: backtest was wrong, and that is worth knowing.
    OUTPERFORMING = "outperforming"


@dataclass(frozen=True, slots=True)
class Expectation:
    """What the evidence predicted, and how much evidence that was.

    ``trade_count`` is required and is not decoration: an expectation drawn
    from forty backtested trades is a weaker claim than one drawn from eight
    hundred, and a monitor that treated them alike would report the same
    confidence about both.
    """

    mean_trade_return: float
    stdev_trade_return: float
    trade_count: int
    source: str

    def __post_init__(self) -> None:
        if self.stdev_trade_return < 0:
            raise ValueError(f"stdev cannot be negative, got {self.stdev_trade_return}")
        if self.trade_count < 1:
            raise ValueError("an expectation resting on no trades is not an expectation")
        if not self.source.strip():
            raise ValueError(
                "an expectation without a source cannot be checked later; name the "
                "backtest or paper run it came from"
            )


@dataclass(frozen=True, slots=True)
class DecayAssessment:
    """The comparison, and what it could and could not have detected."""

    verdict: DecayVerdict
    live_trade_count: int
    realised_mean: float | None
    expected_mean: float
    #: Realised minus expected, in units of the realised standard error.
    #: Negative means worse than expected. ``None`` when it cannot be computed.
    z: float | None
    #: The smallest shortfall this many trades could have detected, at the
    #: same confidence the verdict uses. **The number that stops a CONSISTENT
    #: verdict being read as proof.**
    minimum_detectable_shortfall: float | None
    note: str = ""

    @property
    def actionable(self) -> bool:
        """Whether this assessment supports a decision at all."""
        return self.verdict is not DecayVerdict.INSUFFICIENT_EVIDENCE

    def describe(self) -> str:
        if self.realised_mean is None:
            return f"{self.verdict}: {self.live_trade_count} live trades. {self.note}".strip()
        head = (
            f"{self.verdict}: {self.live_trade_count} trades, realised "
            f"{self.realised_mean:.3%} against expected {self.expected_mean:.3%}"
        )
        if self.z is not None and self.minimum_detectable_shortfall is not None:
            head += (
                f" (z {self.z:+.2f}); could not have detected a shortfall below "
                f"{self.minimum_detectable_shortfall:.3%}"
            )
        # The note is appended rather than replaced. A first version dropped it
        # on any assessment that had a realised mean, which lost the whole
        # explanation on the INSUFFICIENT_EVIDENCE case -- the one where the
        # explanation is the point.
        return f"{head}. {self.note}".strip() if self.note else head


def assess_decay(
    expectation: Expectation,
    live_returns: Sequence[float],
    *,
    confidence_z: float = 2.0,
) -> DecayAssessment:
    """Compare a live record against what the evidence predicted.

    ``confidence_z`` is the width of the band outside which a difference is
    called real. **2.0 is not a threshold on the strategy** -- it is how this
    function defines "more than sampling noise", and it is the same number used
    to compute the minimum detectable shortfall so the two cannot disagree. The
    bar on the *strategy* lives in `criteria.py` and is unapproved.
    """
    count = len(live_returns)
    if count < MIN_TRADES_FOR_A_VERDICT:
        return DecayAssessment(
            verdict=DecayVerdict.INSUFFICIENT_EVIDENCE,
            live_trade_count=count,
            realised_mean=statistics.fmean(live_returns) if live_returns else None,
            expected_mean=expectation.mean_trade_return,
            z=None,
            minimum_detectable_shortfall=None,
            note=(
                f"{count} live trades against a {MIN_TRADES_FOR_A_VERDICT}-trade minimum. "
                "No verdict is offered, because a comparison on this many trades cannot "
                "distinguish a working strategy from a broken one and would be believed "
                "anyway."
            ),
        )

    realised = statistics.fmean(live_returns)
    # The realised spread, not the expectation's: the question is whether THIS
    # sample's mean is far from the expectation, and its own variability is
    # what sets the scale. Using the backtest's spread would import an
    # assumption that live behaves like the backtest, which is the thing under
    # test.
    spread = statistics.stdev(live_returns) if count > 1 else 0.0
    if spread == 0:
        return DecayAssessment(
            verdict=DecayVerdict.INSUFFICIENT_EVIDENCE,
            live_trade_count=count,
            realised_mean=realised,
            expected_mean=expectation.mean_trade_return,
            z=None,
            minimum_detectable_shortfall=None,
            note=(
                "every live trade returned the same amount, so the spread is zero and "
                "no standardised comparison exists. Identical returns across dozens of "
                "trades is itself implausible and worth investigating before it is "
                "interpreted."
            ),
        )

    standard_error = spread / math.sqrt(count)
    z = (realised - expectation.mean_trade_return) / standard_error
    detectable = confidence_z * standard_error

    if z < -confidence_z:
        verdict = DecayVerdict.UNDERPERFORMING
    elif z > confidence_z:
        verdict = DecayVerdict.OUTPERFORMING
    else:
        verdict = DecayVerdict.CONSISTENT

    note = ""
    if verdict is DecayVerdict.CONSISTENT and detectable > abs(expectation.mean_trade_return):
        # The case that makes a green light meaningless: the monitor cannot
        # detect a shortfall smaller than the whole edge, so "consistent" is
        # compatible with the edge having vanished entirely.
        note = (
            f"the smallest detectable shortfall ({detectable:.3%}) exceeds the entire "
            f"expected edge ({expectation.mean_trade_return:.3%}), so this verdict is "
            "compatible with the edge having disappeared. It is not evidence the "
            "strategy is working."
        )
    elif verdict is DecayVerdict.OUTPERFORMING:
        note = (
            "beating the backtest by more than sampling noise usually means the "
            "backtest was wrong rather than the strategy improved; worth investigating "
            "in the same way as underperformance."
        )

    return DecayAssessment(
        verdict=verdict,
        live_trade_count=count,
        realised_mean=realised,
        expected_mean=expectation.mean_trade_return,
        z=z,
        minimum_detectable_shortfall=detectable,
        note=note,
    )
