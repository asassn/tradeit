"""When a backfilled fact became usable -- which is not when we received it.

**The failure this module exists to prevent.** A vendor file delivered today
containing a 1999 bar is not the same object as a bar recorded in 1999. Get the
timestamp wrong in one direction and the corpus silently claims we knew things we
did not, which licenses a backtest decision that could not have been made. Get it
wrong in the other and every backfilled bar is invisible to every historical
clock, which makes the corpus useless.

The resolution is that these are **two questions with two columns**, and the
schema already separates them:

``knowledge_time``
    when could a diligent observer first have *used* this fact? A property of
    the world. This is what :class:`~tradeit.core.clock.AsOfClock` filters on.
``ingested_at``
    when did our pipeline write the row? Today, for any backfill. Provenance,
    and ``TimestampMixin`` already forbids point-in-time filtering on it.

So a raw 1999 bar backfilled today carries a 1999 ``knowledge_time`` and a
present-day ``ingested_at``. That is not a claim that we existed in 1999; it is
a claim that the *print* was public in 1999, which is true and checkable.

**The case where that reasoning fails, and it is the dangerous one.** A
split-adjusted 1999 close, computed by a vendor today, embeds every corporate
action between 1999 and today. Its value depends on the future.

The rule is **not** that adjusted prices are never historically knowable -- a
series adjusted as of 2005, using only splits public by 2005, is perfectly
point-in-time valid. What is invalid is a **vendor-delivered** adjusted series,
because its adjustment epoch is the delivery date and nothing earlier.

> **Consequence, named here rather than left for milestone 4 to discover.**
> Point-in-time corporate actions are a **hard dependency** for any historical
> backtest on this corpus. Deriving our own adjusted series from raw bars plus
> actions known at the as-of instant is the *only* route to an adjusted series
> that is valid at that instant, because every vendor-delivered one is stamped
> at delivery. ``security_corporate_action_facts`` is therefore not an optional
> enrichment; it is on the critical path.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from tradeit.core.calendar import TradingCalendar, get_calendar

__all__ = ["KnowledgeTimeBasis", "action_knowledge_time_for", "knowledge_time_for"]


class KnowledgeTimeBasis(StrEnum):
    """How a ``knowledge_time`` was arrived at.

    Recorded because the timestamp alone cannot say, and because the answer is
    **not** recoverable from ``adjustment_basis``: a raw bar can also fall to
    :attr:`DELIVERY_UNESTABLISHED`, and is then indistinguishable from an
    ordinary raw bar without this.
    """

    #: The source stated when the fact became public. The only measured one.
    SOURCE_DISSEMINATED = "source_disseminated"
    #: Derived by rule from the session close. Valid for a raw print.
    SESSION_CLOSE = "session_close"
    #: A vendor-delivered adjusted series. Its adjustment epoch is the delivery.
    COMPUTED_AT_DELIVERY = "computed_at_delivery"
    #: Nothing established a historical instant. Fail closed: use the delivery
    #: time, making the row invisible to any earlier as-of, rather than guess.
    DELIVERY_UNESTABLISHED = "delivery_unestablished"


def knowledge_time_for(
    *,
    adjustment_basis: str,
    session_date: dt.date,
    delivered_at: dt.datetime,
    disseminated_at: dt.datetime | None = None,
    calendar: TradingCalendar | None = None,
) -> tuple[dt.datetime, KnowledgeTimeBasis]:
    """When this bar became usable, and how that was decided.

    Order of resolution, and each step is a refusal to do the next:

    1. **An adjusted bar takes the delivery time, unconditionally.** Even when
       the vendor supplies a dissemination timestamp -- because that timestamp
       describes the original *print*, not the adjustment, and accepting it
       would backdate a value computed today. This deliberately outranks
       ``disseminated_at``.
    2. A stated dissemination instant, when there is one.
    3. The session close, for a raw bar on a real trading day.
    4. Otherwise the delivery time, recorded as unestablished.
    """
    if adjustment_basis != "raw":
        return delivered_at, KnowledgeTimeBasis.COMPUTED_AT_DELIVERY
    if disseminated_at is not None:
        return disseminated_at, KnowledgeTimeBasis.SOURCE_DISSEMINATED
    cal = calendar or get_calendar()
    if cal.is_session(session_date):
        return cal.close_instant(session_date), KnowledgeTimeBasis.SESSION_CLOSE
    return delivered_at, KnowledgeTimeBasis.DELIVERY_UNESTABLISHED


def action_knowledge_time_for(
    *,
    ex_date: dt.date,
    delivered_at: dt.datetime,
    calendar: TradingCalendar | None = None,
) -> tuple[dt.datetime, KnowledgeTimeBasis]:
    """When a corporate action became usable. Never earlier than its ex-date.

    A corporate action is a fact about the world rather than a derived series,
    so there is no ``COMPUTED_AT_DELIVERY`` case here: nothing about a split
    ratio is recomputed at delivery the way an adjusted price is.

    **An announcement date is deliberately not accepted, and the reason is a
    real tension rather than an oversight.** A split is announced *before* its
    ex-date, so an announcement instant would be a knowledge_time earlier than
    the event_time it describes -- which ``ck_security_action_knowledge``
    (``knowledge_time >= event_time``) forbids, and rightly, since that
    invariant is what stops a fact being usable before it happened.

    Resolving that properly means deciding whether a corporate action has two
    events -- an announcement and an effect -- and the schema carries one
    ``event_time``. That is a modelling decision with its own consequences and
    is **deferred, not assumed**. Until it is taken, the ex-date is used, which
    is *conservative*: we may credit ourselves with knowing later than we could
    have, and never earlier. Under-informed is a safe direction; look-ahead is
    not.
    """
    cal = calendar or get_calendar()
    if cal.is_session(ex_date):
        return cal.close_instant(ex_date), KnowledgeTimeBasis.SESSION_CLOSE
    return delivered_at, KnowledgeTimeBasis.DELIVERY_UNESTABLISHED
