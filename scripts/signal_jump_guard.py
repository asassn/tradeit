#!/usr/bin/env python
"""Exclude observations whose outcome window crosses a §0.10 discontinuity.

The defect and its measurement are in ``RESEARCH_01_DATA_DICTIONARY.md`` §0.10.
A return computed across one of those sessions is not a return, so a study drops
the observation rather than the security: the rest of that security's history is
sound, and dropping the whole name would trade one bias for another.

The outcome session is resolved on the **exchange calendar**, not by adding
calendar days, because a study's horizon is counted in sessions and a
seven-day approximation of five sessions would include or miss a flagged day
depending on the weekday.
"""

from __future__ import annotations

import csv
import datetime as dt
from bisect import bisect_right
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from tradeit.core.calendar import get_calendar


def load_jumps(paths: Sequence[str | Path]) -> dict[int, list[dt.date]]:
    """``security_id -> sorted flagged sessions``, from the scan's CSVs."""
    found: dict[int, list[dt.date]] = {}
    for path in paths:
        with Path(path).open() as handle:
            for row in csv.DictReader(handle):
                found.setdefault(int(row["security_id"]), []).append(
                    dt.date.fromisoformat(row["session_date"])
                )
    for days in found.values():
        days.sort()
    return found


def spans_jump(
    security_ids: np.ndarray,
    session_dates: np.ndarray,
    horizon: int,
    jumps: dict[int, list[dt.date]],
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> np.ndarray:
    """True where the window from a signal session to its outcome holds one.

    The window is exclusive of the signal session and inclusive of the outcome
    session: a discontinuity ON the signal bar has already been priced into the
    entry, while one on any later session through the outcome corrupts the
    return being measured.
    """
    if not jumps:
        return np.zeros(session_dates.shape[0], dtype=bool)
    days = [dt.date.fromisoformat(str(d)) if isinstance(d, str) else d for d in session_dates]
    sessions = get_calendar().sessions_between(
        start or min(days), end or (max(days) + dt.timedelta(days=horizon * 3 + 30))
    )
    place = {day: index for index, day in enumerate(sessions)}
    out = np.zeros(len(days), dtype=bool)
    for row, (security_id, day) in enumerate(zip(security_ids, days, strict=True)):
        flagged = jumps.get(int(security_id))
        if not flagged:
            continue
        index = place.get(day)
        if index is None:
            continue
        last = sessions[min(index + horizon, len(sessions) - 1)]
        # The first flagged session strictly after the signal bar; the window
        # holds one when that session is at or before the outcome bar.
        nxt = bisect_right(flagged, day)
        out[row] = nxt < len(flagged) and flagged[nxt] <= last
    return out
