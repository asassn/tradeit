"""Listing securities — the one thing the corpus cannot be scanned for.

**Measured 2026-11-17: a single corpus-wide count takes 2 minutes 31
seconds** over 83.8M price rows. So this route reads
`tradeit.research01.readmodel`'s precomputed summary, and says so.

The honesty requirement is the same one the capture-health view carries: a
list served from a cache with no build time is a set of numbers of unknown
vintage being read as current. Every response carries the summary's age, and
a response from a corpus with no summary **says that** rather than returning
an empty list, because an empty list reads as "there are no securities".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tradeit.research01.readmodel import TABLE, summary_state

__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "SORTS", "securities_payload"]

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

#: Orderings offered, as an allow-list mapped to SQL. The key reaches this
#: module from a query parameter, so it is looked up rather than interpolated.
SORTS: dict[str, str] = {
    "ticker": "ticker collate nocase asc",
    "priced_rows": "priced_rows desc",
    "last_session": "last_session desc",
    "last_close": "last_close desc",
}


def securities_payload(
    session: Session,
    *,
    query: str | None = None,
    sort: str = "priced_rows",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    with_prices_only: bool = True,
) -> dict[str, Any]:
    """A page of securities, from the summary rather than from the corpus."""
    state = summary_state(session)
    if not state.get("built"):
        # Not an empty list. An empty list reads as "there are no securities",
        # which is a claim about the corpus rather than about this cache.
        return {"available": False, "summary": state, "securities": [], "total": 0}

    where = ["1=1"]
    params: dict[str, Any] = {}
    if with_prices_only:
        # The default, because a security with no price series cannot be
        # charted or valued and its row would be a dead end.
        where.append("priced_rows > 0")
    if query:
        where.append("(ticker like :q collate nocase or issuer_name like :q collate nocase)")
        params["q"] = f"{query.strip()}%"
    clause = " and ".join(where)

    total = int(
        session.execute(text(f"select count(*) from {TABLE} where {clause}"), params).scalar_one()
    )
    rows = session.execute(
        text(
            f"select security_id, ticker, issuer_name, is_current, first_day, "
            f"       last_day, first_session, last_session, price_rows, priced_rows, "
            f"       last_close, "
            f"       has_fundamentals "
            f"from {TABLE} where {clause} "
            f"order by {SORTS.get(sort, SORTS['priced_rows'])} "
            "limit :limit offset :offset"
        ),
        {**params, "limit": min(limit, MAX_LIMIT), "offset": offset},
    ).all()

    return {
        "available": True,
        "summary": state,
        "total": total,
        "returned": len(rows),
        "offset": offset,
        "sort": sort if sort in SORTS else "priced_rows",
        "sorts": sorted(SORTS),
        "securities": [
            {
                "security_id": int(row.security_id),
                "ticker": row.ticker,
                "name": row.issuer_name,
                "is_current": bool(row.is_current) if row.is_current is not None else None,
                "first_day": row.first_day,
                "last_day": row.last_day,
                "first_session": row.first_session,
                "last_session": row.last_session,
                # Named for what it is: rows recorded on the raw basis. The
                # chart reads a view that additionally excludes a handful of
                # withheld bars, so it can draw marginally fewer.
                "price_rows": int(row.price_rows or 0),
                # How many of those actually traded. A security whose two
                # counts disagree is holding rows that are not prices.
                "priced_rows": int(row.priced_rows or 0),
                "last_close": float(row.last_close) if row.last_close is not None else None,
                "has_fundamentals": bool(row.has_fundamentals),
            }
            for row in rows
        ],
        "note": (
            "ordered by how much the corpus holds, never by anything resembling "
            "attractiveness. This is a directory, not a ranking."
        ),
    }
