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

**Bars priced at zero are excluded too**, and that was found late. The corpus
holds 11,580 of them across 248 securities, clustered at the end of a series
where a vendor keeps emitting rows after a stock stops trading -- one with
volume 110 at a price of exactly zero. ``CorpusSessionData`` was excluding them
and these views were not, which is worse than either choice made consistently:
two supported read paths disagreeing about what the corpus contains. Treating
one as a real print books a -100% return on a session nobody traded.

**And so are bars asserting a price nobody traded** -- a zero-volume bar that
is not an exact flat copy of the last traded close. Same rule as
:func:`tradeit.research01.series.admit_prints`, written out in SQL rather than
approximated, and tested against the library on the same synthetic rows.

``trading_sessions`` is a materialised table rather than a view because an
exchange calendar is not expressible in SQL. It is rebuilt on every call.

``corpus_readme`` is the same argument taken one step further. A view fixes the
traps that are expressible as SQL; the rest -- that names are point-in-time,
that a fetch "failure" often means the system refused to guess -- can only be
*stated*. Stating them in a table means a tool that opens this file finds them
without being handed a markdown document alongside it, which is the whole
complaint that produced this module. It is generated from the same constants
the views are, so the two cannot drift.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session

from tradeit.core.calendar import TradingCalendar

__all__ = [
    "CALENDAR_FROM",
    "CALENDAR_TO",
    "README",
    "RETIRED_VIEWS",
    "VIEWS",
    "create_readme",
    "create_views",
    "prepare_corpus",
]

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


def _last_traded(column: str) -> str:
    """The most recent bar before ``p`` that traded, as the view would serve it."""
    return f"""(
            select a.{column} from security_price_facts a
            join trading_sessions ta on ta.session_date = a.session_date
            where a.security_id = p.security_id
              and a.adjustment_basis = p.adjustment_basis
              and a.session_date < p.session_date
              and a.volume > 0
              and a.open > 0 and a.high > 0 and a.low > 0 and a.close > 0
              and (w.opens is null or a.session_date >= w.opens)
            order by a.session_date desc, a.knowledge_time desc
            limit 1
        )"""


#: The rule in :func:`tradeit.research01.series.admit_prints`, in SQL. A bar
#: with volume is a print; one without is served only as an exact flat copy of
#: the last traded close, and never across a split. Written out rather than
#: approximated because an approximation here would be the fourth time two read
#: paths disagreed about what this corpus contains.
#:
#: The subqueries run only for zero-volume rows -- SQLite short-circuits ``or``
#: -- so the 94% of bars that traded pay nothing for it.
_PRINTS = f"""
    and (
        p.volume > 0
        or (
            p.open = p.close and p.high = p.close and p.low = p.close
            and p.close = {_last_traded("close")}
            and not exists (
                select 1 from security_corporate_action_facts s
                where s.security_id = p.security_id
                  and s.action_type in ('split', 'reverse_split')
                  and s.ex_date <= p.session_date
                  and s.ex_date > {_last_traded("session_date")}
            )
        )
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
  and p.open > 0 and p.high > 0 and p.low > 0 and p.close > 0
  and (w.opens is null or p.session_date >= w.opens)
  and (w.closes is null or p.session_date < w.closes)
  {_PRINTS}
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
    "v_registrants": """
create view v_registrants as
-- Every registrant carrying a CIK, with whether the corpus can ticker and
-- price it. Coverage, not survivorship.
--
-- It was called v_dead_registrants and its comment claimed "every registrant
-- EDGAR shows exiting". It never filtered to exits: measured, it returns
-- 40,818 rows, which is every issuer with a CIK. Anyone reading the old name
-- would have taken a coverage denominator for a survivorship one.
--
-- The exit population is not derivable from `issuers`, which carries no
-- lifecycle column at all -- it comes from filing evidence, and lives with the
-- denominator work rather than here.
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


