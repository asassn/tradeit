"""Where a price series is missing sessions, and what shape the holes are.

``data.session_continuity`` reported *instrument 13 is missing 536 of 4,174
trading sessions*. That is a number nobody can act on, because the two
situations it covers want opposite responses:

* **one long contiguous hole** — the security stopped trading and started
  again, or the vendor's history is spliced from two listings, or there was a
  months-long halt. The series is two series, and treating it as one produces
  indicators computed across a boundary nobody could have traded through.
* **many short scattered holes** — thin liquidity, or a foreign listing on a
  calendar this project does not model. Unpleasant, usually harmless, and
  emphatically not the same finding.

536 missing sessions as a single 26-month block and 536 spread over sixteen
years are the same integer and different defects. So the check reports the
runs, not just the count.

Sessions before the first observed bar and after the last are **never**
counted. The expected count is taken over ``[first observed, last observed]``,
so a security that listed after the window opened or delisted before it closed
is measured over the period it actually traded. A delisting is not a gap.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "STRUCTURAL_RUN_SESSIONS",
    "GapRun",
    "GapShape",
    "SeriesContinuity",
    "analyse_series",
]

#: A run at least this long stops being "a few missing prints" and starts being
#: a structural break: roughly a trading month. Chosen because it is long enough
#: that no ordinary holiday or halt reaches it, and short enough to catch a
#: quarter-long suspension.
STRUCTURAL_RUN_SESSIONS = 21


class GapShape(StrEnum):
    """What the holes look like, which is what decides the response."""

    #: Nothing missing.
    COMPLETE = "complete"
    #: Many short absences — thin liquidity or an unmodelled calendar.
    SCATTERED = "scattered_absences"
    #: One long absence — a relisting, a splice, or a suspension.
    STRUCTURAL = "structural_break"
    #: Both, in quantity.
    MIXED = "mixed"

    @property
    def breaks_the_series(self) -> bool:
        """Whether indicators may legitimately be computed straight through.

        A structural break means the bars either side belong to different
        trading histories; a rolling window spanning one produces a value no
        participant could have seen.
        """
        return self in (GapShape.STRUCTURAL, GapShape.MIXED)


@dataclass(frozen=True, slots=True)
class GapRun:
    """A maximal run of consecutive expected sessions with no bar."""

    start: dt.date
    end: dt.date
    sessions: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "sessions": self.sessions,
        }

    def __str__(self) -> str:
        if self.sessions == 1:
            return f"{self.start.isoformat()} (1 session)"
        return f"{self.start.isoformat()}..{self.end.isoformat()} ({self.sessions} sessions)"


@dataclass(frozen=True, slots=True)
class SeriesContinuity:
    """One instrument's coverage over the period it actually traded."""

    instrument_id: int
    ticker: str
    first_bar: dt.date
    last_bar: dt.date
    expected_sessions: int
    observed_bars: int
    runs: tuple[GapRun, ...] = ()

    @property
    def missing_sessions(self) -> int:
        return sum(run.sessions for run in self.runs)

    @property
    def missing_ratio(self) -> float:
        if not self.expected_sessions:
            return 0.0
        return self.missing_sessions / self.expected_sessions

    @property
    def longest_run(self) -> GapRun | None:
        return max(self.runs, key=lambda r: r.sessions) if self.runs else None

    @property
    def shape(self) -> GapShape:
        if not self.runs:
            return GapShape.COMPLETE
        structural = [r for r in self.runs if r.sessions >= STRUCTURAL_RUN_SESSIONS]
        if not structural:
            return GapShape.SCATTERED
        # A single long hole with a handful of one-day absences either side is
        # still one structural break; call it mixed only when the scattered
        # part is itself substantial.
        scattered = self.missing_sessions - sum(r.sessions for r in structural)
        if scattered > self.missing_sessions * 0.2:
            return GapShape.MIXED
        return GapShape.STRUCTURAL

    @property
    def diagnosis(self) -> str:
        longest = self.longest_run
        if self.shape is GapShape.COMPLETE:
            return "every expected session has a bar"
        if self.shape is GapShape.SCATTERED:
            return (
                f"{len(self.runs)} separate absences, longest "
                f"{longest.sessions if longest else 0} session(s) — thin liquidity or a "
                "listing on a calendar this project does not model"
            )
        assert longest is not None
        return (
            f"one absence of {longest.sessions} consecutive sessions "
            f"({longest.start.isoformat()}..{longest.end.isoformat()}) — a suspension, a "
            "relisting, or a history spliced from two listings. Indicators computed "
            "across it read a series nobody could have traded."
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "first_bar": self.first_bar.isoformat(),
            "last_bar": self.last_bar.isoformat(),
            "expected_sessions": self.expected_sessions,
            "observed_bars": self.observed_bars,
            "missing_sessions": self.missing_sessions,
            "missing_ratio": round(self.missing_ratio, 6),
            "shape": str(self.shape),
            "gap_runs": [run.to_payload() for run in self.runs],
            "diagnosis": self.diagnosis,
        }

    def render(self) -> list[str]:
        lines = [
            f"  instrument {self.instrument_id} ({self.ticker or 'unmapped'})",
            f"    observed        {self.first_bar.isoformat()} .. {self.last_bar.isoformat()}"
            f"  ({self.observed_bars:,} bars)",
            f"    expected        {self.expected_sessions:,} trading sessions in that interval",
            f"    missing         {self.missing_sessions:,} ({self.missing_ratio:.2%})"
            f" in {len(self.runs)} run(s)",
            f"    shape           {self.shape}",
            f"    reading         {self.diagnosis}",
        ]
        if self.runs:
            ordered = sorted(self.runs, key=lambda r: -r.sessions)[:10]
            lines.append("    longest runs    " + "; ".join(str(run) for run in ordered))
            if len(self.runs) > len(ordered):
                lines.append(f"                    ... and {len(self.runs) - len(ordered)} more")
        return lines


def analyse_series(
    instrument_id: int,
    ticker: str,
    observed: Sequence[dt.date],
    expected: Sequence[dt.date],
) -> SeriesContinuity:
    """Group the missing sessions into maximal contiguous runs.

    ``expected`` must be the exchange calendar's sessions over
    ``[min(observed), max(observed)]`` — bounded by what was observed, so that
    a listing after the window opened or a delisting before it closed cannot
    register as missing data. Passing a wider range would reintroduce exactly
    that error.
    """
    if not observed:
        raise ValueError("analyse_series needs at least one observed session")
    have = set(observed)
    runs: list[GapRun] = []
    start: dt.date | None = None
    previous: dt.date | None = None
    length = 0
    for day in expected:
        if day in have:
            if start is not None and previous is not None:
                runs.append(GapRun(start=start, end=previous, sessions=length))
            start, previous, length = None, None, 0
            continue
        if start is None:
            start = day
        previous = day
        length += 1
    if start is not None and previous is not None:
        runs.append(GapRun(start=start, end=previous, sessions=length))

    return SeriesContinuity(
        instrument_id=instrument_id,
        ticker=ticker,
        first_bar=min(observed),
        last_bar=max(observed),
        expected_sessions=len(expected),
        observed_bars=len(observed),
        runs=tuple(runs),
    )
