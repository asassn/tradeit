"""The nightly capture agent's health — the only live-updating data here.

Everything else this service reads is a record of something that finished.
`forward.sqlite` is written at 22:10 every night by a launchd agent, and until
now it had **no HTTP surface at all**: its state was reachable only through
`tradeit status` and `tradeit forward ...` on a terminal.

**The question this answers is liveness, and it is not the obvious one.** A
scheduled job does not fail loudly. It stops, and the record simply ceases to
grow — so the number that matters is not a count but the **age of the most
recent ask**. A screen showing 5,176 observations and a healthy green tick
while the agent has been dead for a week is worse than no screen.

Three distinctions this module refuses to collapse, each because conflating
them trains an operator to ignore the warning that matters:

*A silent symbol-session is evidence, not an error.* It is what a delisting is
made of: the vendor returning nothing for a symbol that used to print. Counted
and surfaced, never swept up as a failure.

*An unobserved session is not a hole.* Declining to write 195 silences for a
session the vendor has not yet published is the capture working correctly.

*Interpretable rows are a subset.* `rows_returned` counted the fetch window
rather than the session before 2026-10-23 17:50, so older rows mean something
different and are reported separately rather than summed in.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["SESSION_COUNTS_FROM", "STALE_AFTER_DAYS", "forward_payload"]

#: The instant `rows_returned` began counting the session rather than the
#: fetch window. Rows before it carry different semantics and are counted
#: apart. Mirrors `cli_status.SESSION_COUNTS_FROM`, which is the other reader
#: of this record.
SESSION_COUNTS_FROM = "2026-10-23 17:50"

#: How old the most recent ask may be before the agent is presumed stopped.
#: Four days rather than one: the capture runs on trading sessions, so a
#: Friday-night ask is three days old by Monday morning and nothing is wrong.
#: A fifth day means a session was missed.
STALE_AFTER_DAYS = 4


def forward_payload(session: Session, *, as_of: dt.datetime | None = None) -> dict[str, Any]:
    """The capture agent's state, with liveness first.

    ``as_of`` exists so the age can be tested without freezing the clock
    globally; it defaults to now.
    """
    now = as_of or dt.datetime.now(dt.UTC)

    totals = session.execute(
        text(
            "select count(*) as asks, "
            "       count(distinct symbol) as symbols, "
            "       count(distinct session_date) as sessions, "
            "       max(asked_at) as latest "
            "from forward_observations"
        )
    ).one()

    if totals.asks == 0:
        return {
            "available": True,
            "asks": 0,
            "note": "the forward record is reachable and empty; no capture has run",
        }

    latest_raw = str(totals.latest)
    latest = dt.datetime.fromisoformat(latest_raw)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=dt.UTC)
    age_days = (now - latest).total_seconds() / 86_400

    counts = session.execute(
        text(
            "select "
            "  sum(case when asked_at >= :cut then 1 else 0 end) as interpretable, "
            "  sum(case when asked_at <  :cut then 1 else 0 end) as earlier, "
            "  sum(case when asked_at >= :cut and rows_returned = 0 then 1 else 0 end) "
            "    as silent "
            "from forward_observations"
        ).bindparams(cut=SESSION_COUNTS_FROM)
    ).one()

    recent = session.execute(
        text(
            "select session_date, count(*) as asks, "
            "       sum(case when rows_returned = 0 then 1 else 0 end) as silent "
            "from forward_observations "
            "group by session_date order by session_date desc limit 14"
        )
    ).all()

    return {
        "available": True,
        # Liveness first, because it is the failure mode a scheduled job
        # actually has: it stops, and nothing says so.
        "latest_ask": latest.isoformat(),
        "age_days": round(age_days, 2),
        "stale": age_days > STALE_AFTER_DAYS,
        "stale_after_days": STALE_AFTER_DAYS,
        "liveness_note": (
            f"the most recent ask is {age_days:.1f} days old. A scheduled job does "
            "not crash; it stops, and the record simply ceases to grow — so this "
            "is the number that catches a dead agent, not the totals below"
        ),
        "asks": int(totals.asks),
        "symbols": int(totals.symbols),
        "sessions": int(totals.sessions),
        "interpretable_asks": int(counts.interpretable or 0),
        # Reported apart rather than summed in: these rows counted the fetch
        # window, not the session, so they answer a different question.
        "earlier_asks": int(counts.earlier or 0),
        "earlier_note": (
            f"asks before {SESSION_COUNTS_FROM} counted the fetch window rather "
            "than the session; they are not comparable with the figure above"
        ),
        "silent_observations": int(counts.silent or 0),
        "silent_note": (
            "a silent symbol-session is the vendor returning nothing for a symbol "
            "that used to print. It is evidence — what a delisting is made of — "
            "and not a capture failure"
        ),
        "recent_sessions": [
            {
                "session_date": str(row.session_date),
                "asks": int(row.asks),
                "silent": int(row.silent or 0),
            }
            for row in recent
        ],
    }