#: Views this module used to build and no longer does.
#:
#: Recorded rather than simply dropped from ``VIEWS``, because rebuilding only
#: drops what it is about to create -- so a renamed view otherwise survives in
#: every database that ever had it, under a name whose meaning has changed.
#: ``v_dead_registrants`` is the case that taught this: it never filtered to
#: exits and its name said it did.
RETIRED_VIEWS: tuple[str, ...] = ("v_dead_registrants",)


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
    for name in RETIRED_VIEWS:
        session.execute(text(f"drop view if exists {name}"))
    for name, sql in VIEWS.items():
        session.execute(text(f"drop view if exists {name}"))
        session.execute(text(sql))
    session.commit()
    return len(sessions)


#: What a reader must know that the schema cannot tell them, written into the
#: database itself. Each row is (topic, applies_to, guidance).
#:
#: **Ordered by how badly the mistake hurts**, not alphabetically: a tool that
#: reads only the first row should have read the one that matters most. Every
#: entry corresponds to a confident wrong answer this corpus actually produced.
README: tuple[tuple[str, str, str], ...] = (
    (
        "start here",
        "corpus_readme",
        "This table is the reading guide for the file you have opened. The long "
        "form is docs/RESEARCH_01_DATA_DICTIONARY.md in the tradeit repository, "
        "but nothing here depends on having it. Prefer the v_ views over the "
        "base tables: each one has a convention already applied.",
    ),
    (
        "evidence of profitability: four limitations",
        "security_price_facts",
        "The survivorship gate reads MATERIALLY_SURVIVORSHIP_CORRECTED, and on "
        "2026-09-15 the owner decided results here may count as evidence with "
        "four limitations: coverage before 2007 is thin (8.0% before 1999), so "
        "pre-2007 results are not survivorship-corrected for that era; raw "
        "prints before contradicted splits are withheld; splits the vendor never "
        "recorded are not caught; and results measured before that date must be "
        "re-run. Evidence is still not profitability: pre-registration and "
        "out-of-sample tests decide what a result is worth. "
        "v_registrants shows the coverage side of it: every registrant with a "
        "CIK, and whether this corpus can ticker and price it. It is not "
        "filtered to companies that exited -- `issuers` has no lifecycle "
        "column -- so do not read it as a survivorship denominator.",
    ),
    (
        "every trading day is stored twice",
        "security_price_facts",
        "adjustment_basis holds 'raw' and 'total' for the same session. "
        "count(*) therefore reports double the sessions. Use 'total' for "
        "returns and any comparison across time, 'raw' to reconstruct what a "
        "trader saw on the day, and never mix them in one calculation. "
        "v_prices and v_prices_raw each pick one.",
    ),
    (
        "raw is not always the unadjusted print",
        "security_price_facts",
        "EODHD's raw close is sometimes already split-adjusted. price_series and "
        "the backtester now test each recorded split against the raw and total "
        "prints: a split already inside raw is not applied again, and prints "
        "before a split whose record contradicts the prints are withheld -- "
        "918,473 of them (2.38%), after Sharadar's printed close settled 821 of "
        "1,872 such splits into security_split_price_verdicts. A raw "
        "SELECT does neither, so it still double-counts those splits. A split "
        "that was never recorded is not caught: earlier raw levels may be "
        "adjusted. Rows with source 'sharadar-backfill' are rebuilt from the "
        "printed close.",
    ),
    (
        "a session can have more than one revision",
        "security_price_facts",
        "knowledge_time records when a fact became known. The latest row for a "
        "(security, basis, session) is the current belief; earlier ones are "
        "what was believed before. The views return the latest only. Anything "
        "asking what was knowable on a past date must use "
        "tradeit.research01.series.price_series, which takes as_of -- no view "
        "can be point-in-time.",
    ),
    (
        "a bar can have a price and no trade behind it",
        "security_price_facts",
        "2,245,866 raw bars (6.339%) carry volume 0. 86.5% of them are flat "
        "copies of the previous close -- a quiet day on a thin stock -- and those "
        "are served, because refusing them would make 23,136 live stocks look "
        "delisted. The rest assert a price nobody traded: security 4565 "
        "alternates 0.0001 and 92000 on zero volume. Those are refused by "
        "price_series, the backtester and both price views alike. A served "
        "zero-volume bar is still one nobody traded on that day: any "
        "calculation that transacts at a bar must require volume > 0. A raw "
        "SELECT sees every one of them.",
    ),
    (
        "valid_to is exclusive",
        "symbol_aliases",
        "The last day a security owns a ticker is valid_to minus one day. NULL "
        "means open-ended -- the SEC publishes it as current -- not unknown. "
        "Tickers are reused, so resolve per date, never per ticker: asking "
        "which security is 'ACME' has no answer. v_security_tickers does the "
        "arithmetic and exposes an inclusive last_day.",
    ),
    (
        "rows known to be wrong are still here",
        "security_price_facts",
        "This corpus bounds what it reads rather than destroying what it was "
        "sent, so an audit can always see the vendor's error. Roughly 3,900 "
        "bars fall on days the US market was closed and roughly 91,000 sit "
        "outside their security's adjudicated interval. A raw SELECT includes "
        "both; the views and price_series exclude them.",
    ),
    (
        "names are point-in-time",
        "issuers",
        "display_name is the registrant's name as its most recent index row "
        "rendered it. Yahoo! is YAHOO INC, BlackBerry is RESEARCH IN MOTION "
        "LTD, Block is SQUARE, INC. A name search for a renamed company "
        "returns nothing and looks like absence -- five major companies were "
        "reported missing from this corpus on exactly that mistake, and all "
        "five were present. Search by CIK.",
    ),
    (
        "a fetch failure is not a coverage gap",
        "ingestion_runs",
        "Price backfill asks the vendor only for each registrant's own "
        "lifetime. When a different company later took that ticker the request "
        "returns nothing and is logged as a failure. Measured: of 678 such "
        "failures the vendor could serve, 547 were a different company's "
        "series. The count is how often the system refused to guess.",
    ),
    (
        "most tables are empty by design",
        "sqlite_master",
        "The schema is declared in full for later phases; only a handful of "
        "tables carry data today. An empty table is not a broken one. Ask "
        "whether a table has rows before concluding a capability is missing.",
    ),
    (
        "class_label is a share-class title only",
        "securities",
        "It once also held identity prose for 34 control securities, which is "
        "why older notes describe filtering on source before trusting it. "
        "Migration 0017 moved that prose to identity_evidence and left "
        "class_label NULL on those rows. A short operator tag lives in note.",
    ),
)


