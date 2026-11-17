"""A precomputed summary of the corpus, for the views that list securities.

**Measured 2026-11-17: a single corpus-wide count takes 2 minutes 31
seconds.** `select count(distinct security_id) ... where session_date >
'2026-01-01'` over 83.8M price rows. A detail view can afford a second; a list
cannot afford a scan, and a screener that ran one per request would not be a
screener.

So the corpus-wide questions are answered once, into a table, and the views
read that. It is a **cache**, with the properties a cache must carry:

*It says when it was built.* A summary with no build time is a number of
unknown age being read as current, which is the exact failure the forward
record's liveness view exists to prevent. Every row carries `built_at` and the
API reports it.

*It is derived, never authoritative.* Nothing is computed here that the corpus
does not already hold; this table is a different shape, not a different fact.
A disagreement between the two is a defect in this builder, and the corpus
wins.

*It is rebuilt whole.* An incremental refresh would need to know which
securities changed, which means a scan — the thing being avoided. A full
rebuild is slow and honest; a partial one would be fast and silently stale in
a way nothing could detect.

**It lives in the corpus file**, not beside it. The summary is derived from
the corpus and belongs with it, the API already holds that connection, and a
fourth database would be a fourth thing to point at the wrong file.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["BuildReport", "build_summary", "summary_state"]

#: The table name. Prefixed like the corpus's own views so a reader browsing
#: the schema can see it is a derived convenience rather than a fact table.
TABLE = "security_summary"

_CREATE = f"""
create table if not exists {TABLE} (
    security_id       integer primary key,
    ticker            text,
    issuer_name       text,
    is_current        integer,
    first_day         text,
    last_day          text,
    first_session     text,
    last_session      text,
    price_rows        integer,
    priced_rows       integer,
    last_close        real,
    has_fundamentals  integer,
    built_at          text not null
)
"""


@dataclass(frozen=True, slots=True)
class BuildReport:
    """What a rebuild did, measured rather than estimated."""

    securities: int
    with_prices: int
    with_fundamentals: int
    built_at: dt.datetime
    seconds: float

    def describe(self) -> str:
        return (
            f"{self.securities:,} securities summarised in {self.seconds:,.0f}s "
            f"({self.with_prices:,} with prices, "
            f"{self.with_fundamentals:,} with fundamentals), built "
            f"{self.built_at.isoformat()}"
        )


def build_summary(
    session: Session, *, progress: Callable[[str], None] | None = None
) -> BuildReport:
    """Rebuild the summary table from the corpus, whole.

    Each step is a single pass that the database can plan, rather than a loop
    issuing one query per security: 41,698 round trips against a 73 GB file is
    the slow way to do this, and the aggregate passes below are the reason a
    rebuild takes minutes rather than hours.
    """
    say = progress or (lambda _: None)
    started = dt.datetime.now(dt.UTC)
    clock = dt.datetime.now(dt.UTC)

    session.execute(text(f"drop table if exists {TABLE}"))
    session.execute(text(_CREATE))

    # Identity first: cheap, and it is what a reader searches by.
    say("identity…")
    session.execute(
        text(
            f"insert into {TABLE} "
            "(security_id, ticker, issuer_name, is_current, first_day, last_day, "
            " built_at) "
            "select s.security_id, t.ticker, i.display_name, t.is_current, "
            "       t.first_day, t.last_day, :now "
            "from securities s "
            "left join issuers i on i.issuer_id = s.issuer_id "
            "left join ("
            # One ticker per security: the current one where there is one, and
            # otherwise the last held. A security with two is not collapsed
            # arbitrarily -- `max(is_current)` then `max(first_day)` picks the
            # same row the detail view's `identity_for` picks, so the list and
            # the page agree.
            "  select security_id, ticker, is_current, first_day, last_day from ("
            "    select security_id, ticker, is_current, first_day, last_day, "
            "           row_number() over ("
            "             partition by security_id "
            "             order by is_current desc, first_day desc"
            "           ) as rn "
            "    from v_security_tickers"
            "  ) where rn = 1"
            ") t on t.security_id = s.security_id"
        ).bindparams(now=started.isoformat())
    )
    securities = int(session.execute(text(f"select count(*) from {TABLE}")).scalar_one())
    say(f"  {securities:,} securities, {_elapsed(clock):.0f}s")
    clock = dt.datetime.now(dt.UTC)

    # **One grouped pass over the BASE TABLE, not the view.**
    #
    # The first version ran a correlated subquery per security against
    # `v_prices_raw`. That view joins `trading_sessions` and a windowed
    # `symbol_aliases` subquery, so each of 41,698 correlations re-evaluated
    # it: the build ran six minutes without finishing a single step and was
    # stopped. One `group by` over the view is no better — it exceeded ten
    # minutes on its own.
    #
    # The base table has `ix_security_price_pit (security_id,
    # adjustment_basis, session_date, …)`, which is exactly this aggregate, and
    # one pass takes **2 minutes 31 seconds** measured.
    #
    # **What that costs in accuracy, stated rather than hidden.** The view
    # additionally excludes non-session dates, bars outside a ticker's alias
    # window, prints withheld around contradicted splits, and **flat bars at
    # zero volume**. Measured across four ordinary securities the gap was 0,
    # 0, 0 and 4 rows of several thousand — but it is not always small: ASKE
    # has 329 rows that are *all* `999999.9999` at zero volume, a vendor
    # sentinel stored as a price, and the view returns **nothing** for it.
    #
    # So the summary carries two counts. `price_rows` is what the table holds
    # and `priced_rows` is how many of those actually traded; the listing
    # filters and sorts on the second, and a security whose two counts
    # disagree is visible rather than flattering. Neither is named `sessions`,
    # because neither is the number the chart draws.
    say("price coverage (one indexed pass)…")
    session.execute(text("drop table if exists _price_agg"))
    session.execute(
        text(
            "create temp table _price_agg as "
            "select security_id, min(session_date) as first_session, "
            "       max(session_date) as last_session, count(*) as n, "
            # Two counts, not one. A row can exist and still be nothing: the
            # corpus holds 329 rows for ASKE that are all `999999.9999` at
            # zero volume -- a vendor sentinel stored as a price. `v_prices_raw`
            # excludes them and returns nothing for that security, so a
            # summary carrying only the raw row count would say "329 rows,
            # last close $1,000,000" about a series the detail view declines
            # to draw at all. Found by sorting this very list by price.
            "       sum(case when volume > 0 then 1 else 0 end) as priced "
            "from security_price_facts where adjustment_basis = 'raw' "
            "group by security_id"
        )
    )
    session.execute(
        text(
            f"update {TABLE} set "
            "  first_session = a.first_session, "
            "  last_session  = a.last_session, "
            "  price_rows    = a.n, "
            "  priced_rows   = a.priced "
            f"from _price_agg a where a.security_id = {TABLE}.security_id"
        )
    )
    with_prices = int(
        session.execute(text(f"select count(*) from {TABLE} where priced_rows > 0")).scalar_one()
    )
    say(f"  {with_prices:,} with prices, {_elapsed(clock):.0f}s")
    clock = dt.datetime.now(dt.UTC)

    # An index seek per security on the same index, against the small
    # aggregate rather than the view.
    say("last close…")
    session.execute(
        text(
            f"update {TABLE} set last_close = ("
            "  select p.close from security_price_facts p "
            f"  where p.security_id = {TABLE}.security_id "
            "    and p.adjustment_basis = 'raw' "
            f"    and p.session_date = {TABLE}.last_session limit 1"
            ") where last_session is not null"
        )
    )
    say(f"  {_elapsed(clock):.0f}s")
    clock = dt.datetime.now(dt.UTC)

    say("fundamentals present…")
    session.execute(
        text(
            f"update {TABLE} set has_fundamentals = (select exists("
            "  select 1 from security_fundamental_facts f "
            f"  where f.security_id = {TABLE}.security_id and f.value is not null"
            "))"
        )
    )
    with_fundamentals = int(
        session.execute(
            text(f"select count(*) from {TABLE} where has_fundamentals = 1")
        ).scalar_one()
    )
    say(f"  {with_fundamentals:,} with fundamentals, {_elapsed(clock):.0f}s")

    session.execute(text(f"create index if not exists ix_summary_ticker on {TABLE} (ticker)"))
    session.execute(
        text(f"create index if not exists ix_summary_rows on {TABLE} (priced_rows desc)")
    )
    session.commit()

    return BuildReport(
        securities=securities,
        with_prices=with_prices,
        with_fundamentals=with_fundamentals,
        built_at=started,
        seconds=_elapsed(started),
    )


def summary_state(session: Session) -> dict[str, object]:
    """Whether the summary exists, and how old it is.

    **The age is the point.** A list served from a cache with no build time is
    a set of numbers of unknown vintage being read as current — the same
    failure the capture-health view exists to prevent, in a different place.
    """
    exists = session.execute(
        text("select count(*) from sqlite_master where type='table' and name=:t"),
        {"t": TABLE},
    ).scalar_one()
    if not exists:
        return {
            "built": False,
            "note": (
                "no summary table. The views that list securities need one, "
                "because a corpus-wide scan takes minutes: build it with "
                "`tradeit corpus build-summary`"
            ),
        }
    row = session.execute(
        text(f"select count(*) as n, max(built_at) as built_at from {TABLE}")
    ).one()
    if not row.n:
        return {"built": False, "note": "the summary table is empty"}
    built = dt.datetime.fromisoformat(str(row.built_at))
    if built.tzinfo is None:
        built = built.replace(tzinfo=dt.UTC)
    age = (dt.datetime.now(dt.UTC) - built).total_seconds() / 86_400
    return {
        "built": True,
        "built_at": built.isoformat(),
        "age_days": round(age, 2),
        "securities": int(row.n),
        "note": (
            f"this list is a summary built {age:.1f} days ago, not a live read of "
            "the corpus. It is derived and never authoritative: where it and the "
            "corpus disagree, the corpus wins and the builder has a defect"
        ),
    }


def _elapsed(since: dt.datetime) -> float:
    return (dt.datetime.now(dt.UTC) - since).total_seconds()
