"""Walk-forward: measuring out-of-sample, and measuring the gap.

A single backtest over one period answers "did this work on this data?", which
is the question nobody needed answered. Walk-forward splits history into
in-sample stretches used for fitting or selection and out-of-sample stretches
used only for measurement, and it does so repeatedly so the out-of-sample
result is not itself one lucky draw.

**The reported metric is always out-of-sample**, which is why :meth:`run`
returns only those. But in-sample results are computed and kept, because the
*gap* between them is more informative than either number: a strategy that
earns 30% in-sample and 4% out is telling you something a strategy that earns
6% and 4% is not, even though the out-of-sample numbers are nearly the same.
:meth:`evaluate` returns both and the degradation between them.

Anchored and rolling answer different questions
-----------------------------------------------

**Anchored** grows the training window from a fixed origin: *does this still
work given everything known?* **Rolling** slides a fixed-width window: *does it
work given only the recent past?* A strategy that passes anchored and fails
rolling has probably been carried by an old regime that no longer exists. Both
are supported, and neither is the default -- the mode is a required field.

The embargo is not decoration
-----------------------------

``WalkForwardWindow`` already refuses an out-of-sample window that overlaps its
in-sample one. That is necessary and not sufficient: a study whose target looks
forward N sessions has, at the very last day of training, already observed
prices that fall inside the test window. ``gap_sessions`` drops that overlap.

The rule-based strategies this system runs today fit nothing, so the embargo
changes nothing for them and it would be easy to leave out. It is here because
the signal research Phase 9 also carries *does* fit, and an embargo retrofitted
after the first promising result is an embargo nobody will apply.

**Every window parameter is required.** Window widths determine how many
out-of-sample observations exist, which determines whether any of this means
anything, and a default would let that be chosen by accident.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass

from tradeit.backtesting.base import (
    BacktestEngine,
    BacktestResult,
    BacktestSpec,
    WalkForwardWindow,
)
from tradeit.backtesting.engine import SessionData

__all__ = ["ANCHORED", "ROLLING", "WalkForward", "WalkForwardReport", "WindowResult"]

#: Training grows from a fixed origin. "Given everything known, does it work?"
ANCHORED = "anchored"
#: Training slides at fixed width. "Given only the recent past, does it work?"
ROLLING = "rolling"


@dataclass(frozen=True, slots=True)
class WindowResult:
    """One split, measured on both sides of it."""

    window: WalkForwardWindow
    in_sample: BacktestResult
    out_of_sample: BacktestResult

    @property
    def degradation(self) -> float | None:
        """In-sample return minus out-of-sample return.

        ``None`` when either side produced no metrics. Positive means the
        strategy did better on the data it was chosen against, which is the
        expected direction; the question is how much. A *negative* degradation
        is not a triumph — it usually means the two periods were different
        regimes, and it should prompt a look at the windows rather than a
        celebration.
        """
        if self.in_sample.metrics is None or self.out_of_sample.metrics is None:
            return None
        return self.in_sample.metrics.total_return_pct - self.out_of_sample.metrics.total_return_pct


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    """Every split, with the out-of-sample record and the overfitting signal."""

    mode: str
    results: tuple[WindowResult, ...]

    @property
    def out_of_sample(self) -> tuple[BacktestResult, ...]:
        return tuple(result.out_of_sample for result in self.results)

    @property
    def mean_degradation(self) -> float | None:
        """Average in-sample advantage across windows.

        Averaged over windows rather than pooled over trades: a window with
        forty trades and one with four are equally informative about whether
        the strategy travels, and pooling would let the busy window decide.
        """
        gaps = [r.degradation for r in self.results if r.degradation is not None]
        if not gaps:
            return None
        return sum(gaps) / len(gaps)

    @property
    def out_of_sample_trade_count(self) -> int:
        return sum(len(result.out_of_sample.trades) for result in self.results)

    def consistency(self) -> float | None:
        """Share of windows whose out-of-sample return was positive.

        The number that distinguishes a strategy that works from one that had a
        very good year. A high average return with a consistency of 0.2 is
        three windows of nothing and one of luck.
        """
        returns = [
            r.out_of_sample.metrics.total_return_pct
            for r in self.results
            if r.out_of_sample.metrics is not None
        ]
        if not returns:
            return None
        return sum(1 for value in returns if value > 0) / len(returns)


@dataclass(frozen=True, slots=True)
class WalkForward:
    """Splits history, runs both sides of each split, reports the gap.

    ``engine`` must be configured for the same data ``data`` describes;
    the harness re-specs the date range and calls it once per side per window.
    """

    engine: BacktestEngine
    data: SessionData
    mode: str
    train_sessions: int
    test_sessions: int
    gap_sessions: int

    def __post_init__(self) -> None:
        if self.mode not in (ANCHORED, ROLLING):
            raise ValueError(f"mode must be {ANCHORED!r} or {ROLLING!r}, got {self.mode!r}")
        for name in ("train_sessions", "test_sessions"):
            if getattr(self, name) < 2:
                raise ValueError(f"{name} must be at least 2; a backtest needs two sessions")
        if self.gap_sessions < 0:
            raise ValueError("gap_sessions cannot be negative")

    def windows(self, spec: BacktestSpec) -> list[WalkForwardWindow]:
        """Every split the history supports, in chronological order.

        A history too short for even one complete split yields an empty list
        rather than one truncated window. A short final test period reports a
        number computed on different terms from its neighbours, and it would be
        read alongside them as though it were comparable.
        """
        sessions = list(self.data.sessions(spec.effective_start, spec.end))
        span = self.train_sessions + self.gap_sessions + self.test_sessions
        if len(sessions) < span:
            return []

        out: list[WalkForwardWindow] = []
        start = 0
        while start + span <= len(sessions):
            train_end_index = start + self.train_sessions - 1
            test_start_index = train_end_index + self.gap_sessions + 1
            test_end_index = test_start_index + self.test_sessions - 1
            out.append(
                WalkForwardWindow(
                    train_start=sessions[0 if self.mode == ANCHORED else start],
                    train_end=sessions[train_end_index],
                    test_start=sessions[test_start_index],
                    test_end=sessions[test_end_index],
                )
            )
            start += self.test_sessions
        return out

    def run(self, spec: BacktestSpec) -> list[BacktestResult]:
        """Out-of-sample results only. The in-sample ones are not the record."""
        return list(self.evaluate(spec).out_of_sample)

    def evaluate(self, spec: BacktestSpec) -> WalkForwardReport:
        """Both sides of every split, with the degradation between them."""
        results = tuple(
            WindowResult(
                window=window,
                in_sample=self._run_span(spec, window.train_start, window.train_end, "train"),
                out_of_sample=self._run_span(spec, window.test_start, window.test_end, "test"),
            )
            for window in self.windows(spec)
        )
        return WalkForwardReport(mode=self.mode, results=results)

    def _run_span(
        self, spec: BacktestSpec, start: dt.date, end: dt.date, label: str
    ) -> BacktestResult:
        """One side of one window, named so the two are never confused.

        The name carries the label because a report holding an in-sample and an
        out-of-sample result for the same period is one copy-paste away from
        quoting the wrong one.
        """
        scoped = dataclasses.replace(
            spec, name=f"{spec.name}/{label}/{start}", start=start, end=end
        )
        return self.engine.run(scoped)