def create_readme(session: Session) -> int:
    """(Re)build ``corpus_readme``. Returns the number of entries written.

    Dropped and rebuilt rather than updated in place: the table is generated
    output, and a stale row surviving an edit is exactly the failure the table
    exists to prevent.
    """
    session.execute(text("drop table if exists corpus_readme"))
    session.execute(
        text(
            "create table corpus_readme ("
            " ordinal integer primary key not null,"
            " topic text not null,"
            " applies_to text not null,"
            " guidance text not null)"
        )
    )
    session.execute(
        text(
            "insert into corpus_readme (ordinal, topic, applies_to, guidance)"
            " values (:o, :t, :a, :g)"
        ),
        [
            {"o": index, "t": topic, "a": applies_to, "g": guidance}
            for index, (topic, applies_to, guidance) in enumerate(README, start=1)
        ],
    )
    session.commit()
    return len(README)


def prepare_corpus(
    session: Session,
    *,
    calendar: TradingCalendar | None = None,
    start: dt.date = CALENDAR_FROM,
    end: dt.date = CALENDAR_TO,
) -> tuple[int, int]:
    """Everything that makes the file self-describing. Returns (sessions, entries).

    One entry point because the two halves answer the same question and are
    read together: a view a reader does not know to prefer is a view that does
    not help.
    """
    sessions = create_views(session, calendar=calendar, start=start, end=end)
    return sessions, create_readme(session)
