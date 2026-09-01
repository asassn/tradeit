"""Pulling a vendor archive down inside one billing month, resumably.

**The constraint this is shaped by.** The plan is to subscribe, take the
archive, and stop. If the run cannot survive an interruption, a laptop closing
on day three means starting again on day one -- and the month is the thing being
paid for. So progress is checkpointed per symbol, to a file, after every symbol.

**Nothing is bought to find out whether this works.** ``dry_run`` issues no
requests, writes no rows, and reports exactly the call budget a real run would
consume, so the plan can be sized and the wiring proved before a subscription
exists.

**A budget that is refused is not a failure.** Exhausting the daily allowance
stops the run and says so; it does not slow down, retry, or quietly continue
past a limit the vendor set.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from tradeit.research01.actions import import_corporate_actions
from tradeit.research01.eodhd_client import (
    EodhdClient,
    parse_bars,
    parse_dividends,
    parse_splits,
)
from tradeit.research01.importer import Delivery, ImportResult, import_price_bars

__all__ = ["BackfillPlan", "BackfillProgress", "BackfillReport", "run_backfill"]

#: Requests issued per symbol: EOD, splits, dividends. Used to size a plan
#: before it runs rather than to explain afterwards why it stopped.
CALLS_PER_SYMBOL = 3


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    symbols: tuple[str, ...]
    start: dt.date
    end: dt.date
    vendor: str = "eodhd"
    #: EODHD states 100,000 calls/day on commercial tiers. Kept as a parameter
    #: rather than a constant: it is the vendor's number, not ours, and a plan
    #: that hard-codes someone else's limit is wrong the day they change it.
    daily_call_budget: int = 100_000

    @property
    def estimated_calls(self) -> int:
        return len(self.symbols) * CALLS_PER_SYMBOL

    @property
    def fits_in_one_day(self) -> bool:
        return self.estimated_calls <= self.daily_call_budget


@dataclass(slots=True)
class BackfillProgress:
    """Checkpoint written after every symbol, so a stop costs one symbol.

    Deliberately a plain JSON file rather than a table: it records what *this
    process* has done, which is operational state, not a fact about securities.
    Putting it in the corpus would mix the two.
    """

    path: Path
    completed: set[str] = field(default_factory=set)
    calls_used: int = 0

    @classmethod
    def load(cls, path: Path) -> BackfillProgress:
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text())
        return cls(
            path=path,
            completed=set(raw.get("completed", [])),
            calls_used=int(raw.get("calls_used", 0)),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Written through a temp file: a checkpoint truncated by an interruption
        # is worse than none, because it would be read back as real progress.
        temporary = self.path.with_suffix(".part")
        temporary.write_text(
            json.dumps(
                {"completed": sorted(self.completed), "calls_used": self.calls_used}, indent=1
            )
        )
        temporary.replace(self.path)


@dataclass(slots=True)
class BackfillReport:
    planned: int = 0
    skipped_already_done: int = 0
    symbols_fetched: int = 0
    calls_issued: int = 0
    bars: ImportResult = field(default_factory=ImportResult)
    actions: ImportResult = field(default_factory=ImportResult)
    failures: list[tuple[str, str]] = field(default_factory=list)
    stopped_on_budget: bool = False

    def summary(self) -> dict[str, object]:
        return {
            "planned": self.planned,
            "skipped_already_done": self.skipped_already_done,
            "symbols_fetched": self.symbols_fetched,
            "calls_issued": self.calls_issued,
            "bars_landed": self.bars.landed,
            "bars_unresolved": len(self.bars.unresolved),
            "actions_landed": self.actions.landed,
            "actions_unresolved": len(self.actions.unresolved),
            "failures": len(self.failures),
            "stopped_on_budget": self.stopped_on_budget,
        }


def _merge(into: ImportResult, other: ImportResult) -> None:
    into.landed += other.landed
    into.rejected.extend(other.rejected)
    into.securities_touched |= other.securities_touched


def run_backfill(
    session: Session,
    client: EodhdClient,
    plan: BackfillPlan,
    progress: BackfillProgress,
    *,
    delivered_at: dt.datetime | None = None,
    dry_run: bool = False,
) -> BackfillReport:
    """Fetch and land each symbol, checkpointing as it goes.

    A symbol that raises is recorded and the run continues: one bad response
    should not cost the remaining symbols in a paid month. A symbol is marked
    complete only after its rows are landed, so an interruption mid-symbol
    re-fetches it rather than skipping it.
    """
    report = BackfillReport(planned=len(plan.symbols))
    delivery = Delivery(
        vendor=plan.vendor,
        delivered_at=delivered_at or dt.datetime.now(dt.UTC),
        filename=f"{plan.vendor}-backfill",
    )

    for symbol in plan.symbols:
        if symbol in progress.completed:
            report.skipped_already_done += 1
            continue
        if progress.calls_used + CALLS_PER_SYMBOL > plan.daily_call_budget:
            # Refused, not throttled. The vendor set this number.
            report.stopped_on_budget = True
            break
        if dry_run:
            report.calls_issued += CALLS_PER_SYMBOL
            report.symbols_fetched += 1
            continue

        try:
            bars = parse_bars(symbol, client.eod(symbol, plan.start, plan.end))
            actions = parse_splits(symbol, client.splits(symbol, plan.start, plan.end))
            actions += parse_dividends(symbol, client.dividends(symbol, plan.start, plan.end))
        except Exception as exc:
            report.failures.append((symbol, f"{type(exc).__name__}: {exc}"))
            continue

        progress.calls_used += CALLS_PER_SYMBOL
        report.calls_issued += CALLS_PER_SYMBOL
        _merge(report.bars, import_price_bars(session, bars, delivery))
        _merge(report.actions, import_corporate_actions(session, actions, delivery))
        report.symbols_fetched += 1
        progress.completed.add(symbol)
        progress.save()

    return report


def plan_for(
    symbols: Sequence[str], start: dt.date, end: dt.date, **kwargs: object
) -> BackfillPlan:
    return BackfillPlan(symbols=tuple(symbols), start=start, end=end, **kwargs)  # type: ignore[arg-type]
