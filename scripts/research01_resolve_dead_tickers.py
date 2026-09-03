#!/usr/bin/env python
"""Resolve tickers for dead registrants from their own filings.

**This is the work that moves the survivorship gate.** The gate counts
registrants with a confirmed dated exit that we can price. ``company_tickers.json``
supplies the living and almost none of the dead, so the 7,255 dead registrants in
the XBRL cohort need their symbols from the only source that still holds them:
the filings they made while alive.

Evidence is ``MappingEvidence.FILING_DOCUMENT_TEXT`` -- the registrant's own
annual report saying which symbol its stock trades under, kept verbatim. That
ranks third of six, is **not** in ``_INSUFFICIENT_ALONE``, and is the same
evidence the dot-com cohort's identity rests on.

Seven refusals, because coverage bought by guessing is worth nothing
--------------------------------------------------------------------

1. **The pipeline proves itself before it runs.** A self-test fetches Apple's
   most recent 10-K and requires it to yield ``AAPL`` through the same fetch,
   strip and extract path. This repository has had a "confirmed 0 of 40" that
   was a bug in the extractor rather than a fact about the filings, so a run
   that cannot confirm a known binding does not start.
2. **Exactly one candidate symbol, or nothing.** A filing naming common stock,
   warrants and units names three, and choosing the one that looks like a
   ticker is confirmation bias with a citation attached.
3. **The document is fetched from the registrant's own CIK path**, and the
   SEC's submissions index names which document is primary. An earlier attempt
   guessed the primary document alphabetically and read an RSU award agreement
   and a certification exhibit.
4. **Exchange abbreviations are refused as symbols.** ``symbol TSX: XPL`` binds
   ``XPL``; the regex guard added for that is belt, and this list is braces.
5. **Only annual reports are read.** Item 5 and the Section 12(b) cover table
   are where a registrant states its own symbol.
6. **The interval closes at the registrant's last evidenced activity.** A
   filing evidences the symbol on the day it was filed and says nothing about
   afterwards; leaving it open would let whoever took the symbol next have
   their prices read as this registrant's. ``RESEARCH_01_DATA_CONTRACT`` §
   already records that OTC continuation after delisting is treated as ended,
   so this follows a decision rather than making one.
7. **A mid-life ticker change is not detectable from one filing**, and is not
   guessed at here. ``valid_from`` stays unbounded and unclaimed, and
   ``series_coherence`` and ``adjudicate.py`` are what catch the splice if one
   exists -- they found 63 of 79 in the dot-com cohort.

    EDGAR_USER_AGENT=... PYTHONPATH=src .venv/bin/python \\
      scripts/research01_resolve_dead_tickers.py --limit 50
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.edgar.acquire import extract_identity_evidence, resolve_user_agent, strip_html
from tradeit.research01.confirm import candidate_symbols
from tradeit.storage.tables import Issuer, IssuerIdentifier, Security, SymbolAlias

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
KNOWLEDGE_SOURCE = "edgar_filing_text"

#: Annual reports only. Item 5 and the Section 12(b) cover table are where a
#: registrant states its own symbol; a 10-Q rarely repeats it and an exhibit
#: never does.
ANNUAL = ("10-K", "10-K405", "10-KSB", "10-K/A", "20-F", "40-F")

#: How many annual reports to try before giving up on a registrant. The most
#: recent is preferred because it is the symbol the series ends under.
MAX_ATTEMPTS = 3

#: Never a ticker, however the sentence reads. The `_BOUND_SYMBOL` lookahead
#: already refuses `VENUE: TICKER`; this refuses a bare venue.
NOT_A_SYMBOL = frozenset(
    {
        "NYSE",
        "NASDAQ",
        "AMEX",
        "TSX",
        "TSXV",
        "LSE",
        "ASX",
        "CSE",
        "OTC",
        "OTCBB",
        "OTCQB",
        "OTCQX",
        "ARCA",
        "BATS",
        "NEO",
        "JSE",
        "SEC",
        "USA",
        "US",
        "UK",
        "COMMON",
        "STOCK",
        "SHARES",
        "CLASS",
        "SERIES",
        "NA",
        "NIL",
    }
)

#: SEC fair access. The published ceiling is ten a second; this stays well under.
PAUSE_S = 0.12
MAX_DOCUMENT_BYTES = 30 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Resolution:
    cik: int
    security_id: int
    ticker: str
    accession: str
    filed: dt.date
    citation: str


def _get(url: str, user_agent: str, *, timeout: int = 60) -> bytes | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception:
        return None


def _annual_reports(cik: int, user_agent: str) -> list[tuple[str, str, dt.date]]:
    """(accession, primary document, filed) for this registrant's annual reports.

    The SEC's own submissions index names the primary document, which is the
    only reliable way to find it -- guessing alphabetically reads exhibits.
    """
    raw = _get(SUBMISSIONS.format(cik=cik), user_agent)
    if raw is None:
        return []
    try:
        recent = json.loads(raw)["filings"]["recent"]
    except (KeyError, ValueError):
        return []
    out: list[tuple[str, str, dt.date]] = []
    for index, form in enumerate(recent.get("form", [])):
        if form not in ANNUAL:
            continue
        document = recent["primaryDocument"][index]
        if not document:
            continue
        try:
            filed = dt.date.fromisoformat(recent["filingDate"][index])
        except ValueError:
            continue
        out.append((recent["accessionNumber"][index], document, filed))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def _symbol_from(
    cik: int, accession: str, document: str, user_agent: str
) -> tuple[str, str] | None:
    """The single symbol this filing binds, and the sentence that binds it."""
    url = f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/{document}"
    raw = _get(url, user_agent, timeout=90)
    if raw is None or len(raw) > MAX_DOCUMENT_BYTES:
        return None
    extract = extract_identity_evidence(strip_html(raw.decode("latin-1")))
    symbols = {s for s in candidate_symbols(extract) if s not in NOT_A_SYMBOL}
    if len(symbols) != 1:
        return None
    symbol = symbols.pop()
    for statement in extract.symbol_statements:
        if symbol in statement.text.upper():
            return symbol, statement.text.strip()
    for row in extract.section_12b_rows:
        blob = " ".join(row.cells)
        if symbol in blob.upper():
            return symbol, blob.strip()
    return None


def _self_test(user_agent: str) -> bool:
    """Apple's latest 10-K must yield AAPL, through the same path."""
    reports = _annual_reports(320193, user_agent)
    if not reports:
        print("SELF-TEST: could not list Apple's annual reports")
        return False
    accession, document, filed = reports[0]
    found = _symbol_from(320193, accession, document, user_agent)
    if found is None or found[0] != "AAPL":
        print(f"SELF-TEST FAILED: Apple's {filed} 10-K yielded {found!r}; no run will start")
        return False
    print(f"SELF-TEST passed: {accession} ({filed}) yields AAPL")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--edgar-cache", required=True)
    ap.add_argument("--progress", default=".research01_dead_tickers.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    user_agent = resolve_user_agent()
    if not _self_test(user_agent):
        return 1

    facts = json.loads(Path(args.edgar_cache).read_text())
    exits = {int(k): dt.date.fromisoformat(v) for k, v in facts["exits"].items()}
    last_seen = {int(k): dt.date.fromisoformat(v) for k, v in facts["last_seen"].items()}

    session: Session = sessionmaker(bind=create_engine(args.db, future=True), future=True)()
    have = set(
        session.scalars(
            select(SymbolAlias.security_id).where(SymbolAlias.alias_kind == "ticker")
        ).all()
    )
    targets = [
        (int(value), security_id)
        for value, security_id in session.execute(
            select(IssuerIdentifier.value_normalized, Security.security_id)
            .join(Security, Security.issuer_id == IssuerIdentifier.issuer_id)
            .join(Issuer, Issuer.issuer_id == IssuerIdentifier.issuer_id)
            .where(IssuerIdentifier.namespace == "sec_cik", Issuer.source == "sec_fsds_sub")
        ).all()
        if security_id not in have
    ]
    targets = sorted(t for t in targets if t[0] in exits)
    print(f"dead registrants without a ticker: {len(targets):,}")

    progress = Path(args.progress)
    done = set(json.loads(progress.read_text())) if progress.exists() else set()
    tally: collections.Counter[str] = collections.Counter()
    resolved: list[Resolution] = []
    attempted = 0

    for cik, security_id in targets:
        if str(cik) in done:
            continue
        if args.limit and attempted >= args.limit:
            break
        attempted += 1
        reports = _annual_reports(cik, user_agent)
        time.sleep(PAUSE_S)
        if not reports:
            tally["no_annual_report"] += 1
            done.add(str(cik))
            continue

        found = None
        for accession, document, filed in reports[:MAX_ATTEMPTS]:
            found = _symbol_from(cik, accession, document, user_agent)
            time.sleep(PAUSE_S)
            if found is not None:
                anchor = max(filter(None, (last_seen.get(cik), exits.get(cik))), default=filed)
                resolved.append(
                    Resolution(
                        cik=cik,
                        security_id=security_id,
                        ticker=found[0],
                        accession=accession,
                        filed=filed,
                        citation=(
                            f"symbol bound to CIK {cik} by {accession} filed {filed}: "
                            f'"{found[1][:400]}". Interval start NOT evidenced by the filing; '
                            f"closed at {anchor}, the registrant's last evidenced activity."
                        ),
                    )
                )
                tally["resolved"] += 1
                break
        if found is None:
            tally["not_established"] += 1
        done.add(str(cik))

        if attempted % 50 == 0:
            if not args.dry_run:
                _write(session, resolved, last_seen, exits)
                resolved.clear()
                progress.write_text(json.dumps(sorted(done)))
            print(f"  {attempted:,} attempted  {dict(tally)}", flush=True)

    if not args.dry_run:
        _write(session, resolved, last_seen, exits)
        progress.write_text(json.dumps(sorted(done)))

    print(f"\nattempted {attempted:,}: {dict(tally)}")
    rate = 100 * tally["resolved"] / attempted if attempted else 0.0
    print(f"resolved {tally['resolved']:,} ({rate:.1f}%)")
    print(
        "\nEvery binding carries the filing's own sentence and the accession it came "
        "from. Nothing was resolved from a filing naming more than one symbol."
    )
    return 0


def _write(
    session: Session,
    resolutions: list[Resolution],
    last_seen: dict[int, dt.date],
    exits: dict[int, dt.date],
) -> None:
    now = dt.datetime.now(dt.UTC)
    for row in resolutions:
        anchor = max(filter(None, (last_seen.get(row.cik), exits.get(row.cik))), default=row.filed)
        session.add(
            SymbolAlias(
                security_id=row.security_id,
                alias_kind="ticker",
                alias_value=row.ticker,
                valid_from=dt.date(1990, 1, 1),
                valid_to=anchor,
                knowledge_time=now,
                knowledge_source=KNOWLEDGE_SOURCE,
                citation=row.citation,
                source=KNOWLEDGE_SOURCE,
            )
        )
    session.commit()


if __name__ == "__main__":
    raise SystemExit(main())
