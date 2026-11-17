"""The walk-forward grid: did the edge survive out of sample?

**This is the question the whole project turns on**, and the record that
answers it had almost no surface. `robustness_record.sqlite` holds 53 runs in
26 in-sample/out-of-sample pairs and 17,392 trades, and `/runs` listed them as
a flat table where the pairing — the only structure that matters — was
invisible.

The ratio reported here is ``edge_retained``, defined in
`backtesting/walkforward.py` and **reproduced rather than reinvented**: the
out-of-sample CAGR over the in-sample CAGR. A ratio rather than a difference,
and on CAGR rather than total return, because subtracting one total return
from another gives percentage points whose size depends on how long the spans
were — "lost 40 points" means something different for a strategy that made 60%
than for one that made 45%, and on an anchored scheme the in-sample span grows
to fifteen years against a one-year test.

``None`` when in-sample CAGR is not positive, and that is fail-closed rather
than tidy: **a strategy with no in-sample edge has no edge to retain**, so the
ratio would be an artefact of dividing by something near zero. Those windows
are reported as unresolved and are **skipped, not zeroed**, in the mean — a
zero would read as "the edge vanished" when the truth is that there was none.

The bar is ADR-0034's, approved by the owner on 2026-11-15, and this module
reads it from `readiness.criteria` rather than restating it. A threshold
copied into a second place is a threshold that will disagree with itself.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.readiness.criteria import PROMOTION_CRITERIA
from tradeit.storage.tables import BacktestRun

__all__ = ["EDGE_RETAINED_BAR", "walkforward_payload"]

#: Run names follow `<strategy>/wf-<scheme>-<n>-is` and `-oos`. The pairing is
#: carried in the name and nowhere else -- there is no column joining a window
#: to its own out-of-sample test.
_WINDOW = re.compile(r"^(?P<stem>.+)-(?P<leg>is|oos)$")


#: The criterion carrying the fragility bar, looked up by NAME.
#:
#: Found by `metric` at first, which broke the moment ADR-0038 renamed the
#: metric to `edge_retained_window_share` — the route's own tests caught it
#: the same afternoon. The name is the stable identifier: `metric` says what
#: evidence the criterion consumes and is free to change when the evaluation
#: changes, which is exactly what happened.
FRAGILITY_CRITERION = "out_of_sample_degradation"


def _bar() -> tuple[float | None, str]:
    """The approved fragility bar, read from the readiness criteria.

    Not restated here. A number copied into a second place is a number that
    will eventually disagree with itself, and this one is a written decision
    (ADR-0034, evaluation amended by ADR-0038) rather than an implementation
    detail.

    **The same 0.5 does two jobs**, which is what made ADR-0038 cheap: the
    share of its edge each window must retain, and the share of windows that
    must retain it. This module applies it to the first; `readiness` applies
    it to the second.
    """
    for criterion in PROMOTION_CRITERIA:
        if criterion.name == FRAGILITY_CRITERION:
            return criterion.threshold.value, criterion.threshold.citation
    return None, f"no {FRAGILITY_CRITERION} criterion is declared"


EDGE_RETAINED_BAR, EDGE_RETAINED_CITATION = _bar()


def walkforward_payload(session: Session) -> dict[str, Any]:
    """Every in-sample window paired with its own out-of-sample test."""
    runs = session.execute(select(BacktestRun).order_by(BacktestRun.id)).scalars().all()

    legs: dict[tuple[str, str], BacktestRun] = {}
    unpaired: list[str] = []
    for run in runs:
        match = _WINDOW.match(run.name)
        if match is None:
            unpaired.append(run.name)
            continue
        legs[(match.group("stem"), match.group("leg"))] = run

    stems = sorted({stem for stem, _ in legs})
    windows: list[dict[str, Any]] = []
    retained: list[float] = []

    for stem in stems:
        in_sample = legs.get((stem, "is"))
        out_of_sample = legs.get((stem, "oos"))
        if in_sample is None or out_of_sample is None:
            # A half-pair is not a window. Reported rather than dropped: a
            # missing out-of-sample leg is the one most worth noticing.
            unpaired.append(f"{stem} (only the {'in' if in_sample else 'out of'}-sample leg)")
            continue

        edge: float | None = None
        unresolved: str | None = None
        if in_sample.cagr is None or out_of_sample.cagr is None:
            unresolved = "a leg recorded no CAGR"
        elif in_sample.cagr <= 0:
            unresolved = (
                f"in-sample CAGR is {in_sample.cagr:.2%}; a strategy with no "
                "in-sample edge has no edge to retain, so the ratio would be an "
                "artefact of dividing by something near zero"
            )
        else:
            edge = out_of_sample.cagr / in_sample.cagr
            retained.append(edge)

        windows.append(
            {
                "window": stem.split("/")[-1],
                "strategy": stem.split("/")[0],
                "in_sample": {
                    "start": in_sample.start_date.isoformat(),
                    "end": in_sample.end_date.isoformat(),
                    "cagr": in_sample.cagr,
                    "sharpe": in_sample.sharpe,
                    "max_drawdown_pct": in_sample.max_drawdown_pct,
                },
                "out_of_sample": {
                    "start": out_of_sample.start_date.isoformat(),
                    "end": out_of_sample.end_date.isoformat(),
                    "cagr": out_of_sample.cagr,
                    "sharpe": out_of_sample.sharpe,
                    "max_drawdown_pct": out_of_sample.max_drawdown_pct,
                },
                "edge_retained": edge,
                "unresolved": unresolved,
                "clears_bar": None if edge is None else edge >= (EDGE_RETAINED_BAR or 0),
            }
        )

    # Skipped, never zeroed. A window with no in-sample edge contributes
    # nothing to a mean about how much edge survived, and a zero would read as
    # "the edge vanished" when the truth is that there was none to begin with.
    #
    # This mirrors `WalkForwardReport.mean_edge_retained` exactly, including
    # the arithmetic mean, because the metric is a published definition behind
    # an approved threshold and changing it needs a scoped proposition.
    mean = sum(retained) / len(retained) if retained else None

    # **The mean cannot be shown alone**, and this is the most important
    # decision in the module.
    #
    # `edge_retained` is a ratio whose denominator is an in-sample CAGR, so a
    # window that barely made money in sample produces an enormous ratio from
    # an ordinary out-of-sample year — and an arithmetic mean is dominated by
    # exactly those windows. Measured on the robustness record 2026-11-17:
    # `wf-rolling-7` made **0.51%** in sample and 21.78% out, which is a ratio
    # of 42.4, and it alone pulls the mean of 21 windows from 0.70 to 4.21.
    #
    # "Mean edge retained 4.21, clears the 0.5 bar" is a true sentence and a
    # dangerously flattering summary of a record in which **eight of
    # twenty-one windows lost money out of sample while making it in sample**.
    # So the distribution travels with the mean: the median, how many windows
    # actually clear the bar, how many went negative, and the smallest
    # in-sample CAGR among them, which is the number that explains the tail.
    #
    # No new threshold is invented here. These are facts about the
    # distribution, and the reader draws the conclusion.
    median = statistics.median(retained) if retained else None
    clearing = (
        sum(1 for value in retained if value >= EDGE_RETAINED_BAR)
        if retained and EDGE_RETAINED_BAR is not None
        else None
    )
    negative = sum(1 for value in retained if value < 0) if retained else None
    smallest_in_sample = min(
        (
            w["in_sample"]["cagr"]
            for w in windows
            if w["edge_retained"] is not None and w["in_sample"]["cagr"] is not None
        ),
        default=None,
    )

    return {
        "windows": windows,
        "window_count": len(windows),
        "resolved_count": len(retained),
        "unresolved_count": len(windows) - len(retained),
        "mean_edge_retained": mean,
        "median_edge_retained": median,
        "windows_clearing_bar": clearing,
        "windows_negative": negative,
        "smallest_in_sample_cagr": smallest_in_sample,
        "distribution_note": (
            None
            if mean is None or median is None
            else (
                f"the mean is {mean:.2f} and the median {median:.2f}. "
                "`edge_retained` divides by in-sample CAGR, so a window that "
                "barely made money in sample produces an enormous ratio from an "
                "ordinary out-of-sample year, and an arithmetic mean is dominated "
                f"by it — the smallest in-sample CAGR here is "
                f"{smallest_in_sample:.2%}. Read the count clearing the bar and "
                "the count that went negative before the mean."
                if smallest_in_sample is not None
                else f"the mean is {mean:.2f} and the median {median:.2f}."
            )
        ),
        "mean_clears_bar": (
            None if mean is None or EDGE_RETAINED_BAR is None else mean >= EDGE_RETAINED_BAR
        ),
        "bar": EDGE_RETAINED_BAR,
        "bar_citation": EDGE_RETAINED_CITATION,
        "unpaired": sorted(unpaired),
        "note": (
            "edge_retained is out-of-sample CAGR over in-sample CAGR — a ratio "
            "rather than a difference, because subtracting total returns gives "
            "percentage points whose size depends on how long the spans were. "
            "Windows with no in-sample edge are unresolved and are skipped, not "
            "zeroed: a zero would read as the edge vanishing when there was none."
        ),
    }
