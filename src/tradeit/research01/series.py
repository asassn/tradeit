"""Reconstructing a price series as it could have been known on a given day.

**The gap this closes.** ``pit.py`` establishes that a vendor-delivered adjusted
series carries the vendor's adjustment epoch: AAPL's raw close of 500.04 on
2020-08-27 arrives as ``adjusted_close`` 121.15 because of a split four days
later. Such a series cannot be used for historical work, and the module says the
only valid route is to derive our own from raw bars plus the actions known at
the instant being asked about. **That derivation did not exist**, so until now
the corpus offered a choice between raw bars that jump at every split and
adjusted bars that contain the future.

**How an adjustment is point-in-time.** A split on ex-date *D* makes every price
before *D* incomparable with prices after it, so the earlier ones are divided by
the ratio. Which splits apply depends on **when you are standing**:

* standing *after* the split, looking back — it applies;
* standing *before* it — it does not exist yet, and applying it would put the
  future into the past.

So the factor for a bar is the product of every split whose ex-date is after
that bar **and** whose ``knowledge_time`` is at or before ``as_of``. Both
conditions, always. Dropping the second is the classic way a backtest quietly
learns tomorrow's corporate actions.

**Only ``raw`` bars are read.** A ``total`` bar is the vendor's own adjustment
and is stamped at delivery; feeding one through this would adjust an already
adjusted number twice, with the vendor's epoch still inside it.
"""

from __future__ import annotations

import datetime as dt
import math
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.core.calendar import TradingCalendar
from tradeit.storage.tables import (
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SecuritySplitPriceVerdict,
    SymbolAlias,
)

__all__ = [
    "AdjustedBar",
    "Coherence",
    "SplitAdjustment",
    "SplitEvidence",
    "adjudicated_bound",
    "adjudicated_window",
    "admit_prints",
    "known_splits",
    "price_series",
    "series_coherence",
    "split_evidence",
    "split_reading",
]

#: Actions that change the share count and therefore the comparability of a
#: price. Dividends do not: they change total return, which is a different
#: series with its own basis, and mixing the two silently produces neither.
_SPLIT_TYPES = frozenset({"split", "reverse_split"})


@dataclass(frozen=True, slots=True)
class SplitAdjustment:
    """One split that was knowable at the as-of instant."""

    ex_date: dt.date
    ratio: Decimal
    knowledge_time: dt.datetime


class VolumeBasis(StrEnum):
    """Which share count a security's stored ``raw`` **volume** is counted in.

    ``RESEARCH_01_DATA_DICTIONARY.md`` §0.9. The stored close is usually the
    print, but the stored volume is usually already restated for every split the
    vendor knew of on delivery -- AAPL's 2010 volume carries its 2014 7:1 **and**
    its 2020 4:1, twenty-eight times the shares that traded. Measured across every
    recorded split: EODHD volume runs straight through the ex-date at 4,706 and
    steps with it at 797. Not uniform within a vendor, so it is established per
    security and source, from that security's own splits.

    **Why per security, not per split.** A vendor that restates volume restates
    it for every split at once -- it is one cumulative factor -- so a security's
    splits are several readings of one fact. Pooling them is what lets a noisy
    2-for-1, whose expected step is small against ordinary swings in activity,
    be decided by the security's other splits rather than on its own.

    The stored ``volume_adjusted`` column cannot answer this: every one of the
    corpus's 41 million raw rows carries ``False``, written by the importers as
    a default, not measured.
    """

    #: Volume steps with each split: it is the shares that traded.
    RAW = "raw"
    #: Volume runs through each split: it is already restated, for every
    #: recorded split, including ones after the read's as-of.
    ADJUSTED = "adjusted"
    #: The security has splits and its prints cannot say which. Served as
    #: before this rule existed, and marked, so that anything resting on a
    #: volume *level* -- a liquidity floor -- can refuse it.
    UNDETERMINED = "undetermined"
    #: No recorded split, so the question does not arise.
    NOT_NEEDED = "not_needed"


#: Sessions of volume read either side of an ex-date, and the fewest per side.
VOLUME_TEST_SESSIONS = 20
VOLUME_MIN_SESSIONS = 10
#: Splits smaller than this are not read: a 5-for-4's expected volume step is
#: smaller than an ordinary month's change in how much a stock trades.
VOLUME_TEST_MIN_RATIO = 1.5
#: Pooled step, as a fraction of the pooled split size, at or above which the
#: volume is the print, and at or below which it is already restated.
VOLUME_RAW_AT_LEAST = 0.6
VOLUME_ADJUSTED_AT_MOST = 0.4


#: One raw bar as the read paths carry it: session, open, high, low, close, volume.
PrintRow = tuple[dt.date, Decimal, Decimal, Decimal, Decimal, Decimal]


