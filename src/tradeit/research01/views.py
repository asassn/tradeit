"""The corpus's read conventions, expressed as SQL views.

`RESEARCH_01_DATA_DICTIONARY.md` §0 lists six ways this corpus returns a
confident wrong answer. A dictionary helps a *reader* and does nothing for a
*tool*, so these views put the conventions in the database: ``select * from
v_prices`` is correct by construction -- one row per session, adjusted, with
disputed and non-session bars already excluded.

Nothing is deleted, altered or moved. A view is a saved query; dropping one
costs nothing and the evidence underneath is untouched.

**The one thing a view cannot do, stated here so it does not become a seventh
trap.** A view takes no ``as_of`` argument, so it cannot be point-in-time.
``v_prices`` returns *the current belief* -- the latest ``knowledge_time`` for
each session. 327,924 keys in this corpus carry more than one revision, so that
is a real difference rather than a technicality. **Anything asking what was
knowable on a past date must use** :func:`tradeit.research01.series.price_series`,
which takes ``as_of``. These views serve the simple case; they do not replace
the library, and :func:`create_views` writes that warning into each view's own
SQL where a reader running ``.schema`` will meet it.

``trading_sessions`` is a materialised table rather than a view because an
exchange calendar is not expressible in SQL. It is rebuilt on every call.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session

from tradeit.core.calendar import TradingCalendar

__all__ = ["CALENDAR_FROM", "CALENDAR_TO", "VIEWS", "create_views"]

CALENDAR_FROM = dt.date(1990, 1, 1)
CALENDAR_TO = dt.date(2030, 12, 31)

#: The adjudicated window, as `price_series` computes it: the narrowest reading
#: of the security's ticker intervals. `max(valid_from)` and `min(valid_to)`
#: rather than the union, because a security claimed over two disjoint eras
#: should yield the era every alias agrees on.
_WINDOW = """
    left join (
        select security_id,
               max(valid_from) as opens,
               min(valid_to)   as closes
        from symbol_aliases
        where alias_kind = 'ticker'
        group by security_id
    ) w on w.security_id = p.security_id
"""

#: Latest revision only. Expressed as NOT EXISTS rather than a window function
#: because `ix_security_price_pit` is (security_id, adjustment_basis,
#: session_date, knowledge_time) and makes this an index range scan, where a
#: window function would sort 71 million rows.
_LATEST = """
    and not exists (
        select 1 from security_price_facts q
        where q.security_id = p.security_id
          and q.adjustment_basis = p.adjustment_basis
          and q.session_date = p.session_date
          and q.knowledge_time > p.knowledge_time
    )
"""


def _price_view(name: str, basis: str, note: str) -> str:
    return f"""
create view {name} as
-- {note}
select
    p.security_id,
    p.session_date,
    p.open, p.high, p.low, p.close, p.volume,
    p.knowledge_time
from security_price_facts p
join trading_sessions ts on ts.session_date = p.session_date
{_WINDOW}
where p.adjustment_basis = '{basis}'
  and (w.opens is null or p.session_date >= w.opens)
  and (w.closes is null or p.session_date < w.closes)
  {_LATEST}
"""


VIEWS: dict[str, str] = {
    "v_prices": _price_view(
        "v_prices",
        "total",
        "Split- and dividend-adjusted. Use this for returns and any comparison "
        "across time. NOT point-in-time: this is the current belief.",
    ),
    "v_prices_raw": _price_view(
        "v_prices_raw",
        "raw",
        "The unadjusted print -- what a trader saw on the day. Do not compute "
        "returns from this across a split.",
    ),
    "v_security_tickers": """
create view v_security_tickers as
-- One row per ticker a security has held, with the interval arithmetic done.
-- last_day is inclusive; the stored valid_to is exclusive and off by one day,
-- which is trap 0.2 in the data dictionary.
select
    a.security_id,
    s.issuer_id,
    a.alias_value            as ticker,
    a.valid_from             as first_day,
    case when a.valid_to is null then null
         else date(a.valid_to, '-1 day') end as last_day,
    a.valid_to is null       as is_current,
    a.knowledge_source       as evidence,
    a.citation
from symbol_aliases a
join securities s on s.security_id = a.security_id
where a.alias_kind = 'ticker'
""",
    "v_dead_registrants": """
create view v_dead_registrants as
-- The survivorship population: every registrant EDGAR shows exiting, with
-- whether the corpus can identify, name and price it. `has_prices` is the
-- gate's numerator.
select
    ii.value_normalized      as cik,
    i.display_name           as name_as_last_filed,
    i.source                 as seeded_from,
    exists (select 1 from symbol_aliases a
            join securities s2 on s2.security_id = a.security_id
            where s2.issuer_id = i.issuer_id and a.alias_kind = 'ticker') as has_ticker,
    exists (select 1 from security_price_facts p
            join securities s3 on s3.security_id = p.security_id
            where s3.issuer_id = i.issuer_id) as has_prices
from issuers i
join issuer_identifiers ii
  on ii.issuer_id = i.issuer_id and ii.namespace = 'sec_cik'
""",
}


def create_views(
    session: Session,
    *,
    calendar: TradingCalendar | None = None,
    start: dt.date = CALENDAR_FROM,
    end: dt.date = CALENDAR_TO,
) -> int:
    """(Re)build ``trading_sessions`` and every view. Returns the session count.

    Idempotent: each object is dropped and recreated, so running it twice
    leaves the same database and running it after new data lands refreshes the
    calendar without touching a single fact row.
    """
    sessions = (calendar or TradingCalendar()).sessions_between(start, end)
    session.execute(text("drop table if exists trading_sessions"))
    session.execute(text("create table trading_sessions (session_date date primary key not null)"))
    if sessions:
        session.execute(
            text("insert into trading_sessions (session_date) values (:d)"),
            [{"d": day.isoformat()} for day in sessions],
        )
    for name, sql in VIEWS.items():
        session.execute(text(f"drop view if exists {name}"))
        session.execute(text(sql))
    session.commit()
    return len(sessions)
