"""What this issuer filed, and whether the record of it is complete.

**Completeness is the question this route exists to answer**, not the list.
The corpus holds 8.26M filing-index rows, and `filings` is seeded from more
than one place: 22,927 issuers come from the EDGAR full index, 17,000 from
`sec_fsds_sub`, 858 from filing text and 34 from control evidence. The data
dictionary records that the index seeder skipped issuers already held, so the
XBRL cohort's filing history can stop before the issuer did.

So the payload reports the **issuer's source** and compares the last filing
with the last session the security actually traded. Measured 2026-11-17 across
price-active securities, filings stopping more than a year before trading
ended: 24 of 11,635 for `sec_fsds_sub`, 28 of 4,889 for `edgar_full_index`,
**93 of 858 for `edgar_filing_text`** and 0 of 23 for the controls. Rare
overall, concentrated in one cohort, and worth knowing per security rather
than assumed from the cohort.

A filing list that ends in 2009 for a company that traded until 2014 is not a
company that stopped filing; it is a record that stops, and those are
different facts.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["RECENT", "STOPS_EARLY_DAYS", "filings_payload"]

#: Individual filings returned. The counts by form answer "what does this
#: issuer file"; the list answers "what did it file lately", and an unbounded
#: one would be a download of 716 rows for Apple alone.
RECENT = 60

#: How far the last filing may fall short of the last traded session before it
#: is reported. A year, because an annual filer legitimately goes eleven
#: months between filings and a tighter bound would flag every one of them.
STOPS_EARLY_DAYS = 365


def filings_payload(session: Session, security_id: int) -> dict[str, Any]:
    """One issuer's filing record, with its completeness stated."""
    issuer = session.execute(
        text(
            "select i.issuer_id, i.display_name, i.source "
            "from securities s join issuers i on i.issuer_id = s.issuer_id "
            "where s.security_id = :sid"
        ).bindparams(sid=security_id)
    ).first()
    if issuer is None:
        return {
            "security_id": security_id,
            "available": False,
            "note": "no issuer is recorded for this security",
        }

    forms = session.execute(
        text(
            "select form_type, count(*) as n, min(filed_at) as first_filed, "
            "       max(filed_at) as last_filed "
            "from filings where issuer_id = :iid "
            "group by form_type order by n desc"
        ).bindparams(iid=issuer.issuer_id)
    ).all()

    if not forms:
        return {
            "security_id": security_id,
            "available": False,
            "issuer": {"name": str(issuer.display_name), "source": str(issuer.source)},
            "note": (
                "no filings are recorded for this issuer. `filings` holds "
                "company-filed forms only and is seeded from more than one place; "
                "an issuer can be known without its index having been ingested"
            ),
        }

    recent = session.execute(
        text(
            "select accession, form_type, filed_at, period_end "
            "from filings where issuer_id = :iid "
            "order by filed_at desc limit :n"
        ).bindparams(iid=issuer.issuer_id, n=RECENT)
    ).all()

    total = sum(int(row.n) for row in forms)
    last_filed = max(str(row.last_filed) for row in forms)
    first_filed = min(str(row.first_filed) for row in forms)

    # The comparison that turns a list into an answer about completeness.
    traded = session.execute(
        text("select last_session from security_summary where security_id = :sid").bindparams(
            sid=security_id
        )
    ).first()
    last_session = str(traded.last_session) if traded and traded.last_session else None

    stops_early = None
    if last_session:
        gap = (
            session.execute(
                text("select julianday(:t) - julianday(:f) as gap").bindparams(
                    t=last_session, f=last_filed
                )
            ).scalar_one()
            or 0
        )
        if gap > STOPS_EARLY_DAYS:
            stops_early = (
                f"the last filing is {last_filed} and the security traded until "
                f"{last_session} — {int(gap)} days later. A record that stops is "
                "not a company that stopped filing, and only the first is "
                "established here"
            )

    return {
        "security_id": security_id,
        "available": True,
        "issuer": {"name": str(issuer.display_name), "source": str(issuer.source)},
        "total": total,
        "first_filed": first_filed,
        "last_filed": last_filed,
        "last_session": last_session,
        "stops_early": stops_early,
        "source_note": _SOURCE_NOTES.get(str(issuer.source), _SOURCE_NOTES["unknown"]),
        "forms": [
            {
                "form_type": str(row.form_type),
                "count": int(row.n),
                "first": str(row.first_filed),
                "last": str(row.last_filed),
            }
            for row in forms
        ],
        "recent": [
            {
                "accession": str(row.accession),
                "form_type": str(row.form_type),
                "filed_at": str(row.filed_at),
                "period_end": str(row.period_end) if row.period_end else None,
            }
            for row in recent
        ],
        "truncated": total > RECENT,
        "scope_note": (
            "company-filed forms only. EDGAR lists an ownership form once per "
            "filer — a Form 4 appears under both the reporting owner and the "
            "subject — and the corpus resolves such filings to the subject, so "
            "they are not counted here as the issuer's own"
        ),
    }


#: What each seeding route implies about completeness, in the reader's terms.
_SOURCE_NOTES: dict[str, str] = {
    "edgar_full_index": (
        "seeded from the EDGAR full index, which is the complete quarterly record from 1994 Q3"
    ),
    "sec_fsds_sub": (
        "seeded from the SEC Financial Statement Data Sets. The index seeder "
        "skipped issuers already held, so this issuer's filing history can stop "
        "before the issuer did — compare the dates above against its trading"
    ),
    "edgar_filing_text": (
        "seeded from filing text. Measured 2026-11-17, this is the cohort where "
        "filing records most often stop early: 93 of 858 price-active securities "
        "have a last filing more than a year before their last session"
    ),
    "control_identity_evidence": (
        "one of the 30 control securities, whose identity was confirmed against "
        "primary regulatory filings by a person"
    ),
    "unknown": "the seeding route for this issuer is not recorded",
}
