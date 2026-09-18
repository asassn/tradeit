"""Cross-sectional information coefficients with an honest standard error.

Every IC in ``SIGNAL_SCOREBOARD.md`` before §32 was a **pooled** rank
correlation: one Spearman coefficient over every (security, date) observation,
with the t-statistic corrected only for horizon overlap. §32 found two defects
in that, both by a pre-registered positive control coming back inverted.

**It answers the wrong question.** Pooling across dates rewards a signal for
describing *when* the market was about to rise. A portfolio cannot trade that:
it must choose among the securities available today. Ranking **within** each
date asks which security, which is the question the product is organised
around. Over 2000-2009 the two had opposite signs for twelve-month momentum.

**Its standard error is wrong.** Hundreds of securities share each date and
therefore that date's market move, so they are not independent observations.
The independent unit is time, not the row.

This module computes the per-date IC (Fama-MacBeth) and takes its standard error
from **non-overlapping calendar blocks**, one horizon long, with a Newey-West
correction for the one block of overlap that remains between neighbours.

**Why blocks rather than "divide the dates by horizon/stride".** §32's first
correction did exactly that, and it is only right when every sample date sits a
full stride from the next. It does not here: each security samples on its own
grid from its own first bar, so the §26 panel carries 1,770 usable dates across
a decade -- nearly one per session -- and consecutive dates share almost all of
their 63-session outcome window. Dividing 1,770 by three would claim ~590
independent periods from a decade that contains about forty non-overlapping
63-session windows. Blocking by calendar time counts what the decade actually
contains, whatever the sampling grid did.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Floats = NDArray[np.float64]

#: A date carrying fewer securities than this is not a usable cross-section; its
#: IC would be an artefact of a handful of names.
MIN_PER_DATE = 20
#: Calendar days per trading session, for turning a horizon in sessions into a
#: calendar block length without needing an exchange calendar.
DAYS_PER_SESSION = 365.25 / 252.0


def _ranks(values: Floats) -> Floats:
    """Average ranks, so ties do not depend on input order.

    Vectorised: a tie group's members all receive the mean of the positions the
    group spans. The first version looped over tie groups in Python, which is
    fine for one cross-section and too slow for the 200-permutation calibration
    the adx_14 confirmation registered.
    """
    n = values.shape[0]
    order = np.argsort(values, kind="mergesort")
    ordered = values[order]
    starts = np.concatenate(([0], np.flatnonzero(np.diff(ordered)) + 1))
    ends = np.concatenate((starts[1:], [n]))
    mean_position = (starts + ends - 1) / 2.0
    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = np.repeat(mean_position, ends - starts)
    return ranks


def spearman(x: Floats, y: Floats) -> float | None:
    """Rank correlation, or ``None`` when either side is constant."""
    if x.shape[0] < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    if np.std(rx) == 0.0 or np.std(ry) == 0.0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


@dataclass(frozen=True, slots=True)
class CrossSectionalIC:
    """A per-date information coefficient and what its significance rests on."""

    #: Mean of the calendar-block means of the per-date ICs. The same number
    #: ``t`` is built on, by construction -- see ``cross_sectional_ic``.
    ic: float
    #: t-statistic from non-overlapping calendar blocks, Newey-West lag 1.
    t: float | None
    #: Dates that carried at least :data:`MIN_PER_DATE` securities.
    dates: int
    #: Non-overlapping horizon-length calendar blocks those dates fall in. This,
    #: not the row count and not the date count, is the sample size.
    blocks: int
    #: Standard deviation of the block means, which sets the resolution.
    block_sd: float
    #: Median securities per usable date. **Read this before the IC.** The
    #: estimate is an unweighted mean over dates, so when breadth is uneven the
    #: many thin dates outvote the few broad ones: on the §26 panel, 1,592 dates
    #: of 20-199 recent listings read -0.128 while the 119 broad dates holding
    #: 73% of the observations read -0.056, and the headline came out -0.123.
    #: A median far below the mean breadth means the estimate describes the
    #: thin population, whatever share of the data it holds.
    median_breadth: float = 0.0

    def detectable(self, hurdle: float) -> float | None:
        """The smallest |IC| this sample could have shown clearing ``hurdle``.

        A null is only as strong as this number. Reporting "no effect" without
        it claims an absence the test may have been unable to see.
        """
        if self.blocks < 3 or self.block_sd == 0.0:
            return None
        return (
            hurdle * self.block_sd * float(np.sqrt(self._inflation)) / float(np.sqrt(self.blocks))
        )

    #: Variance inflation from the Newey-West term, carried so ``detectable``
    #: uses the same standard error as ``t``.
    _inflation: float = 1.0


def _newey_west_inflation(series: Floats) -> float:
    """Bartlett-weighted variance inflation for one lag of autocorrelation.

    Adjacent blocks still share part of their outcome window -- a date near the
    end of one block has a horizon running into the next -- so their means are
    positively correlated, and ignoring that would overstate the t-statistic.
    One lag covers it, because blocks two apart share nothing.

    Floored at 1: a negative estimated autocorrelation would otherwise *shrink*
    the standard error, which is not a direction this correction is entitled to
    move it.
    """
    n = series.shape[0]
    if n < 4:
        return 1.0
    centred = series - series.mean()
    variance = float(np.dot(centred, centred)) / n
    if variance == 0.0:
        return 1.0
    lag1 = float(np.dot(centred[1:], centred[:-1])) / n
    return float(max(1.0, 1.0 + 2.0 * 0.5 * lag1 / variance))


def cross_sectional_ic(
    signal: Sequence[float] | Floats,
    outcome: Sequence[float] | Floats,
    dates: Sequence[dt.date],
    horizon_sessions: int,
    *,
    min_per_date: int = MIN_PER_DATE,
) -> CrossSectionalIC | None:
    """Fama-MacBeth IC with a calendar-block, Newey-West standard error.

    Returns ``None`` only when no date carries a usable cross-section. With
    usable dates but fewer than three blocks, it returns the estimate with
    ``t=None`` -- too little time to judge significance, which is a real answer
    rather than an error, and the estimate is still worth reporting.
    """
    x = np.asarray(signal, dtype=np.float64)
    y = np.asarray(outcome, dtype=np.float64)
    if not (x.shape[0] == y.shape[0] == len(dates)):
        raise ValueError("signal, outcome and dates must be the same length")

    by_date: dict[dt.date, list[int]] = {}
    for index, date in enumerate(dates):
        by_date.setdefault(date, []).append(index)

    per_date: list[tuple[dt.date, float]] = []
    for date in sorted(by_date):
        rows = by_date[date]
        if len(rows) < min_per_date:
            continue
        ic = spearman(x[rows], y[rows])
        if ic is not None:
            per_date.append((date, ic))
    if not per_date:
        return None
    breadth = float(np.median([len(by_date[date]) for date, _ in per_date]))

    origin = per_date[0][0]
    width = max(1, round(horizon_sessions * DAYS_PER_SESSION))
    blocks: dict[int, list[float]] = {}
    for date, ic in per_date:
        blocks.setdefault((date - origin).days // width, []).append(ic)
    means = np.array([np.mean(blocks[b]) for b in sorted(blocks)], dtype=np.float64)

    # The estimate IS the mean of the block means -- the same quantity the t is
    # built on. The first version reported the mean over DATES and built the t
    # on the mean over BLOCKS, calling the difference slight. It was not: on
    # §32's panel it produced a positive control reading IC +0.0141 with
    # t -0.01, opposite signs, and lifted adx_14 from t +1.33 to +2.55 -- past
    # the hurdle -- on nothing but the mismatch. An estimate and its standard
    # error must describe one number. Equal weight per calendar block is also
    # the honest time average: a period the sampling grid happened to visit
    # forty times is not forty times as informative as one it visited once.
    estimate = float(np.mean(means))
    if means.shape[0] < 3:
        return CrossSectionalIC(estimate, None, len(per_date), int(means.shape[0]), 0.0, breadth)
    sd = float(np.std(means, ddof=1))
    inflation = _newey_west_inflation(means)
    if sd == 0.0:
        return CrossSectionalIC(estimate, None, len(per_date), int(means.shape[0]), 0.0, breadth)
    t = estimate / (sd * float(np.sqrt(inflation)) / float(np.sqrt(means.shape[0])))
    return CrossSectionalIC(
        ic=estimate,
        t=t,
        dates=len(per_date),
        blocks=int(means.shape[0]),
        block_sd=sd,
        median_breadth=breadth,
        _inflation=inflation,
    )
