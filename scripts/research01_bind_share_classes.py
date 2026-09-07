#!/usr/bin/env python
"""One security per share class, labelled from the registrant's own cover page.

**The gap this closes.** ``research01_bind_current_tickers.py`` refuses any CIK
the SEC lists with more than one ticker, because the corpus held **one
placeholder security per issuer with a NULL class_label** and binding two
classes to one row would conflate two securities that trade at different
prices. The refusal is right; the model was incomplete. 1,158 registrants sat
in that state, Alphabet and Berkshire Hathaway among them.

**What evidences a class.** Not the vendor, and not a guess from the ticker's
shape. Every Exchange Act cover page states its registered securities as a
table -- title of each class, trading symbol, exchange -- and that table is the
registrant saying which symbol belongs to which class. ``GOOGL`` is Class A
Common Stock because Alphabet's own 10-K says so on the line that names both.

**Two independent sources must agree.** A symbol is bound only when the SEC's
``company_tickers.json`` lists it for that CIK *and* the registrant's own cover
page names it. Either alone has failed before: the SEC file says nothing about
classes, and a cover page names registered notes that never trade.

**Nothing is replaced.** The existing placeholder security is left exactly as
it is, because it carries the issuer's fundamentals -- 11,224 facts for
Alphabet alone -- and those are issuer-level financials that belong to no
single class. Class securities are added beside it. The consequence, recorded
rather than solved here: joining a class's prices to its issuer's fundamentals
goes through ``issuer_id``, not ``security_id``.

    EDGAR_USER_AGENT=... PYTHONPATH=src .venv/bin/python \\
        scripts/research01_bind_share_classes.py --dry-run
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.edgar.acquire import extract_identity_evidence, resolve_user_agent, strip_html
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import IssuerIdentifier, Security, SymbolAlias

DEFAULT_TICKERS = "/Users/ericsasson/Documents/TradeItData/edgar/reference/company_tickers.json"
SOURCE = "edgar_filing_text"

#: The em dash a cover page puts in the symbol column for a registered security
#: that does not trade. A row carrying one is a note, not a share class.
NOT_TRADED = {"\u2014", "\u2013", "-", "", "N/A", "None"}

#: A class title is a phrase like "Class A Common Stock, $0.001 par value".
#: These words disqualify a row: it is debt, and debt is not a share class.
DEBT_WORDS = re.compile(r"\bnotes?\b|\bdebentures?\b|\bbonds?\b|%", re.IGNORECASE)


def _plain(symbol: str) -> str:
    """A symbol with its class punctuation removed, **for comparison only**.

    ``BRK.A`` and ``BRK-A`` are one security written two ways; storing either
    as though it were the other is how a corpus acquires a symbol no vendor
    serves. This is the same distinction ``confirm.comparison_form`` draws for
    vendor disambiguators, and it is drawn here for the same reason.
    """
    return re.sub(r"[.\-]", "", symbol.strip().upper())


def _annual_reports(cik: int, user_agent: str) -> list[tuple[str, str, dt.date]]:
    """(accession, primary document, filed) newest first."""
    import urllib.request

    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=60) as response:
            recent = json.loads(response.read())["filings"]["recent"]
    except Exception:
        return []
    wanted = {"10-K", "10-K405", "10-KSB", "20-F", "40-F"}
    out: list[tuple[str, str, dt.date]] = []
    for index, form in enumerate(recent.get("form", [])):
        if form not in wanted:
            continue
        try:
            filed = dt.date.fromisoformat(recent["filingDate"][index])
        except ValueError:
            continue
        out.append(
            (recent["accessionNumber"][index], recent["primaryDocument"][index] or "", filed)
        )
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def _classes(cik: int, accession: str, document: str, user_agent: str) -> list[tuple[str, str]]:
    """(symbol, class title) for every *traded* share class the cover names."""
    import urllib.request

    stem = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    url = f"{stem}/{document}" if document else f"{stem}/{accession}.txt"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
    except Exception:
        return []
    extract = extract_identity_evidence(strip_html(raw.decode("latin-1")))
    found: list[tuple[str, str]] = []
    for row in extract.section_12b_rows:
        if len(row.cells) < 2:
            continue
        title, symbol = row.cells[0].strip(), row.cells[1].strip()
        if symbol in NOT_TRADED or DEBT_WORDS.search(title):
            continue
        found.append((symbol.upper(), title))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--shard", default="", metavar="I/N")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    user_agent = resolve_user_agent(None, None, env_file=Path(".env"))
    raw = json.loads(Path(args.tickers).read_text())
    entries = raw.values() if isinstance(raw, dict) else raw
    sec_tickers: dict[int, set[str]] = collections.defaultdict(set)
    for entry in entries:
        sec_tickers[int(entry["cik_str"])].add(str(entry["ticker"]).upper())

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    held = {
        int(value): issuer_id
        for value, issuer_id in session.execute(
            select(IssuerIdentifier.value_normalized, IssuerIdentifier.issuer_id).where(
                IssuerIdentifier.namespace == "sec_cik"
            )
        ).all()
    }
    bound = {
        value.upper()
        for value in session.scalars(
            select(SymbolAlias.alias_value).where(SymbolAlias.alias_kind == "ticker")
        ).all()
    }

    targets = sorted(
        cik for cik, symbols in sec_tickers.items() if len(symbols) > 1 and cik in held
    )
    print(f"CIKs the SEC lists with several tickers, held here: {len(targets):,}")
    if args.shard:
        index, _, count = args.shard.partition("/")
        i, n = int(index), int(count)
        targets = [c for c in targets if c % n == i]
        print(f"  shard {i}/{n}: {len(targets):,}")
    if args.limit:
        targets = targets[: args.limit]

    tally: collections.Counter[str] = collections.Counter()
    created = 0
    for position, cik in enumerate(targets, 1):
        reports = _annual_reports(cik, user_agent)
        time.sleep(0.12)
        if not reports:
            tally["no_annual_report"] += 1
            continue
        accession, document, filed = reports[0]
        pairs = _classes(cik, accession, document, user_agent)
        time.sleep(0.12)
        # Both sources must name the symbol: the SEC's file for this CIK, and
        # the registrant's own cover page. They render punctuation differently
        # -- Berkshire's 10-K writes "BRK.A" and company_tickers.json writes
        # "BRK-A" -- so the comparison ignores it and **the stored value does
        # not**. EODHD serves BRK-A and not BRK.A, so the SEC's rendering is
        # what can be priced; the filing's rendering is quoted verbatim in the
        # citation, where it is evidence rather than a key.
        by_plain = {_plain(symbol): symbol for symbol in sec_tickers[cik]}
        agreed = [
            (by_plain[_plain(symbol)], title)
            for symbol, title in pairs
            if _plain(symbol) in by_plain
        ]
        quoted = {by_plain[_plain(s)]: s for s, _ in pairs if _plain(s) in by_plain}
        if len(agreed) < 2:
            tally["cover_does_not_resolve_the_classes"] += 1
            continue
        for symbol, title in agreed:
            if symbol in bound:
                tally["already_bound"] += 1
                continue
            if args.dry_run:
                if created < 12:
                    print(f"    CIK {cik:<9} {symbol:<8} <- {title[:58]!r}")
                created += 1
                tally["would_create"] += 1
                continue
            security = Security(
                issuer_id=held[cik],
                security_type="common_stock",
                class_label=title[:128],
                currency="USD",
                note="share class evidenced by the registrant's Section 12(b) cover table",
                source=SOURCE,
            )
            session.add(security)
            session.flush()
            session.add(
                SymbolAlias(
                    security_id=security.security_id,
                    alias_kind="ticker",
                    alias_value=symbol,
                    valid_from=dt.date(1990, 1, 1),
                    valid_to=None,
                    knowledge_time=dt.datetime.now(dt.UTC),
                    knowledge_source=SOURCE,
                    citation=(
                        f"Section 12(b) cover table of {accession} filed {filed} by CIK {cik}: "
                        f'"{title}" trades as "{quoted.get(symbol, symbol)}". '
                        f"Stored as {symbol!r}, the rendering the SEC's "
                        "company_tickers.json uses for this CIK; the filing's own "
                        "rendering is quoted above and is not normalised away."
                    ),
                    source=SOURCE,
                )
            )
            bound.add(symbol)
            created += 1
            tally["created"] += 1
        if not args.dry_run and position % 25 == 0:
            session.commit()
            print(f"  {position:,}/{len(targets):,}  {dict(tally)}", flush=True)
    if not args.dry_run:
        session.commit()
    print(f"\n{dict(tally)}")
    print(f"class securities {'that would be created' if args.dry_run else 'created'}: {created:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
