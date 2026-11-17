"""Where one security's facts came from, and what the corpus withholds.

**This is the content that distinguishes `research-01` from a price feed**,
and it had no surface at all. Every ticker interval carries the filing that
evidenced it; every split the corpus could not settle carries the verdict and
who decided it; every withheld bar has a reason. A vendor hands you a series —
this hands you the series and its argument.

Three rules the route encodes rather than leaving to the caller.

*An interval is half-open and `valid_to` is exclusive.* The dictionary calls
reading it as inclusive "trap 0.2", so this reads `v_security_tickers`, which
has the arithmetic done, rather than `symbol_aliases` directly.

*A citation is the point, not metadata.* The intervals are evidenced by filing
text — "Our common shares are quoted on the OTC Bulletin Board under the
symbol AAAB" — and an interval shown without its citation is an assertion. The
full text travels.

*Absence of a split verdict is not absence of doubt.* A verdict row exists
only where a split was **disputed**; a security with none may have no splits
at all. The payload says which, rather than letting an empty list imply a
clean bill of health.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["ACTION_SAMPLE", "provenance_payload"]

#: How many individual corporate actions to return. The counts and the date
#: range are the answer for a security with 81 dividends; the list is for
#: checking a specific one, and an unbounded list would be a download.
ACTION_SAMPLE = 40


def provenance_payload(session: Session, security_id: int) -> dict[str, Any]:
    """One security's identity history, corporate actions and disputes."""
    tickers = session.execute(
        text(
            "select ticker, first_day, last_day, is_current, evidence, citation "
            "from v_security_tickers where security_id = :sid "
            "order by first_day"
        ).bindparams(sid=security_id)
    ).all()

    issuer = session.execute(
        text(
            "select i.display_name, i.source "
            "from securities s join issuers i on i.issuer_id = s.issuer_id "
            "where s.security_id = :sid"
        ).bindparams(sid=security_id)
    ).first()

    action_counts = session.execute(
        text(
            "select action_type, count(*) as n, "
            "       min(ex_date) as first_seen, max(ex_date) as last_seen "
            "from security_corporate_action_facts where security_id = :sid "
            "group by action_type order by n desc"
        ).bindparams(sid=security_id)
    ).all()

    actions = session.execute(
        text(
            "select ex_date, action_type, ratio, cash_amount "
            "from security_corporate_action_facts where security_id = :sid "
            "order by ex_date desc limit :n"
        ).bindparams(sid=security_id, n=ACTION_SAMPLE)
    ).all()

    verdicts = session.execute(
        text(
            "select ex_date, verdict, decided_by, stored_raw_close, "
            "       vendor_printed_close, vendor_adjusted_close, citation "
            "from security_split_price_verdicts where security_id = :sid "
            "order by ex_date desc"
        ).bindparams(sid=security_id)
    ).all()

    splits = next((row.n for row in action_counts if row.action_type == "split"), 0)

    return {
        "security_id": security_id,
        "issuer": (
            None
            if issuer is None
            else {"name": str(issuer.display_name), "source": str(issuer.source)}
        ),
        "tickers": [
            {
                "ticker": str(row.ticker),
                "first_day": str(row.first_day),
                # Already exclusive-corrected by the view. Reading the stored
                # `valid_to` as inclusive is trap 0.2 in the data dictionary.
                "last_day": str(row.last_day) if row.last_day else None,
                "is_current": bool(row.is_current),
                "evidence": str(row.evidence),
                # The whole citation, never truncated: an interval without its
                # evidence is an assertion, and the evidence is frequently the
                # most interesting thing on the page.
                "citation": str(row.citation) if row.citation else None,
            }
            for row in tickers
        ],
        "ticker_note": (
            "an interval evidenced by `sec_company_tickers` proves the CURRENT "
            "holding only; its start is not evidenced and is left open "
            "deliberately, so a 1990 first_day there is a floor, not a finding"
        ),
        "action_counts": [
            {
                "action_type": str(row.action_type),
                "count": int(row.n),
                "first": str(row.first_seen),
                "last": str(row.last_seen),
            }
            for row in action_counts
        ],
        "actions": [
            {
                "ex_date": str(row.ex_date),
                "action_type": str(row.action_type),
                "ratio": None if row.ratio is None else float(row.ratio),
                "cash_amount": None if row.cash_amount is None else float(row.cash_amount),
            }
            for row in actions
        ],
        "actions_truncated": len(actions) == ACTION_SAMPLE,
        "split_verdicts": [
            {
                "ex_date": str(row.ex_date),
                "verdict": str(row.verdict),
                "decided_by": str(row.decided_by),
                "stored_raw_close": (
                    None if row.stored_raw_close is None else float(row.stored_raw_close)
                ),
                "vendor_printed_close": (
                    None if row.vendor_printed_close is None else float(row.vendor_printed_close)
                ),
                "vendor_adjusted_close": (
                    None if row.vendor_adjusted_close is None else float(row.vendor_adjusted_close)
                ),
                "citation": str(row.citation) if row.citation else None,
            }
            for row in verdicts
        ],
        # An empty verdict list means one of two different things, and the
        # difference matters: no split was ever disputed, or there was no
        # split to dispute. Saying which stops absence reading as assurance.
        "split_verdict_note": (
            "no split was disputed for this security"
            if verdicts == [] and splits
            else "this security records no split at all, so there was none to dispute"
            if verdicts == []
            else "a verdict row exists only where a split was disputed; these are "
            "the ones the corpus had to settle"
        ),
    }
