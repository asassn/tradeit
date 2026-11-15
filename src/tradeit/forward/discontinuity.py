"""Single-session price moves too large to be trading. **Report only.**

Why this exists
---------------

`docs/ELME_VENUE_BREAK_2026-11-15.md` measured what the capture cannot see. ELME
left the NYSE after 2026-10-23 and traded OTC as ``ELMES`` from 2026-10-26; the
vendor served both venues as one continuous series with a **-97.6% move at the
join and no field marking it**, the capture got ``ok`` sixteen times, and the
break was found three weeks later by a person asking a question. Nothing failed
and nothing was logged.

This module is the cheap part of closing that gap: it reads bars already in the
record and reports moves that trading cannot produce. It would have printed ELME
on 2026-10-26 instead of 2026-11-15.

**What it does not claim.** It does not detect splices. A single-session move far
larger than trading can explain is produced by a splice, a venue change, a
vendor re-mapping, a forward split, a reverse split and a share consolidation
alike, and **this cannot tell them apart.** A flag that named the cause would be
inventing a fact; the flag says only *"this move is not a trading move, and the
record does not explain it"*. That is a superset of the splice problem and it is
also exactly the §(3) limitation of `CLAUDE.md` -- *"splits no vendor recorded
are still not caught"* -- so firing on an unrecorded split is the detector
working, not noise.

**It never writes, never blocks and never overwrites.** It takes bars and returns
findings. Deciding what to *do* about a disagreeing bar needs the
which-vendor-wins decision that `_write_bars` deliberately defers, and this does
not pre-empt it. A detector that quarantined a bar would be taking that decision
by the back door.

The threshold is derived, not chosen
------------------------------------

Measured 2026-11-15 over the whole forward record -- 4,327 bars, 195
instruments, 4,132 consecutive session pairs. Ratios are direction-free
(``max(a/b, b/a)``), so a halving and a doubling both read as ``2``:

======================  =========
pair ratio              count
======================  =========
median                  1.014
99th percentile          1.143
99.99th percentile       1.490
**largest, excl. ELME**  **1.490**
ELME at the venue break  **42.243**
======================  =========

Two constraints fix the number, and **they do not conflict, so it is determined
rather than picked**:

1. It must sit **above every move trading produced** in the record. The largest
   was 1.490x (-32.9%, a penny stock), so the floor is 1.49.
2. It must sit **at or below the smallest ratio a corporate action produces**, or
   routine actions escape. The smallest in common use is 2:1, so the ceiling
   is 2.0.

``2`` is the only round number in ``[1.49, 2.0]`` and is the ceiling itself, so
it is the strictest value satisfying both. It sits 34% above the largest observed
trading move and a factor of 21 below the one known identity event. Every
candidate from 1.5 to 10 fires on exactly one pair in the current record, so the
choice inside that range costs nothing today and the binding constraint is (2).

**The re-measurement condition, stated so it is not quietly ignored.** 4,132
pairs over 17 sessions is a small sample and 1.490x is an observed maximum, not a
bound; a longer record will contain larger trading moves. If a genuine trading
move ever exceeds the threshold, **the answer is to re-measure and record the new
distribution, not to raise the bar until the flag goes quiet.** Raising a
detector's threshold to silence it is how a safeguard becomes decoration.

**Deliberately not a** :class:`~tradeit.readiness.criteria.ThresholdSource`.
Reusing that enum was the obvious move and is wrong: it exists to say whether a
threshold may authorise a promotion, and this one authorises nothing. Carrying it
here would invite wiring a report-only check into a readiness gate, which is the
one thing it must not become.

Out of scope, on purpose
------------------------

* **A price on zero volume.** ELME's last NYSE bar was 1.77 on volume 0
  (`RESEARCH_01_DATA_DICTIONARY.md` §0.7). Real, already a documented read rule,
  and a *different question* -- answering two questions from one function is how
  a report becomes unreadable.
* **Revisions.** Three of ELME's sixteen bars have since been revised by the
  vendor. Finding those needs re-asking an older session, which needs the
  which-vendor-wins decision. Not taken here.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.core.enums import Bartimeframe
from tradeit.storage.tables import CorporateAction, OhlcvBar

__all__ = [
    "DISCONTINUITY_RATIO",
    "DiscontinuityScan",
    "PriceDiscontinuity",
    "find_discontinuities",
    "scan_record",
]

#: See the module docstring for the derivation. Direction-free: a price that at
#: least doubles or at least halves between consecutive sessions.
DISCONTINUITY_RATIO = Decimal("2")


class BarLike(Protocol):
    """The three fields this needs from a bar.

    A protocol so ORM rows pass straight in and a test fixture is a two-line
    dataclass. Taking :class:`~tradeit.storage.tables.OhlcvBar` would make every
    test need a database, and a check nobody can test cheaply is a check that
    rots.
    """

    @property
    def instrument_id(self) -> int: ...
    @property
    def session_date(self) -> dt.date: ...
    @property
    def close(self) -> Decimal: ...


class ActionLike(Protocol):
    """The three fields needed to say an action *could* bear on a move."""

    @property
    def instrument_id(self) -> int: ...
    @property
    def action_type(self) -> str: ...
    @property
    def ex_date(self) -> dt.date: ...
    @property
    def ratio(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class PriceDiscontinuity:
    """One consecutive-session pair whose move trading cannot explain."""

    instrument_id: int
    previous_session: dt.date
    session: dt.date
    previous_close: Decimal
    close: Decimal
    #: Direction-free magnitude, always >= 1.
    ratio: Decimal
    #: Actions in the record dated on the later session for this instrument.
    #: **Candidate explanations, not verdicts.** A 2:1 split recorded beside a
    #: 42x move does not explain it, and deciding whether an action accounts for
    #: a move is a judgement this does not make. Empty means the record offers
    #: nothing -- which, while nothing writes actions to the forward record, is
    #: every flag, and the scan says so rather than letting "unexplained" read
    #: as a stronger claim than it is.
    candidate_actions: tuple[str, ...] = ()

    @property
    def direction(self) -> str:
        return "down" if self.close < self.previous_close else "up"

    def describe(self) -> str:
        pct = (self.close / self.previous_close - 1) * 100
        line = (
            f"instrument {self.instrument_id}: {self.previous_session} -> {self.session}  "
            f"{self.previous_close} -> {self.close}  "
            f"({self.ratio.quantize(Decimal('0.01'))}x {self.direction}, {pct:+.1f}%)"
        )
        if self.candidate_actions:
            return f"{line}\n    the record holds: {', '.join(self.candidate_actions)}"
        return line


@dataclass(frozen=True, slots=True)
class DiscontinuityScan:
    """What a scan looked at and what it found."""

    bars: int
    instruments: int
    pairs: int
    ratio: Decimal
    findings: tuple[PriceDiscontinuity, ...]
    #: Whether the record holds any corporate action at all. When it does not,
    #: **no finding here can be explained by one**, and a report that said
    #: "unexplained" without saying that would overstate itself.
    actions_available: bool = False

    def describe(self) -> str:
        lines = [
            f"discontinuity scan: {self.bars} bars, {self.instruments} instruments, "
            f"{self.pairs} session pairs, threshold {self.ratio}x"
        ]
        if not self.findings:
            lines.append("  no single-session move beyond the threshold")
            return "\n".join(lines)
        lines.append(
            f"  {len(self.findings)} move(s) too large to be trading. **A cause is not "
            "claimed**: a splice, a venue change, a vendor re-mapping or an "
            "unrecorded split all look like this."
        )
        for finding in self.findings:
            lines.append(f"  {finding.describe()}")
        if not self.actions_available:
            lines.append(
                "  the record holds no corporate action at all, so none of these could "
                "have been explained by one even if an action existed"
            )
        return "\n".join(lines)


def _ratio(previous: Decimal, close: Decimal) -> Decimal | None:
    """Direction-free magnitude of a move, or ``None`` if it has no meaning.

    A zero or negative close makes a ratio undefined or meaningless, and
    returning ``None`` rather than raising is the same choice
    :func:`~tradeit.forward.capture._row_session` makes: one unreadable bar must
    not take down a report that runs unattended.
    """
    try:
        if previous <= 0 or close <= 0:
            return None
        return max(close / previous, previous / close)
    except (InvalidOperation, ZeroDivisionError, TypeError):
        return None


def find_discontinuities(
    bars: Iterable[BarLike],
    *,
    ratio: Decimal = DISCONTINUITY_RATIO,
    actions: Iterable[ActionLike] = (),
) -> list[PriceDiscontinuity]:
    """Report every consecutive-session pair whose move is at least ``ratio``.

    Pure: reads the bars given, writes nothing, raises nothing on bad data.
    ``bars`` need not be sorted -- they are grouped per instrument and ordered
    here, because a caller that forgot an ``ORDER BY`` would otherwise get
    findings computed across a scrambled series, which is a wrong answer that
    looks like a right one.

    Consecutive means *consecutive in the record*, not calendar-adjacent. A gap
    where the security did not print is still one pair: a security that stops
    for a week and resumes a factor of ten lower is the exact shape being looked
    for, and requiring adjacent sessions would skip it.
    """
    by_instrument: dict[int, list[BarLike]] = {}
    for bar in bars:
        by_instrument.setdefault(bar.instrument_id, []).append(bar)

    actions_by_key: dict[tuple[int, dt.date], list[str]] = {}
    for action in actions:
        key = (action.instrument_id, action.ex_date)
        actions_by_key.setdefault(key, []).append(
            f"{action.action_type} ex {action.ex_date} ratio {action.ratio}"
        )

    found: list[PriceDiscontinuity] = []
    for instrument_id, series in sorted(by_instrument.items()):
        series.sort(key=lambda bar: bar.session_date)
        for earlier, later in itertools.pairwise(series):
            magnitude = _ratio(earlier.close, later.close)
            if magnitude is None or magnitude < ratio:
                continue
            found.append(
                PriceDiscontinuity(
                    instrument_id=instrument_id,
                    previous_session=earlier.session_date,
                    session=later.session_date,
                    previous_close=earlier.close,
                    close=later.close,
                    ratio=magnitude,
                    candidate_actions=tuple(
                        actions_by_key.get((instrument_id, later.session_date), ())
                    ),
                )
            )
    found.sort(key=lambda hit: (-hit.ratio, hit.instrument_id, hit.session))
    return found


def scan_record(
    session: Session,
    *,
    ratio: Decimal = DISCONTINUITY_RATIO,
    since: dt.date | None = None,
) -> DiscontinuityScan:
    """Scan daily bars in a record and report the moves trading cannot explain.

    ``since`` limits the *later* bar of a pair, so a nightly run can report only
    what is new without losing the pair that spans the boundary: the query reads
    from one session before ``since`` for exactly that reason. Omitted, it reads
    everything -- the full scan over the current record takes well under a
    second, and `ELME_VENUE_BREAK_2026-11-15.md` §5 says to re-run it whenever
    the record is relied on.
    """
    bars: Sequence[OhlcvBar] = (
        session.execute(
            select(OhlcvBar)
            .where(OhlcvBar.timeframe == str(Bartimeframe.D1))
            .order_by(OhlcvBar.instrument_id, OhlcvBar.session_date)
        )
        .scalars()
        .all()
    )
    actions: Sequence[CorporateAction] = session.execute(select(CorporateAction)).scalars().all()
    findings = find_discontinuities(bars, ratio=ratio, actions=actions)
    if since is not None:
        findings = [hit for hit in findings if hit.session >= since]
    return DiscontinuityScan(
        bars=len(bars),
        instruments=len({bar.instrument_id for bar in bars}),
        pairs=max(0, len(bars) - len({bar.instrument_id for bar in bars})),
        ratio=ratio,
        findings=tuple(findings),
        actions_available=bool(actions),
    )