def admit_prints(
    rows: Sequence[PrintRow], split_dates: Iterable[dt.date]
) -> tuple[list[PrintRow], int]:
    """Keep the bars that are prices; refuse the ones asserting a price nobody traded.

    ``rows`` must be one security's bars in session order. Returns the admitted
    bars and how many were refused.

    **The rule.** A bar with volume is a print. A bar with **no** volume is
    admitted only when it is an exact flat copy -- ``open = high = low = close``
    -- of the last close that actually traded. Everything else with no volume is
    refused: a sentinel, a spike, a new price, an intraday range nobody dealt in.

    **Why not simply refuse every zero-volume bar**, which is the obvious rule and
    was measured before being rejected. Of 2,237,807 positive-priced raw bars
    with no volume, **1,935,200 (86.5%) are flat copies of the previous close** --
    a quiet day on a thin stock, the vendor carrying the last price forward. And
    of 640,333 runs of consecutive zero-volume bars, **637,214 are followed by
    trading again**; 23,136 of those last longer than ten sessions. The
    backtester retires a holding after a stretch of *silence*, so refusing them
    would turn every one of those live, quiet stocks into a false delisting. A
    carried close adds nothing and moves no mark, so it is kept.

    **What is refused, and why that is the harm.** The remainder assert a price
    with no trade behind it: 109,063 flat bars at a *new* price, 185,879 with an
    intraday range, 11,674 jumping more than threefold, 3,792 in the
    spike-and-back shape of security 4565 (``0.0001``, ``92000``, ``0.0001``, all on
    zero volume). Those are what put a +8,511,217% mean return into a real study,
    and in a backtest they are marks and stop triggers at prices nobody quoted.

    **There is no threshold in this rule**, deliberately. Equality with the last
    traded close is a fact about the bar; "within 3x of it" would be a parameter,
    and a parameter here would decide which vendor errors count as prices.

    **The anchor resets at a split.** A carried *raw* close on or after an
    ex-date is the pre-split price repeated into a post-split session -- a
    two-for-one would mark the holding at double its value. So after a split the
    first admitted bar must have traded.
    """
    splits = sorted(set(split_dates))
    kept: list[PrintRow] = []
    refused = 0
    anchor: Decimal | None = None
    anchor_day: dt.date | None = None
    previous: dt.date | None = None
    for row in rows:
        day, open_, high, low, close, volume = row
        if previous is not None and day <= previous:
            raise ValueError(f"bars must be in session order: {day} follows {previous}")
        previous = day
        if volume > 0:
            kept.append(row)
            anchor, anchor_day = close, day
            continue
        if anchor_day is not None:
            first_split_after = bisect_right(splits, anchor_day)
            if first_split_after < len(splits) and splits[first_split_after] <= day:
                anchor = anchor_day = None
        if anchor is not None and open_ == high == low == close == anchor:
            kept.append(row)
        else:
            refused += 1
    return kept, refused


@dataclass(frozen=True, slots=True)
class AdjustedBar:
    """A bar restated for splits known at the as-of instant.

    ``split_factor`` is carried rather than discarded so a reader can recover
    the raw print, and so an adjustment can be checked instead of trusted.
    """

    session_date: dt.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    split_factor: Decimal
    raw_close: Decimal
    #: How ``volume`` was put on this bar's price basis. ``UNDETERMINED`` means
    #: it was not: the stored number, scaled as the read did before §0.9 was
    #: found. A caller resting anything on a volume *level* must refuse it.
    volume_basis: VolumeBasis = VolumeBasis.NOT_NEEDED

    @property
    def is_adjusted(self) -> bool:
        return self.split_factor != 1


def adjudicated_window(session: Session, security_id: int) -> tuple[dt.date | None, dt.date | None]:
    """The interval this security's ticker is evidenced to have meant it.

    **Both ends, because a reused ticker has two neighbours.** The end keeps out
    whoever took the symbol next; the start keeps out whoever held it before,
    which the corpus learned the expensive way -- ``AAAB`` arrived carrying bars
    from 1999 to 2003 under a registrant that did not exist until 2011.

    Half-open ``[valid_from, valid_to)``, matching ``resolve_security``. The
    widest ``valid_from`` and the earliest ``valid_to`` win where a security
    carries several aliases: each is an independent claim about when the symbol
    meant this security, and the safe reading of two is the narrower one.
    """
    row = session.execute(
        select(func.max(SymbolAlias.valid_from), func.min(SymbolAlias.valid_to)).where(
            SymbolAlias.security_id == security_id,
            SymbolAlias.alias_kind == "ticker",
        )
    ).one_or_none()
    if row is None:
        return None, None
    return row[0], row[1]


