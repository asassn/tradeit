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
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.storage.tables import SecurityCorporateActionFact, SecurityPriceFact, SymbolAlias

__all__ = [
    "AdjustedBar",
    "Coherence",
    "SplitAdjustment",
    "adjudicated_bound",
    "known_splits",
    "price_series",
    "series_coherence",
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

    @property
    def is_adjusted(self) -> bool:
        return self.split_factor != 1


def adjudicated_bound(session: Session, security_id: int) -> dt.date | None:
    """The last session this security's ticker is evidenced to have meant it.

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
            # An ex-date after the boundary belongs to whoever held the symbol
            # next, and their share count says nothing about ours.
            conditions.append(SecurityCorporateActionFact.ex_date <= bound)
    rows = session.execute(
        select(
            SecurityCorporateActionFact.ex_date,
            SecurityCorporateActionFact.ratio,
            SecurityCorporateActionFact.knowledge_time,
        )
        .where(*conditions)
        .order_by(SecurityCorporateActionFact.ex_date)
    ).all()
    return [
        SplitAdjustment(ex_date=ex, ratio=Decimal(str(ratio)), knowledge_time=kt)
        for ex, ratio, kt in rows
        if ratio and Decimal(str(ratio)) > 0
    ]


def price_series(
    session: Session,
    security_id: int,
    *,
    as_of: dt.datetime,
    start: dt.date | None = None,
    end: dt.date | None = None,
    include_disputed: bool = False,
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
        bound = adjudicated_bound(session, security_id)
        if bound is not None:
            conditions.append(SecurityPriceFact.session_date <= bound)

    rows = session.execute(
        select(
            SecurityPriceFact.session_date,
            SecurityPriceFact.open,
            SecurityPriceFact.high,
            SecurityPriceFact.low,
            SecurityPriceFact.close,
            SecurityPriceFact.volume,
            SecurityPriceFact.knowledge_time,
        )
        .where(*conditions)
        .order_by(SecurityPriceFact.session_date, SecurityPriceFact.knowledge_time)
    ).all()

    # Latest revision per session, by iterating in knowledge_time order and
    # letting later rows overwrite earlier ones for the same date.
    latest: dict[dt.date, tuple[Decimal, Decimal, Decimal, Decimal, Decimal]] = {}
    for session_date, o, h, low, c, v, _kt in rows:
        latest[session_date] = (o, h, low, c, v)

    splits = known_splits(session, security_id, as_of=as_of, include_disputed=include_disputed)

    out: list[AdjustedBar] = []
    for session_date in sorted(latest):
        o, h, low, c, v = latest[session_date]
        # Every split strictly AFTER this bar. A split on the bar's own date has
        # already taken effect in that day's print, so applying it again would
        # halve a price that was never doubled.
        factor = Decimal(1)
        for split in splits:
            if split.ex_date > session_date:
                factor *= split.ratio
        out.append(
            AdjustedBar(
                session_date=session_date,
                open=o / factor,
                high=h / factor,
                low=low / factor,
                close=c / factor,
                # A split multiplies the share count, so historical volume is
                # multiplied where price is divided. Adjusting one without the
                # other silently breaks every turnover and liquidity measure.
                volume=v * factor,
                split_factor=factor,
                raw_close=c,
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
