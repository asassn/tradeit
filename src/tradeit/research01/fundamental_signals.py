"""Fundamental signals as they could have been known on a given day.

Implements the definitions in ``docs/prereg/FUNDAMENTALS_2026-09-18.md``. Pure
functions over already-fetched facts: nothing here reads the database, so every
point-in-time rule can be tested exactly.

**The rule everything rests on: a value is what was FIRST filed.** A figure for
one period appears in its own filing and then again, as a comparative, in later
ones -- sometimes restated. ``first_filed`` keeps only the earliest row per
``(metric, period end, duration)``, and a value is usable on session ``D`` only
if that filing date is **strictly before** ``D``: ``knowledge_time`` is stamped at
midnight and a filing can arrive after the close.

**Fiscal Q4 is derived.** Companies report the fourth quarter only inside the
annual figure; the stand-alone Q4 value appears months later as a comparative
(Apple's FY2012 Q4: first printed stand-alone on 2013-04-24, six months after its
10-K). Q4 is the annual value minus the nine-month year-to-date, known on the
later of the two filing dates -- which on Apple FY2012 reproduces the late
stand-alone figure to the dollar, half a year earlier.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: A period is matched to "a year earlier" or "a quarter earlier" within this.
MATCH_DAYS = 20
#: Earnings drift follows an announcement; a quarter filed longer ago than this
#: is not a surprise any more.
SUE_FRESH_DAYS = 120
#: Seasonal differences before the latest quarter used for the scale, and the
#: fewest that may form one.
SUE_HISTORY = 8
SUE_MIN_HISTORY = 6
#: A company whose latest annual filing is older than this has stopped filing,
#: or filing annually, and carries no annual signal.
ANNUAL_FRESH_DAYS = 456  # fifteen months


@dataclass(frozen=True, slots=True)
class Fact:
    """One value as first filed."""

    metric: str
    period_end: dt.date
    #: Quarters the value spans: 0 for an instant (a balance), 1 a quarter,
    #: 3 a nine-month year-to-date, 4 a fiscal year.
    duration: int
    value: float
    #: The filing date that first reported it.
    known: dt.date


def first_filed(
    rows: Iterable[tuple[str, dt.date, int, float, dt.date]],
) -> list[Fact]:
    """Collapse raw rows to the earliest-filed value per metric, period and duration.

    ``rows`` are ``(metric, period_end, duration, value, filing_date)``. Where two
    rows share a period and a filing date, the first seen wins; the importer
    writes one row per filing, so that is a duplicate, not a disagreement.
    """
    earliest: dict[tuple[str, dt.date, int], Fact] = {}
    for metric, period_end, duration, value, known in rows:
        key = (metric, period_end, duration)
        current = earliest.get(key)
        if current is None or known < current.known:
            earliest[key] = Fact(metric, period_end, duration, float(value), known)
    return sorted(earliest.values(), key=lambda f: (f.metric, f.period_end, f.duration))


def _near(target: dt.date, candidates: Iterable[Fact]) -> Fact | None:
    """The candidate whose period ends closest to ``target``, within MATCH_DAYS."""
    best: Fact | None = None
    for fact in candidates:
        gap = abs((fact.period_end - target).days)
        if gap <= MATCH_DAYS and (best is None or gap < abs((best.period_end - target).days)):
            best = fact
    return best


class FundamentalHistory:
    """One security's first-filed facts, queried as of a session."""

    def __init__(self, facts: Sequence[Fact]) -> None:
        self._by: dict[tuple[str, int], list[Fact]] = {}
        for fact in facts:
            self._by.setdefault((fact.metric, fact.duration), []).append(fact)
        self._quarters = self._quarterly("NetIncomeLoss")

    def _facts(self, metric: str, duration: int, on: dt.date) -> list[Fact]:
        return [f for f in self._by.get((metric, duration), []) if f.known < on]

    # -- quarters ---------------------------------------------------------

    def _quarterly(self, metric: str) -> list[Fact]:
        """Stand-alone quarters, with fiscal Q4 derived where it can be.

        Where a period has both a stand-alone value and a derived one, the one
        known EARLIER is kept -- for a Q4 that is the derivation.
        """
        quarters: dict[dt.date, Fact] = {f.period_end: f for f in self._by.get((metric, 1), [])}
        nine_months = self._by.get((metric, 3), [])
        for annual in self._by.get((metric, 4), []):
            ytd = _near(annual.period_end - dt.timedelta(days=92), nine_months)
            if ytd is None:
                continue
            derived = Fact(
                metric,
                annual.period_end,
                1,
                annual.value - ytd.value,
                max(annual.known, ytd.known),
            )
            existing = quarters.get(annual.period_end)
            if existing is None or derived.known < existing.known:
                quarters[annual.period_end] = derived
        return sorted(quarters.values(), key=lambda f: f.period_end)

    def sue(self, on: dt.date) -> float | None:
        """Standardised unexpected earnings, seasonal random walk, as of ``on``."""
        known = [q for q in self._quarters if q.known < on]
        if not known:
            return None
        latest = known[-1]
        if (on - latest.known).days > SUE_FRESH_DAYS:
            return None

        def seasonal(quarter: Fact) -> float | None:
            prior = _near(
                quarter.period_end - dt.timedelta(days=365),
                (q for q in known if q.period_end < quarter.period_end),
            )
            return None if prior is None else quarter.value - prior.value

        change = seasonal(latest)
        if change is None:
            return None
        history = [d for q in known[-1 - SUE_HISTORY : -1] if (d := seasonal(q)) is not None]
        if len(history) < SUE_MIN_HISTORY:
            return None
        scale = statistics.stdev(history)
        if scale <= 0:
            return None
        return change / scale

    # -- fiscal years -----------------------------------------------------

    def _latest_annual(self, metric: str, on: dt.date) -> Fact | None:
        annuals = self._facts(metric, 4, on)
        if not annuals:
            return None
        latest = max(annuals, key=lambda f: f.period_end)
        if (on - latest.known).days > ANNUAL_FRESH_DAYS:
            return None
        return latest

    def _assets_at(self, period_end: dt.date, on: dt.date) -> float | None:
        fact = _near(period_end, self._facts("Assets", 0, on))
        return None if fact is None else fact.value

    def gross_profitability(self, on: dt.date) -> float | None:
        """Latest fiscal-year gross profit over total assets at that year end."""
        gross = self._latest_annual("GrossProfit", on)
        if gross is None:
            return None
        assets = self._assets_at(gross.period_end, on)
        if assets is None or assets <= 0:
            return None
        return gross.value / assets

    def asset_growth(self, on: dt.date) -> float | None:
        """Total assets at the latest fiscal-year end over a year earlier, minus one."""
        anchor = self._latest_annual("NetIncomeLoss", on)
        if anchor is None:
            return None
        now = self._assets_at(anchor.period_end, on)
        before = self._assets_at(anchor.period_end - dt.timedelta(days=365), on)
        if now is None or before is None or before <= 0:
            return None
        return now / before - 1.0

    def accruals(self, on: dt.date) -> float | None:
        """(Fiscal-year net income - operating cash flow) over average total assets."""
        income = self._latest_annual("NetIncomeLoss", on)
        if income is None:
            return None
        cash = _near(
            income.period_end, self._facts("NetCashProvidedByUsedInOperatingActivities", 4, on)
        )
        now = self._assets_at(income.period_end, on)
        before = self._assets_at(income.period_end - dt.timedelta(days=365), on)
        if cash is None or now is None or before is None or now + before <= 0:
            return None
        return (income.value - cash.value) / ((now + before) / 2.0)