def adjudicated_bound(session: Session, security_id: int) -> dt.date | None:
    """The date at which this security's ticker stops meaning it. **Exclusive.**

    Half-open ``[valid_from, valid_to)``, which is how every interval table in
    this schema declares itself -- ``ck_alias_interval`` and its four siblings
    all require ``valid_to > valid_from``, and ``resolve_security``, which
    decides what may enter the corpus at all, tests ``valid_to > on``. An
    earlier version of this function read the same column as inclusive, so a
    bar on the boundary date was admitted here and rejected there. One column,
    one meaning.

    ``None`` when no interval has been closed, which is the ordinary case. A
    closed interval is written only by ``adjudicate.py`` and only where evidence
    located a boundary, so this reads a **recorded, cited fact** rather than
    recomputing a heuristic at query time.

    The earliest close wins where a security carries several ticker aliases:
    each is an independent claim about when the symbol stopped meaning this
    security, and the safe reading of two is the earlier one.
    """
    return session.scalar(
        select(func.min(SymbolAlias.valid_to)).where(
            SymbolAlias.security_id == security_id,
            SymbolAlias.alias_kind == "ticker",
            SymbolAlias.valid_to.is_not(None),
        )
    )


class SplitEvidence(StrEnum):
    """Whether a recorded split is already inside the stored ``raw`` prints.

    **Why this exists.** Both read paths adjust ``raw`` by the recorded splits:
    ``price_series`` divides earlier prints, and ``CorpusSessionData`` changes a
    holding's share count on the ex-date. That is right only if ``raw`` is the
    print, and for some EODHD rows it is not -- see
    ``RESEARCH_01_DATA_DICTIONARY.md`` §0.1a.

    **The test, and why it is two tests.** Around the ex-date each basis is
    judged on its own, from the median of up to five closes either side: it
    *jumps* if its level moves by about ``1 / ratio``, is *flat* if it barely
    moves, and is *other* otherwise. A first version compared ``raw`` against
    ``total`` in one ratio and assumed ``total`` was always adjusted. It is not:
    BNCN's EODHD ``total`` jumps at its 2005 5-for-4 while its ``raw`` is flat,
    and that one-ratio test read an already-adjusted ``raw`` as a genuine print.

    **Which cells act, and why only these.** Measured 2026-09-15 against
    Sharadar's printed close, whose ``closeunadj`` steps at every split checked,
    on up to forty splits per cell where Sharadar could decide:

    ============================  ========  =========================
    raw / total                   splits    Sharadar says
    ============================  ========  =========================
    jumps / flat                  7,282     36 of 36 the print
    flat / flat                     667     33 of 33 already adjusted
    flat / other                    772     9 print, 23 adjusted
    jumps / jumps                    63     14 print, 2 adjusted
    jumps / other, and raw other  1,000+    mixed
    ============================  ========  =========================

    Only the two cells that were right every time act. Every mixed cell is
    :attr:`CONTRADICTED`: acting on a 72% cell is a wrong adjustment at 28% of
    its splits, and the corpus prefers a withheld print to a wrong one.

    **It reads bars regardless of their knowledge_time**, deliberately. The
    question is whether a vendor's stored number is the print, not what the
    market knew, and both bases are read on the same sessions either side.
    """

    #: ``raw`` jumps and ``total`` is flat: the print. Adjust it.
    IN_RAW = "in_raw"
    #: Both flat: ``raw`` is already adjusted. **Do not adjust again.**
    ALREADY_ADJUSTED = "already_adjusted"
    #: Any other shape. The prints disagree with each other or with the recorded
    #: ratio, so no factor can be stated and prints before it are withheld.
    CONTRADICTED = "contradicted"
    #: Fewer than :data:`SPLIT_TEST_MIN_SESSIONS` closes of either basis on a
    #: side. Applied as recorded; a read cannot span a gap like that anyway.
    NO_EVIDENCE = "no_evidence"
    #: Under :data:`SPLIT_TEST_MIN_RATIO` from 1, where an ordinary week's move
    #: is as large as the split. Applied as recorded; the error either way is
    #: under five percent.
    TOO_SMALL = "too_small"


#: Splits closer to 1 than this are not tested -- see :attr:`SplitEvidence.TOO_SMALL`.
SPLIT_TEST_MIN_RATIO = 0.05
#: How far a basis's level may sit from the expected step, as a share of the
#: split's own log size, and still be read as jumping or as flat.
SPLIT_TEST_TOLERANCE = 0.35
#: Closes read either side of the ex-date, and the fewest that make a level.
SPLIT_TEST_SESSIONS = 5
SPLIT_TEST_MIN_SESSIONS = 3
_APPLIED = frozenset({SplitEvidence.IN_RAW, SplitEvidence.NO_EVIDENCE, SplitEvidence.TOO_SMALL})


