"""Reading one security's valuation out of the corpus.

**Separate from `app.py` because it reads a different database, and that is
the whole point.** Everything else the service serves comes from the decision
record --- what the system decided. Valuation comes from `research-01` --- what
the world did. Measured 2026-11-17, the decision record holds **zero** rows in
`securities`, `security_price_facts` and `security_fundamental_facts`, so a
valuation route pointed at it would answer "no data" forever and read as a gap
in the corpus rather than a service wired to the wrong database. That is the
confusion `create_app` already refuses a default DSN to avoid.

So the corpus is a **second, optional** connection. Without it the route says
the corpus is not attached, which is a different answer from "this security
has no valuation" and is reported as one.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tradeit.research01.fundamental_signals import FundamentalHistory, first_filed
from tradeit.valuation import PriceBar, panel_for
from tradeit.valuation.cashflow import (
    CAPEX_METRIC,
    CASH_METRICS,
    CURRENT_DEBT_METRICS,
    LONG_TERM_DEBT_METRICS,
    OPERATING_CASH_METRICS,
)
from tradeit.valuation.multiples import EARNINGS_METRIC, EQUITY_METRIC, SHARES_METRIC

__all__ = [
    "HISTORY_STRIDE",
    "WANTED_METRICS",
    "identity_for",
    "search_securities",
    "valuation_payload",
]

#: Every tag the valuation layer reads, named once so the query fetches
#: exactly what is used. Reading the whole table for one security would pull
#: thousands of rows the panel ignores.
WANTED_METRICS: tuple[str, ...] = (
    EARNINGS_METRIC,
    EQUITY_METRIC,
    SHARES_METRIC,
    CAPEX_METRIC,
    *OPERATING_CASH_METRICS,
    *CASH_METRICS,
    *LONG_TERM_DEBT_METRICS,
    *CURRENT_DEBT_METRICS,
)

#: Sessions between observations in the security's own valuation history.
#: Roughly one a quarter. Daily would add thousands of observations that move
#: only with price while the fundamentals behind them are unchanged, which
#: would make the percentile a statement about price volatility rather than
#: about valuation.
HISTORY_STRIDE = 63


def search_securities(session: Session, query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Find securities by ticker or company name.

    **The single thing that turned this surface from a diagnostic into a
    product.** Before it, the only way in was to type a numeric
    ``security_id``, which nobody knows: the screen asked the reader for the
    answer it was supposed to give them.

    Current tickers sort first and exact ticker matches above those, because
    somebody typing ``GM`` means the company trading as GM today, not a
    1990s holder of the symbol. Both are returned --- a ticker that has
    changed hands is exactly what this corpus exists to keep straight --- with
    the interval shown so the reader can tell them apart.
    """
    like = f"{query.strip()}%"
    rows = session.execute(
        text(
            "select t.security_id, t.ticker, i.display_name, t.is_current, "
            "       t.first_day, t.last_day "
            "from v_security_tickers t "
            "join issuers i on i.issuer_id = t.issuer_id "
            "where t.ticker like :like collate nocase "
            "   or i.display_name like :like collate nocase "
            "order by t.is_current desc, "
            "         case when upper(t.ticker) = upper(:exact) then 0 else 1 end, "
            "         length(t.ticker), t.ticker "
            "limit :limit"
        ).bindparams(like=like, exact=query.strip(), limit=limit),
    ).all()
    return [
        {
            "security_id": int(sid),
            "ticker": str(ticker),
            "name": str(name),
            "is_current": bool(current),
            "first_day": str(first) if first else None,
            "last_day": str(last) if last else None,
        }
        for sid, ticker, name, current, first, last in rows
    ]


def identity_for(session: Session, security_id: int) -> dict[str, Any] | None:
    """The ticker and company name to put at the top of the page.

    Returns the **current** ticker where there is one, and otherwise the last
    one held, flagged. A delisted security shown under a ticker somebody else
    trades today would be the splice this corpus exists to prevent, arriving
    as a page heading.
    """
    row = session.execute(
        text(
            "select t.ticker, i.display_name, t.is_current, t.first_day, t.last_day "
            "from v_security_tickers t "
            "join issuers i on i.issuer_id = t.issuer_id "
            "where t.security_id = :sid "
            "order by t.is_current desc, t.first_day desc limit 1"
        ).bindparams(sid=security_id),
    ).first()
    if row is None:
        return None
    ticker, name, current, first, last = row
    return {
        "ticker": str(ticker),
        "name": str(name),
        "is_current": bool(current),
        "first_day": str(first) if first else None,
        "last_day": str(last) if last else None,
    }


def _history_for(session: Session, security_id: int) -> FundamentalHistory:
    rows = session.execute(
        text(
            "select metric, period_end, coalesce(duration_qtrs, 0) as dur, value, "
            "date(knowledge_time) as known "
            "from security_fundamental_facts "
            "where security_id = :sid and value is not null "
            "and metric in :metrics"
            # `expanding` is required for an IN against a bound sequence:
            # without it SQLite is handed one placeholder for a tuple and
            # fails with `near "?": syntax error`. Found by calling the route.
        )
        .bindparams(bindparam("metrics", expanding=True), sid=security_id)
        .params(metrics=list(WANTED_METRICS)),
    ).all()
    return FundamentalHistory(
        first_filed(
            (
                str(metric),
                period_end
                if isinstance(period_end, dt.date)
                else dt.date.fromisoformat(period_end),
                int(dur),
                float(value),
                known if isinstance(known, dt.date) else dt.date.fromisoformat(known),
            )
            for metric, period_end, dur, value, known in rows
        )
    )


