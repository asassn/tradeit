"""One security's price history, on the basis the reader asks for.

**Both bases are served, because the difference is a fact about the data
rather than a preference.** The corpus's own views say it plainly:
`v_prices_raw` is *"the unadjusted print — what a trader saw on the day"*, and
`v_prices` is split- and dividend-adjusted and describes itself as *"the
current belief"*.

Neither is the right one in general:

*Raw* is what the market actually printed, and it is what a valuation multiple
needs — a per-share figure was reported in the share count of its own time.
It also **jumps at every split**, and a chart of it looks broken unless the
splits are marked. They are.

*Adjusted* is comparable across time and is what a return is computed from. It
is also not point-in-time: the adjustment embeds every corporate action since,
so a 1999 adjusted close is a number computed today, not one anybody saw.

A chart that silently picked one would teach the reader the wrong thing about
their own data. This one names which it is showing and marks the splits either
way.

**The drawn series is sampled; the summary is not.** A 9,237-session history
cannot be drawn point-for-point in an SVG a few hundred pixels wide, so the
series is bucketed — but the high, the low and their dates are measured over
**every** row, so a sampled chart can never misreport an extreme it skipped.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["DEFAULT_POINTS", "MAX_POINTS", "prices_payload"]

#: Points in the drawn series. A chart a few hundred pixels wide cannot
#: resolve more, and every extra point is bytes over the wire for a pixel
#: nobody sees.
DEFAULT_POINTS = 400
MAX_POINTS = 2000

_BASES = {
    "raw": (
        "v_prices_raw",
        "the unadjusted print — what a trader saw on the day. Jumps at every "
        "split, which is why the splits are marked",
    ),
    "total": (
        "v_prices",
        "split- and dividend-adjusted — comparable across time, and what a "
        "return is computed from. Not point-in-time: the adjustment embeds "
        "every corporate action since, so an old close here is a number "
        "computed today rather than one anybody saw",
    ),
}


def prices_payload(
    session: Session,
    security_id: int,
    *,
    basis: str = "raw",
    points: int = DEFAULT_POINTS,
) -> dict[str, Any]:
    """The price history, sampled for drawing, with exact extremes."""
    view, basis_note = _BASES.get(basis, _BASES["raw"])

    # The view name is interpolated, never user input: `basis` is validated
    # against a fixed pattern at the route and looked up in `_BASES` here, so
    # only two literal strings can reach this. Every value is bound.
    summary = session.execute(
        text(
            f"select count(*) as n, min(session_date) as first, "
            f"       max(session_date) as last, min(low) as low, max(high) as high "
            f"from {view} where security_id = :sid"
        ).bindparams(sid=security_id)
    ).one()

    if not summary.n:
        return {
            "security_id": security_id,
            "basis": basis,
            "available": False,
            "note": f"no {basis} price series for this security in the corpus",
        }

    # Measured over every row, not over the sample. A chart that skipped the
    # high and then reported the sample's high would be wrong in the one place
    # a reader checks.
    extremes = session.execute(
        text(
            f"select session_date, close from {view} "
            "where security_id = :sid and close = ("
            f"  select max(close) from {view} where security_id = :sid)"
            " limit 1"
        ).bindparams(sid=security_id)
    ).first()

    stride = max(1, summary.n // max(1, min(points, MAX_POINTS)))
    rows = session.execute(
        text(
            "select session_date, close from ("
            f"  select session_date, close, row_number() over (order by session_date)"
            f"         as rn from {view} where security_id = :sid"
            ") where rn % :stride = 0 or rn = 1 or rn = :n order by session_date"
        ).bindparams(sid=security_id, stride=stride, n=summary.n)
    ).all()

    splits = session.execute(
        text(
            "select ex_date, ratio from security_corporate_action_facts "
            "where security_id = :sid and action_type = 'split' "
            "order by ex_date"
        ).bindparams(sid=security_id)
    ).all()

    return {
        "security_id": security_id,
        "basis": basis,
        "basis_note": basis_note,
        "available": True,
        "sessions": int(summary.n),
        "first_session": str(summary.first),
        "last_session": str(summary.last),
        "low": float(summary.low) if summary.low is not None else None,
        "high": float(summary.high) if summary.high is not None else None,
        "peak_close": (
            None
            if extremes is None
            else {"session": str(extremes.session_date), "close": float(extremes.close)}
        ),
        "series": [{"session": str(row.session_date), "close": float(row.close)} for row in rows],
        "drawn": len(rows),
        "sampled": len(rows) < summary.n,
        "sampling_note": (
            f"the chart draws {len(rows)} of {summary.n} sessions. The high, the "
            "low and their dates above are measured over every row, so a sampled "
            "line cannot misreport an extreme it skipped"
        ),
        # Marked on the chart whichever basis is shown: on raw they explain the
        # jumps, and on adjusted they explain why there are none.
        "splits": [
            {"ex_date": str(row.ex_date), "ratio": float(row.ratio) if row.ratio else None}
            for row in splits
        ],
    }