def _level(
    session: Session, security_id: int, ex_date: dt.date, basis: str, *, before: bool
) -> float | None:
    fact = SecurityPriceFact
    date_rule = fact.session_date < ex_date if before else fact.session_date >= ex_date
    closes = session.scalars(
        select(fact.close)
        .where(
            fact.security_id == security_id,
            fact.adjustment_basis == basis,
            fact.close > 0,
            date_rule,
        )
        .order_by(fact.session_date.desc() if before else fact.session_date)
        .limit(SPLIT_TEST_SESSIONS)
    ).all()
    if len(closes) < SPLIT_TEST_MIN_SESSIONS:
        return None
    ordered = sorted(float(c) for c in closes)
    return ordered[len(ordered) // 2]


def _shape(before: float, after: float, ratio: float) -> str:
    size = abs(math.log(ratio))
    step = math.log(after / before)
    if abs(step + math.log(ratio)) < SPLIT_TEST_TOLERANCE * size:
        return "jumps"
    if abs(step) < SPLIT_TEST_TOLERANCE * size:
        return "flat"
    return "other"


def recorded_verdict(session: Session, security_id: int, ex_date: dt.date) -> SplitEvidence | None:
    """A second vendor's answer for this split, latest revision, or ``None``.

    Written by ``scripts/research01_arbitrate_splits.py`` from a vendor's
    **printed** close, with the three closes it rests on stored beside it. Read
    only where the corpus's own prints cannot decide -- see :func:`split_evidence`.
    """
    row = session.execute(
        select(SecuritySplitPriceVerdict.verdict)
        .where(
            SecuritySplitPriceVerdict.security_id == security_id,
            SecuritySplitPriceVerdict.ex_date == ex_date,
        )
        .order_by(SecuritySplitPriceVerdict.knowledge_time.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return SplitEvidence.IN_RAW if row[0] == "in_raw" else SplitEvidence.ALREADY_ADJUSTED


def split_evidence(
    session: Session, security_id: int, ex_date: dt.date, ratio: Decimal
) -> SplitEvidence:
    """Classify one recorded split against the stored prints. See :class:`SplitEvidence`.

    **A recorded verdict is consulted only where the prints contradict**, not
    ahead of them. The two acting shapes were right at 69 of 69 sampled splits
    against a second vendor, so preferring a stored row there would add a query
    per split to every read and change nothing. Where the prints cannot decide,
    the recorded row is the only evidence there is.
    """
    value = float(ratio)
    if abs(math.log(value)) < math.log(1 + SPLIT_TEST_MIN_RATIO):
        return SplitEvidence.TOO_SMALL
    levels = [
        _level(session, security_id, ex_date, basis, before=side)
        for basis in ("raw", "total")
        for side in (True, False)
    ]
    if any(level is None for level in levels):
        return SplitEvidence.NO_EVIDENCE
    raw_before, raw_after, total_before, total_after = (float(x) for x in levels)  # type: ignore[arg-type]
    raw, total = _shape(raw_before, raw_after, value), _shape(total_before, total_after, value)
    if raw == "jumps" and total == "flat":
        return SplitEvidence.IN_RAW
    if raw == "flat" and total == "flat":
        return SplitEvidence.ALREADY_ADJUSTED
    return recorded_verdict(session, security_id, ex_date) or SplitEvidence.CONTRADICTED


def split_reading(
    session: Session, security_id: int, *, as_of: dt.datetime
) -> tuple[list[SplitAdjustment], dt.date | None]:
    """The splits a read should apply, and the date before which it must not read.

    Returns ``(applied, floor)``. ``applied`` omits splits already inside ``raw``
    and contradicted ones. ``floor`` is the latest contradicted ex-date, or
    ``None``: prints **before** it are withheld, because they would need a split
    factor nobody can state. Withheld before rather than after, so a series keeps
    its ending -- which is where a survivorship study looks.
    """
    applied, _baked, floor = classified_splits(session, security_id, as_of=as_of)
    return applied, floor


def classified_splits(
    session: Session, security_id: int, *, as_of: dt.datetime
) -> tuple[list[SplitAdjustment], list[SplitAdjustment], dt.date | None]:
    """``(applied, baked, floor)``: :func:`split_reading` plus the splits already
    inside the stored prices.

    ``baked`` is what :func:`split_reading` discards -- splits whose evidence is
    :attr:`SplitEvidence.ALREADY_ADJUSTED`. A price read does not need them,
    because they are already in the number. A **volume** read does: a close the
    vendor restated for a split is on the post-split share count, and a volume
    served beside it must be counted in the same shares or their product is not
    the money that traded. Classified once, here, so no read pays for the
    evidence twice.
    """
    applied: list[SplitAdjustment] = []
    baked: list[SplitAdjustment] = []
    floor: dt.date | None = None
    for split in known_splits(session, security_id, as_of=as_of, include_disputed=True):
        if not _within_adjudication(session, security_id, split.ex_date):
            continue
        evidence = split_evidence(session, security_id, split.ex_date, split.ratio)
        if evidence in _APPLIED:
            applied.append(split)
        elif evidence is SplitEvidence.ALREADY_ADJUSTED:
            baked.append(split)
        elif evidence is SplitEvidence.CONTRADICTED:
            floor = split.ex_date if floor is None else max(floor, split.ex_date)
    return applied, baked, floor


#: Far enough ahead that every recorded split is knowable. Used only to ask what
#: the vendor had folded into a stored number -- see :func:`recorded_splits`.
_EVERYTHING = dt.datetime(9999, 12, 31, tzinfo=dt.UTC)


def recorded_splits(session: Session, security_id: int) -> list[SplitAdjustment]:
    """Every recorded split inside the adjudicated interval, **whatever its
    knowledge_time**.

    This is not a look-ahead, and the distinction matters. A vendor that restated
    volume did so for every split it knew on delivery, so a 2010 volume in this
    corpus already contains the 2020 split. Knowing that split is the only way to
    take it back **out**. Using it to undo information the stored number should
    never have held is a repair; using it to inform a signal would be the leak.
    """
    return [
        split
        for split in known_splits(session, security_id, as_of=_EVERYTHING, include_disputed=True)
        if _within_adjudication(session, security_id, split.ex_date)
    ]


def _volume_levels(
    session: Session, security_id: int, ex_date: dt.date, *, before: bool
) -> dict[str, tuple[float, int]]:
    """Median traded volume on one side of an ex-date, per source, with its count."""
    fact = SecurityPriceFact
    date_rule = fact.session_date < ex_date if before else fact.session_date >= ex_date
    rows = session.execute(
        select(fact.source, fact.volume)
        .where(
            fact.security_id == security_id,
            fact.adjustment_basis == "raw",
            fact.volume > 0,
            fact.close > 0,
            date_rule,
        )
        .order_by(fact.session_date.desc() if before else fact.session_date)
        .limit(VOLUME_TEST_SESSIONS)
    ).all()
    by_source: dict[str, list[float]] = {}
    for source, volume in rows:
        by_source.setdefault(source, []).append(float(volume))
    out: dict[str, tuple[float, int]] = {}
    for source, values in by_source.items():
        values.sort()
        out[source] = (values[len(values) // 2], len(values))
    return out


def volume_basis(
    session: Session,
    security_id: int,
    splits: Sequence[SplitAdjustment] | None = None,
) -> dict[str, VolumeBasis]:
    """The stored volume's basis for each source, from this security's splits.

    Only sources with evidence appear. A caller asking about a source that is
    absent, on a security that has splits, should read ``UNDETERMINED`` -- see
    :func:`basis_of`.

    **The test.** Either side of each split of at least
    :data:`VOLUME_TEST_MIN_RATIO`, the median traded volume over up to
    :data:`VOLUME_TEST_SESSIONS` sessions, per source, from one source on both
    sides so two vendors' bases are never compared with each other. The log of
    the step, as a fraction of the log of the split, is ~1 if volume is the print
    and ~0 if it is already restated. Pooled across the security's splits,
    weighted by each split's size, so a 7-for-1 counts for more than a 2-for-1 --
    the larger step is the one ordinary activity cannot fake.
    """
    usable = [
        split
        for split in (recorded_splits(session, security_id) if splits is None else splits)
        if abs(math.log(float(split.ratio))) >= math.log(VOLUME_TEST_MIN_RATIO)
    ]
    weighted: dict[str, float] = {}
    size: dict[str, float] = {}
    for split in usable:
        log_ratio = math.log(float(split.ratio))
        before = _volume_levels(session, security_id, split.ex_date, before=True)
        after = _volume_levels(session, security_id, split.ex_date, before=False)
        for source in before.keys() & after.keys():
            (low, n_low), (high, n_high) = before[source], after[source]
            if min(n_low, n_high) < VOLUME_MIN_SESSIONS or low <= 0 or high <= 0:
                continue
            # step / log_ratio, weighted by |log_ratio|: the sign keeps a reverse
            # split's fall in volume reading as the print, like a forward split's rise.
            step = math.log(high / low)
            weighted[source] = weighted.get(source, 0.0) + math.copysign(step, log_ratio)
            size[source] = size.get(source, 0.0) + abs(log_ratio)
    out: dict[str, VolumeBasis] = {}
    for source, total in size.items():
        fraction = weighted[source] / total
        if fraction >= VOLUME_RAW_AT_LEAST:
            out[source] = VolumeBasis.RAW
        elif fraction <= VOLUME_ADJUSTED_AT_MOST:
            out[source] = VolumeBasis.ADJUSTED
        else:
            out[source] = VolumeBasis.UNDETERMINED
    return out


def basis_of(
    source: str,
    bases: dict[str, VolumeBasis],
    session_date: dt.date,
    recorded: Sequence[SplitAdjustment],
) -> VolumeBasis:
    """The basis of one bar's volume, from its supplier and the security's evidence.

    **Asked per bar, because it only matters for a bar with a split after it.**
    Every factor in :func:`volume_on_price_basis` counts splits after the bar, so
    a bar later than the security's last recorded split is served exactly as
    stored whatever the basis is. Asking the question per security instead marked
    a series ``UNDETERMINED`` from end to end because of one split before its
    first bar -- measured on the adx_14 pilot, where it was a large share of the
    rows flagged.

    Shared by both read paths so ``price_series`` and ``CorpusSessionData`` cannot
    disagree about which bars' volume can be trusted as a level.
    """
    if not any(split.ex_date > session_date for split in recorded):
        return VolumeBasis.NOT_NEEDED
    return bases.get(source, VolumeBasis.UNDETERMINED)


def volume_on_price_basis(
    stored: Decimal,
    session_date: dt.date,
    basis: VolumeBasis,
    *,
    recorded: Sequence[SplitAdjustment],
    baked: Sequence[SplitAdjustment],
    price_factor: Decimal,
) -> Decimal:
    """Stored volume, restated so that served price times it is the money that traded.

    ``price_factor`` is what the read divides the stored price by. Three factors,
    each counting only splits **after** the bar:

    * ``V`` -- the splits the vendor folded into the stored volume: every
      recorded split if ``ADJUSTED``, none if ``RAW``. Dividing it out gives the
      shares that traded.
    * ``A`` -- the ``baked`` splits the vendor folded into the stored **price**.
      That price is on the post-split share count, so the volume is too.
    * ``price_factor`` -- the read's own adjustment, applied to volume the way it
      is applied to price, inverted.

    ``UNDETERMINED`` is served exactly as the read did before §0.9 was found --
    stored times ``price_factor`` -- because no better number can be stated. The
    bar carries the basis so a level-based caller can refuse it.
    """
    if basis is VolumeBasis.UNDETERMINED:
        return stored * price_factor
    folded = Decimal(1)
    if basis is VolumeBasis.ADJUSTED:
        for split in recorded:
            if split.ex_date > session_date:
                folded *= split.ratio
    in_price = Decimal(1)
    for split in baked:
        if split.ex_date > session_date:
            in_price *= split.ratio
    return stored / folded * in_price * price_factor


def _within_adjudication(session: Session, security_id: int, ex_date: dt.date) -> bool:
    bound = adjudicated_bound(session, security_id)
    return bound is None or ex_date < bound


def known_splits(
    session: Session,
    security_id: int,
    *,
    as_of: dt.datetime,
    include_disputed: bool = False,
) -> list[SplitAdjustment]:
    """Splits for this security that were knowable at ``as_of``, oldest first.

    The ``knowledge_time`` bound is the whole point and is not optional.

    **Actions are bounded by the adjudicated interval too, and leaving them
    unbounded was a real defect.** Corporate actions were fetched under the same
    symbol as the prices, so a series that turned out to hold two companies
    holds two companies' splits. Cutting the bars alone left the successor's six
    compounding reverse splits still dividing the registrant's prices, and
    ``ASCX``'s $18.00 close in 2000 still read as three trillion dollars -- the
    exact absurdity that set this whole investigation off, surviving the fix
    meant to end it.
    """
    conditions = [
        SecurityCorporateActionFact.security_id == security_id,
        SecurityCorporateActionFact.action_type.in_(_SPLIT_TYPES),
        SecurityCorporateActionFact.knowledge_time <= as_of,
        SecurityCorporateActionFact.ratio.is_not(None),
    ]
    if not include_disputed:
        bound = adjudicated_bound(session, security_id)
        if bound is not None:
            # An ex-date at or after the boundary belongs to whoever held the
            # symbol next, and their share count says nothing about ours.
            conditions.append(SecurityCorporateActionFact.ex_date < bound)
    rows = session.execute(
        select(
            SecurityCorporateActionFact.ex_date,
            SecurityCorporateActionFact.ratio,
            SecurityCorporateActionFact.knowledge_time,
        )
        .where(*conditions)
        .order_by(SecurityCorporateActionFact.ex_date)
    ).all()
    splits = [
        SplitAdjustment(ex_date=ex, ratio=Decimal(str(ratio)), knowledge_time=kt)
        for ex, ratio, kt in rows
        if ratio and Decimal(str(ratio)) > 0
    ]
    if include_disputed:
        return splits
    # Splits the vendor had already folded into ``raw`` are not applied again,
    # and contradicted ones are not applied at all -- see SplitEvidence.
    return [
        split
        for split in splits
        if split_evidence(session, security_id, split.ex_date, split.ratio) in _APPLIED
    ]


def price_series(
    session: Session,
    security_id: int,
    *,
    as_of: dt.datetime,
    start: dt.date | None = None,
    end: dt.date | None = None,
    include_disputed: bool = False,
    calendar: TradingCalendar | None = None,
) -> list[AdjustedBar]:
    """The split-adjusted series for one security, as knowable at ``as_of``.

    Revisions are handled the way every point-in-time read here does: where a
    session has several rows, the one with the latest ``knowledge_time`` at or
    before ``as_of`` wins. A later correction does not exist at an earlier
    as-of, which is the correct answer rather than a special case.

    **Bars outside the ticker's adjudicated interval are excluded by default.**
    That is a departure from :func:`series_coherence`, which reports and never
    filters -- and the difference is the evidence. Coherence is a heuristic
    about a shape; an adjudicated bound is a boundary that was established, cited
    and written to the corpus. Filtering on the first would hide a splice;
    filtering on the second is the corpus being read as it is recorded.

    ``include_disputed=True`` returns everything, for a caller auditing the cut
    rather than trading on it. It is spelled out at every call site so that
    reading a known-contaminated series is never the accident.

    **Bars priced at zero are excluded on read**, for the same reason and by the
    same rule: the corpus was sent them, keeps them, and does not serve them.
    ``include_disputed=True`` returns them, like every other exclusion here.

    **So are bars asserting a price nobody traded** -- a zero-volume bar that is
    not an exact flat copy of the last traded close. See :func:`admit_prints`
    for the rule and the measurement that chose it over refusing every
    zero-volume bar. A carried close *is* served, with its zero volume intact,
    so a caller that needs to transact at a bar must still check
    ``bar.volume > 0``: a price someone traded yesterday is not one you could
    trade at today.

    **Bars dated on a day the market was closed are excluded on read, and left
    in the table.** 3,930 of them arrived before the importer learned to refuse
    them -- July 4th, Thanksgiving, Good Friday, and 2025-01-09, the national
    day of mourning. They are excluded rather than deleted for the same reason
    a spliced bar is: the corpus's habit is to bound what it will read, not to
    destroy what it was sent, so the vendor's error stays visible to an audit
    and stops reaching a strategy. ``include_disputed=True`` returns them too,
    since a caller auditing the cut needs to see what was cut.
    """
    conditions = [
        SecurityPriceFact.security_id == security_id,
        SecurityPriceFact.adjustment_basis == "raw",
        SecurityPriceFact.knowledge_time <= as_of,
    ]
    if start is not None:
        conditions.append(SecurityPriceFact.session_date >= start)
    if end is not None:
        conditions.append(SecurityPriceFact.session_date <= end)
    if not include_disputed:
        opens, closes = adjudicated_window(session, security_id)
        if opens is not None:
            conditions.append(SecurityPriceFact.session_date >= opens)
        if closes is not None:
            conditions.append(SecurityPriceFact.session_date < closes)

    rows = session.execute(
        select(
            SecurityPriceFact.session_date,
            SecurityPriceFact.open,
            SecurityPriceFact.high,
            SecurityPriceFact.low,
            SecurityPriceFact.close,
            SecurityPriceFact.volume,
            SecurityPriceFact.knowledge_time,
            SecurityPriceFact.source,
        )
        .where(*conditions)
        .order_by(SecurityPriceFact.session_date, SecurityPriceFact.knowledge_time)
    ).all()

    # Latest revision per session, by iterating in knowledge_time order and
    # letting later rows overwrite earlier ones for the same date.
    latest: dict[dt.date, tuple[Decimal, Decimal, Decimal, Decimal, Decimal]] = {}
    #: The source of the revision that won each session: volume's basis is a
    #: property of who supplied it, and one security can hold two vendors.
    supplier: dict[dt.date, str] = {}
    sessions = calendar or TradingCalendar()
    for session_date, o, h, low, c, v, _kt, source in rows:
        if not include_disputed and not sessions.is_session(session_date):
            # A day with no trading has no price. Filtered here rather than in
            # SQL because the exchange calendar is not a column.
            continue
        if not include_disputed and min(o, h, low, c) <= 0:
            # A bar priced at zero is not a price. The corpus holds 11,580 of
            # them across 248 securities, clustered at the end of a series
            # where the vendor keeps emitting rows after a stock stops trading
            # -- one of them with volume 110 at a price of exactly zero.
            #
            # ``AdjustedBar`` is a plain dataclass and would hand one over
            # unvalidated, where ``OhlcvBar`` refuses it. Dividing by a zero
            # factor is not the risk; booking a -100% return on a session
            # nobody traded is, and these sit at the end of a series, which is
            # exactly where a survivorship study is most sensitive.
            continue
        latest[session_date] = (o, h, low, c, v)
        supplier[session_date] = source

    baked: list[SplitAdjustment] = []
    recorded: list[SplitAdjustment] = []
    bases: dict[str, VolumeBasis] = {}
    if include_disputed:
        splits = known_splits(session, security_id, as_of=as_of, include_disputed=True)
        floor = None
    else:
        splits, baked, floor = classified_splits(session, security_id, as_of=as_of)
        recorded = recorded_splits(session, security_id)
        bases = volume_basis(session, security_id, recorded)

    ordered: list[PrintRow] = [
        (day, *latest[day]) for day in sorted(latest) if floor is None or day >= floor
    ]
    if not include_disputed:
        # On raw values, before adjustment: the anchor is a raw close, and the
        # split reset inside the rule is what stops a pre-split close carrying
        # into a post-split session.
        ordered, _ = admit_prints(ordered, (split.ex_date for split in splits))

    out: list[AdjustedBar] = []
    for session_date, o, h, low, c, v in ordered:
        # Every split strictly AFTER this bar. A split on the bar's own date has
        # already taken effect in that day's print, so applying it again would
        # halve a price that was never doubled.
        factor = Decimal(1)
        for split in splits:
            if split.ex_date > session_date:
                factor *= split.ratio
        if include_disputed:
            # The audit path reads as it always has; the basis says so.
            basis = VolumeBasis.UNDETERMINED if splits else VolumeBasis.NOT_NEEDED
        else:
            basis = basis_of(supplier[session_date], bases, session_date, recorded)
        out.append(
            AdjustedBar(
                session_date=session_date,
                open=o / factor,
                high=h / factor,
                low=low / factor,
                close=c / factor,
                # Volume is put on the same share count as the price just
                # served, so their product is the money that traded -- see
                # volume_on_price_basis and DATA_DICTIONARY §0.9. Multiplying
                # stored volume by the price factor, as this did before, double
                # counted every vendor that had already restated it.
                volume=volume_on_price_basis(
                    v,
                    session_date,
                    basis,
                    recorded=recorded,
                    baked=baked,
                    price_factor=factor,
                ),
                split_factor=factor,
                raw_close=c,
                volume_basis=basis,
            )
        )
    return out


class Coherence(StrEnum):
    """Whether a price series plausibly belongs to the registrant it is filed under.

    Measured on the corpus, the distribution is **bimodal rather than a tail**:
    89.3% of series end within a year of their registrant's last EDGAR filing,
    and 9.2% end **more than seven years** after it, several by more than
    twenty. Two populations, not one with outliers.

    A company that stopped filing in 2001 and has prices to 2026 is far more
    likely to be two companies than one that traded silently for a quarter
    century. The vendor marks a reused ticker with ``_old`` -- but confirmation
    binds the *plain* symbol to whichever registrant the filing named, and the
    plain symbol's history then runs on into the next holder's.
    """

    #: Series ends near the registrant's last filing. The expected shape.
    COHERENT = "coherent"
    #: Ends somewhat after. A delisted company can trade over the counter for a
    #: while without filing, so this is a question rather than a verdict.
    QUESTIONABLE = "questionable"
    #: Ends many years after. Treat as two companies until shown otherwise.
    SUSPECT_TICKER_REUSE = "suspect_ticker_reuse"
    #: No filing span known, so the comparison cannot be made. **Not** coherent.
    UNKNOWN = "unknown"


#: Years past the last filing at which a series stops being explainable by
#: over-the-counter trading and starts looking like a second company. Chosen
#: from the measured gap between the two populations, not from taste.
QUESTIONABLE_AFTER_YEARS = 1.0
SUSPECT_AFTER_YEARS = 7.0


def series_coherence(
    *, last_session: dt.date | None, last_filing: dt.date | None
) -> tuple[Coherence, float | None]:
    """Compare where a series ends to where its registrant stopped filing.

    Returns the verdict and the gap in years. **Reports rather than filters**:
    truncating a suspect series here would discard legitimate post-delisting
    trading, and dropping it would hide a splice instead of naming it. The
    caller decides, with the verdict in hand.
    """
    if last_session is None or last_filing is None:
        return Coherence.UNKNOWN, None
    years = (last_session - last_filing).days / 365.25
    if years > SUSPECT_AFTER_YEARS:
        return Coherence.SUSPECT_TICKER_REUSE, years
    if years > QUESTIONABLE_AFTER_YEARS:
        return Coherence.QUESTIONABLE, years
    return Coherence.COHERENT, years