def _prices_for(session: Session, security_id: int) -> list[PriceBar]:
    """Raw prints, never the adjusted series.

    `v_prices` is split- and dividend-adjusted and describes itself as "the
    current belief"; pairing it with per-share and balance-sheet figures
    reported in the share count of their own time is wrong by every split
    since. `v_prices_raw` is "what a trader saw on the day", which is what a
    multiple needs.
    """
    rows = session.execute(
        text(
            "select session_date, close from v_prices_raw "
            "where security_id = :sid order by session_date"
        ).bindparams(sid=security_id),
    ).all()
    return [
        PriceBar(
            session_date=(day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day))),
            close=float(close),
        )
        for day, close in rows
    ]


def valuation_payload(session: Session, security_id: int) -> dict[str, Any]:
    """One security's valuation panel, as JSON.

    **Everything the panel refuses is reported**, not filtered out. A response
    carrying an empty `multiples` list and nothing else would read as "we did
    not look"; the `unresolved` list says what was looked at and why it did not
    answer, which is usually the more useful fact --- a company with negative
    equity is telling you something a missing number is not.
    """
    try:
        prices = _prices_for(session, security_id)
    except OperationalError as missing:
        # `v_prices_raw` is a **view**, created by the corpus build and not by
        # `Base.metadata.create_all`. A database with the right tables and no
        # views is the shape you get from pointing `--corpus` at anything that
        # is not research-01 -- including the decision record, which has every
        # table and none of the rows. Caught so that mistake reads as "wrong
        # database" rather than as a 500 naming a view nobody has heard of.
        return {
            "security_id": security_id,
            "available": False,
            "note": (
                "this database has no `v_prices_raw` view, so it is not a "
                "prepared research-01 corpus. The record and the corpus are "
                f"different databases. Underlying error: {missing.orig}"
            ),
        }
    if not prices:
        return {
            "security_id": security_id,
            "available": False,
            "identity": identity_for(session, security_id),
            "note": (
                "no raw price series for this security in the corpus; valuation "
                "needs the unadjusted print"
            ),
        }
    history = _history_for(session, security_id)
    latest = prices[-1]
    panel = panel_for(
        security_id,
        latest,
        history,
        latest.session_date,
        past_prices=prices[::HISTORY_STRIDE],
    )
    return {
        "security_id": security_id,
        "available": True,
        "identity": identity_for(session, security_id),
        "as_of": panel.as_of.isoformat(),
        "price": {"close": latest.close, "session": latest.session_date.isoformat()},
        "fcf_yield": panel.fcf_yield,
        # Named in the payload rather than left to the reader: a yield inverts
        # a multiple, so for this one a HIGH rank is cheap and for the
        # multiples below a LOW rank is cheap. Getting that backwards reverses
        # the conclusion.
        "fcf_yield_rank": panel.fcf_yield_rank,
        "fcf_yield_rank_means": "fraction of its own history at or below; higher is cheaper",
        "fcf_yield_history_size": panel.fcf_yield_history_size,
        "fcf_yield_history": [
            {"session": day.isoformat(), "value": value} for day, value in panel.fcf_yield_history
        ],
        "free_cash_flow": (
            None
            if panel.free_cash_flow is None
            else {
                "value": panel.free_cash_flow.value,
                "operating_cash": panel.free_cash_flow.operating_cash,
                "capital_expenditure": panel.free_cash_flow.capital_expenditure,
                "fiscal_year_end": panel.free_cash_flow.period_end.isoformat(),
                "first_filed": panel.free_cash_flow.known.isoformat(),
            }
        ),
        "enterprise_value": (
            None
            if panel.enterprise is None
            else {
                "value": panel.enterprise.value,
                "market_cap": panel.enterprise.market_cap,
                "debt": panel.enterprise.debt,
                "cash": panel.enterprise.cash,
                "debt_metrics": list(panel.enterprise.debt_metrics),
                "debt_may_be_understated": panel.enterprise.debt_may_be_understated,
            }
        ),
        "implied_growth": [
            {"discount_rate": g.discount_rate, "growth": g.growth} for g in panel.implied
        ],
        "quality": [{"name": q.name, "detail": q.detail} for q in panel.quality],
        "multiples": [
            {
                "basis": m.basis,
                "value": m.value,
                "rank": panel.multiple_ranks.get(m.basis),
                "rank_means": "fraction of its own history at or below; lower is cheaper",
                "history_size": panel.multiple_history_size.get(m.basis),
                "explanation": m.explanation,
            }
            for m in panel.multiples
        ],
        "unresolved": [{"basis": u.basis, "reason": u.reason} for u in panel.unresolved],
        "explanations": list(panel.explanations),
        "caveats": list(panel.caveats),
        # The thing this surface must keep saying. ADR-0036.
        "note": (
            "no verdict is computed. A multiple has no absolute meaning without "
            "growth and risk, valuation is horizon-dependent, and no fundamental "
            "signal has yet passed on this corpus. These are facts to read, not "
            "a recommendation."
        ),
    }
