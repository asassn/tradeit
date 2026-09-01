#!/usr/bin/env python
"""Confirm dot-com tickers from filings, and write the identities that survive.

Three stages, each refusing to do the next one's job:

1. **Locate** a 10-K per cohort CIK from the local full-index. No network.
2. **Confirm** the ticker against that filing's own words. A filing that names
   no symbol, a different symbol, or several symbols yields nothing.
3. **Write** an issuer, a security and a ticker alias for each confirmation.

**This creates identity, and that is deliberate rather than a lapse.** The
importer may never create identity, because a vendor price file is not evidence
about who a company is. A registrant's own annual report *is*:
``MappingEvidence.FILING_DOCUMENT_TEXT`` ranks above ``NAME_MATCH`` and is not
in ``_INSUFFICIENT_ALONE``. Every issuer written here carries the filing's
sentence and accession as its citation, so any row can be walked back to the
document that produced it.

**It writes RESOLVED, never MANUAL_VERIFIED.** A program that reads a filing has
not satisfied the rule that a person read it.

Resumable: progress is checkpointed per CIK, because 1,887 fetches is long
enough that an interruption should not cost the run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.edgar.acquire import (
    extract_identity_evidence,
    resolve_user_agent,
    strip_html,
)
from tradeit.research01.confirm import Confirmation, confirm_ticker
from tradeit.storage.tables import (
    Base,
    Issuer,
    IssuerIdentifier,
    Security,
    SymbolAlias,
)

SEC = "https://www.sec.gov/Archives/"


def _write_identity(session: Session, c: Confirmation, vendor_name: str) -> bool:
    """One issuer, one security, one filing-evidenced ticker alias. Idempotent."""
    normalized = str(c.cik)
    if session.scalars(
        select(IssuerIdentifier.issuer_id).where(
            IssuerIdentifier.namespace == "sec_cik",
            IssuerIdentifier.value_normalized == normalized,
        )
    ).first():
        return False

    issuer = Issuer(
        display_name=vendor_name or c.ticker, note=f"dotcom/{c.ticker}", source="edgar_filing_text"
    )
    session.add(issuer)
    session.flush()
    session.add(
        IssuerIdentifier(
            issuer_id=issuer.issuer_id,
            namespace="sec_cik",
            value=normalized,
            value_normalized=normalized,
            role="primary",
            citation=f"{c.accession}: {c.citation}",
            source="edgar_filing_text",
        )
    )
    security = Security(
        issuer_id=issuer.issuer_id,
        security_type="common_stock",
        currency="USD",
        note=f"dotcom/{c.ticker}",
        source="edgar_filing_text",
    )
    session.add(security)
    session.flush()
    session.add(
        SymbolAlias(
            security_id=security.security_id,
            alias_kind="ticker",
            alias_value=c.ticker,
            # The FILING evidences which company holds the symbol. It does not
            # give an interval, so this one is open-ended and the citation says
            # which half of the claim rests on what. A narrower interval would
            # be a vendor span wearing a filing's authority.
            valid_from=dt.date(1990, 1, 1),
            valid_to=None,
            knowledge_time=dt.datetime.now(dt.UTC),
            knowledge_source="edgar_filing_text",
            citation=(
                f'symbol bound to CIK {c.cik} by {c.accession}: "{c.citation}". '
                "Interval NOT evidenced by the filing; open-ended deliberately."
            ),
            source="edgar_filing_text",
        )
    )
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filings", required=True, help="JSON produced by the cohort locator")
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--progress", default=".research01_confirm.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    filings = json.loads(Path(args.filings).read_text())
    progress = Path(args.progress)
    done = set(json.loads(progress.read_text())) if progress.exists() else set()

    engine = create_engine(args.db, future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    ua = resolve_user_agent()

    stats = {
        "confirmed": 0,
        "not_established": 0,
        "fetch_failed": 0,
        "written": 0,
        "skipped_done": 0,
        "existing_issuer": 0,
    }
    for i, (cik, meta) in enumerate(filings.items()):
        if cik in done:
            stats["skipped_done"] += 1
            continue
        if args.limit and stats["confirmed"] + stats["not_established"] >= args.limit:
            break
        try:
            req = urllib.request.Request(SEC + meta["path"], headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=90) as r:
                text = strip_html(r.read().decode("latin-1"))
        except Exception:
            stats["fetch_failed"] += 1
            continue
        c = confirm_ticker(
            ticker=meta["ticker"],
            cik=int(cik),
            accession=meta["accession"],
            extract=extract_identity_evidence(text),
        )
        if c is None:
            stats["not_established"] += 1
        else:
            stats["confirmed"] += 1
            if _write_identity(session, c, meta.get("name", "")):
                stats["written"] += 1
            else:
                stats["existing_issuer"] += 1
        done.add(cik)
        if i % 25 == 0:
            session.commit()
            progress.write_text(json.dumps(sorted(done)))
            print(f"  {i:>5}/{len(filings)}  {json.dumps(stats)}", flush=True)
        time.sleep(0.12)  # SEC fair access

    session.commit()
    progress.write_text(json.dumps(sorted(done)))
    print(f"FINAL {json.dumps(stats)}")
    print(
        json.dumps(
            {
                "issuers": session.scalar(select(func.count()).select_from(Issuer)),
                "securities": session.scalar(select(func.count()).select_from(Security)),
                "ticker_aliases": session.scalar(
                    select(func.count())
                    .select_from(SymbolAlias)
                    .where(SymbolAlias.alias_kind == "ticker")
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
