"""Counting the trials, and charging for them.

``BacktestSpec`` states the commitment this module keeps: *counting how many
variations were tried is the only defence against reporting the best of two
hundred as if it were the first of one.* Until now nothing counted.

The problem is not dishonesty. A researcher who tries two hundred parameter
sets and reports the best one has done nothing wrong except omit the two
hundred, and the omission is usually accidental — the failures were deleted,
the notebook was rerun, the earlier attempts "didn't count because they were
just exploring". The best of two hundred coin-flipping strategies has a
flattering Sharpe ratio, and no amount of out-of-sample discipline inside a
single trial detects it. Only the count does.

The hurdle
----------

Under the null hypothesis of no skill, an annualised Sharpe ratio estimated
over *y* years has a standard error of roughly ``1 / sqrt(y)``. Draw *N*
independent estimates from that distribution and the largest one is not zero;
its expected value follows from extreme-value theory and grows with ``log N``.
:func:`sharpe_hurdle` computes it. A result that does not clear its own hurdle
is not evidence, however good it looks on its own.

Three assumptions, stated because they bound what this can claim:

* **Returns are treated as normal.** Real return series are fat-tailed and
  skewed, which makes a Sharpe ratio flatter its strategy to begin with. The
  hurdle does not correct for that, so clearing it is necessary rather than
  sufficient.
* **Trials are treated as independent.** They rarely are — two hundred nearby
  parameter sets are not two hundred experiments. Correlation means the
  effective number of trials is *lower* than the count, so the hurdle is too
  high rather than too low. That is the safe direction, and it is the reason
  this is a screening tool rather than a p-value.
* **The count must be recorded, not estimated.** A ledger nobody wrote to
  reports one trial and clears everything. :meth:`TrialLedger.assess` returns
  ``UNRESOLVED`` when no trial matching the result was ever recorded, rather
  than assuming it was the first.

A re-run is not a new trial
---------------------------

Trials are counted by the content of the specification, so running the same
backtest twice counts once. That is deliberate and it cuts both ways: it means
a flaky pipeline cannot inflate the count, and it means changing a single
parameter — which is what searching *is* — always does.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from tradeit.backtesting.base import BacktestResult, BacktestSpec
from tradeit.reproducibility.versioning import content_hash, short_hash

__all__ = ["EULER_MASCHERONI", "Trial", "TrialLedger", "TrialVerdict", "sharpe_hurdle"]

#: Euler-Mascheroni constant, from the expected maximum of N normal draws.
EULER_MASCHERONI = 0.5772156649015329


class TrialVerdict(StrEnum):
    """What the trial count says about a result.

    ``UNRESOLVED`` is a first-class answer, not an error: a result whose search
    history was never recorded cannot be judged, and saying so is the honest
    outcome. Treating it as a single trial would let an unrecorded search pass.
    """

    CLEARS_HURDLE = "clears_hurdle"
    BELOW_HURDLE = "below_hurdle"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


def trial_key(spec: BacktestSpec) -> str:
    """The identity of a variation.

    Built from the fields that change what is being tested. ``name`` is
    excluded on purpose: relabelling a run is not a new experiment, and if it
    counted, a search could be hidden by renaming.
    """
    return content_hash(
        {
            "start": spec.start.isoformat(),
            "end": spec.end.isoformat(),
            "universe": spec.universe,
            "initial_capital": str(spec.initial_capital),
            "strategy_config_digest": spec.strategy_config_digest,
            "cost_model": spec.cost_model,
            "fill_model": spec.fill_model,
            "benchmark_instrument_id": spec.benchmark_instrument_id,
            "warmup_sessions": spec.warmup_sessions,
        }
    )


def expected_max_of_normals(trials: int) -> float:
    """Expected maximum of ``trials`` independent standard normal draws.

    The standard extreme-value approximation. Exact enough from about three
    trials and conservative below that, where it is barely used anyway.
    """
    if trials <= 1:
        return 0.0
    normal = statistics.NormalDist()
    return (1 - EULER_MASCHERONI) * normal.inv_cdf(1 - 1 / trials) + (
        EULER_MASCHERONI * normal.inv_cdf(1 - 1 / (trials * math.e))
    )


def sharpe_hurdle(trials: int, years: float) -> float | None:
    """The annualised Sharpe a no-skill search would be expected to produce.

    ``None`` when the period is too short to estimate a Sharpe at all. Returning
    zero there would say "any positive Sharpe clears", which is the opposite of
    what too little data means.
    """
    if years <= 0:
        return None
    if trials <= 1:
        # One trial still has to beat zero, but nothing more. The multiple-
        # testing penalty is precisely the part that does not apply.
        return 0.0
    return expected_max_of_normals(trials) / math.sqrt(years)


@dataclass(frozen=True, slots=True)
class Trial:
    """One recorded variation and what it produced."""

    key: str
    recorded_at: dt.datetime
    description: str
    sharpe: float | None = None

    @property
    def label(self) -> str:
        return short_hash(self.key)


@dataclass(slots=True)
class TrialLedger:
    """Every variation tried against one hypothesis.

    Mutable by design: a ledger is a log, and a log that cannot be appended to
    during a search is a log nobody keeps.
    """

    hypothesis: str
    trials: dict[str, Trial] = field(default_factory=dict)

    def record(
        self,
        spec: BacktestSpec,
        *,
        at: dt.datetime,
        description: str = "",
        sharpe: float | None = None,
    ) -> Trial:
        """Log one variation. Re-running the same specification counts once."""
        key = trial_key(spec)
        existing = self.trials.get(key)
        if existing is not None:
            # Keep the first recording's timestamp; update the outcome, because
            # a re-run with a fixed data bug is the same experiment.
            # `sharpe if not None` rather than `or`: a genuine Sharpe of 0.0
            # is falsy, and `or` would silently discard it.
            trial = dataclasses.replace(
                existing, sharpe=sharpe if sharpe is not None else existing.sharpe
            )
        else:
            trial = Trial(key=key, recorded_at=at, description=description, sharpe=sharpe)
        self.trials[key] = trial
        return trial

    def record_all(self, specs: Iterable[BacktestSpec], *, at: dt.datetime) -> None:
        for spec in specs:
            self.record(spec, at=at)

    @property
    def count(self) -> int:
        return len(self.trials)

    def knows(self, spec: BacktestSpec) -> bool:
        return trial_key(spec) in self.trials

    def hurdle(self, years: float) -> float | None:
        return sharpe_hurdle(self.count, years)

    def assess(self, result: BacktestResult) -> tuple[TrialVerdict, str]:
        """Judge one result against the search that produced it.

        Returns the verdict and a sentence fit to put in a report. The sentence
        always names the trial count, because a Sharpe quoted without it is the
        omission this module exists to prevent.
        """
        if not self.knows(result.spec):
            return (
                TrialVerdict.UNRESOLVED,
                f"this run is not in the '{self.hypothesis}' ledger, so the number of "
                "variations behind it is unknown; an unrecorded search cannot be judged",
            )
        if result.metrics is None or result.metrics.sharpe is None:
            return (
                TrialVerdict.NOT_APPLICABLE,
                f"no Sharpe ratio was computed, so the {self.count}-trial hurdle cannot be applied",
            )
        years = (result.spec.end - result.spec.start).days / 365.25
        hurdle = self.hurdle(years)
        if hurdle is None:
            return (
                TrialVerdict.NOT_APPLICABLE,
                "the test period is too short to estimate a Sharpe ratio",
            )
        observed = result.metrics.sharpe
        shape = (
            f"Sharpe {observed:.2f} against a {hurdle:.2f} hurdle for {self.count} "
            f"trial{'s' if self.count != 1 else ''} over {years:.1f} years"
        )
        if observed > hurdle:
            return TrialVerdict.CLEARS_HURDLE, shape
        return (
            TrialVerdict.BELOW_HURDLE,
            f"{shape}: a no-skill search of this size would be expected to produce this result",
        )
