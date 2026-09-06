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

The prune, and the defect that made it necessary
------------------------------------------------

A filing states a symbol; it does not state that the symbol is **its own**. Four
Clayton Williams drilling partnerships each filed a 10-K describing their
sponsor -- *"CWEI is an oil and gas company based in Midland, Texas, and its
common stock is traded ... under the symbol CWEI"* -- and five PDC Energy
partnerships each said *"The common stock of **PDC** is traded ..."*. Operating
partnerships quote their general partner: *"The **General Partner's** common
stock is listed for trading on the NYSE"*. Every one of those is a true sentence
about somebody else.

**The discriminator is not how many registrants claim a symbol -- it is whether
they claim it in the same words.** Two registrants holding one symbol a decade
apart is ticker reuse, which this corpus exists to represent: ``ALTR`` was
Altera until 2015 and Altair afterwards, and both bindings are right. Measured
over the 3,814 bindings, the shared symbols split cleanly: 104 pairs with
*different* sentences, spread three to fourteen years apart, and 28 groups whose
sentences are **character-identical**, which means the same document language
was copied and at most one filer is its subject.

So two rules, both fail-closed:

* a symbol claimed by **three or more** registrants is refused for all of them;
* a symbol claimed by registrants using an **identical sentence** is refused for
  all of them.

Neither guesses which claimant is right, because nothing here can tell.

    EDGAR_USER_AGENT=... PYTHONPATH=src .venv/bin/python \\
      scripts/research01_resolve_dead_tickers.py --limit 50
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.edgar.acquire import extract_identity_evidence, resolve_user_agent, strip_html
from tradeit.edgar.evidence import ReportingRegime, reporting_regime
from tradeit.research01.confirm import candidate_symbols
from tradeit.research01.registered_classes import (
    RegisteredClasses,
    read_registered_classes,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import (
    Filing,
    Issuer,
    IssuerIdentifier,
    Security,
    SymbolAlias,
)

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
KNOWLEDGE_SOURCE = "edgar_filing_text"

#: Annual reports only. Item 5 and the Section 12(b) cover table are where a
#: registrant states its own symbol; a 10-Q rarely repeats it and an exhibit
#: never does.
ANNUAL = ("10-K", "10-K405", "10-KSB", "10-K/A", "20-F", "40-F")

#: The issuer's **own** offering prospectus, read only when no annual report
#: binds a symbol. Its cover states where the stock is listed and under what
#: symbol -- verified 2026-09-06 on a 1994 424B1: *"The Company's Class A common
#: stock is listed on the New York Stock Exchange, Inc. under the symbol
#: \"HFI.\""*
#:
#: **424B2, 424B3 and 424B5 are deliberately excluded.** They are resale, merger
#: and shelf-takedown documents that describe *other parties*: one sampled
#: 424B3 yielded LPS, FNF and BKFS, none necessarily the filer's. A filing
#: naming several symbols already fails closed here; the hazard is the one
#: naming exactly one that belongs to somebody else, which binds silently and
#: wrongly. Restricting to the issuer's own offering is what removes it.
OFFERING = ("424B1", "424B4", "424A")

#: Offering prospectuses are large and the symbol is on the cover, so the
#: earliest is preferred -- it is the offering that put the stock on the tape.
MAX_OFFERING_ATTEMPTS = 2

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

#: Pulls the filing's own sentence back out of a stored citation. **A citation
#: that does not parse yields a value unique to its row**, so an unreadable
#: citation can never be mistaken for a match with another -- the first version
#: split on a delimiter that is not in the format and silently compared whole
#: citations, which differ by CIK and so never matched at all. It reported zero
#: identical sentences over data known to contain twenty-eight groups of them.
_QUOTED = re.compile(r'filed \d{4}-\d\d-\d\d: "(.*?)"\. Interval', re.S)


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


def _annual_reports(
    cik: int, user_agent: str, forms: tuple[str, ...] = ANNUAL
) -> list[tuple[str, str, dt.date]]:
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
        if form not in forms:
            continue
        # An empty primaryDocument is not an absent filing. Every pre-2001
        # submission is a single .txt with no primary document named, so
        # skipping these reported "no annual report" for registrants that
        # filed several -- measured 2026-09-06: 3,488 untickered dead
        # registrants have their latest annual report before 2001. The empty
        # string is carried through and _symbol_from falls back to the
        # complete submission text file.
        document = recent["primaryDocument"][index] or ""
        try:
            filed = dt.date.fromisoformat(recent["filingDate"][index])
        except ValueError:
            continue
        out.append((recent["accessionNumber"][index], document, filed))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


@dataclass(frozen=True, slots=True)
class DocumentRead:
    """What one filing yielded: a bound symbol, and what its cover declares.

    The two travel together because they come from the same parse. Reading the
    document twice to answer them separately would double the cost of the
    slowest step in the run for no gain.
    """

    symbol: str | None
    statement: str
    classes: RegisteredClasses


def _symbol_from(cik: int, accession: str, document: str, user_agent: str) -> DocumentRead | None:
    """Read one filing. ``None`` only when it could not be fetched or parsed.

    A read that finds no symbol is still a result — its ``classes`` may say the
    registrant declared no registered securities at all, which closes the
    question rather than leaving it open.
    """
    stem = f"{ARCHIVES}/{cik}/{accession.replace('-', '')}"
    # The complete submission is the fallback, never the preference: it
    # concatenates every exhibit, so a symbol in an exhibit could be read as
    # the filing's own statement. It is used only where the SEC names no
    # primary document, which is every pre-2001 filing.
    url = f"{stem}/{document}" if document else f"{stem}/{accession}.txt"
    raw = _get(url, user_agent, timeout=90)
    if raw is None or len(raw) > MAX_DOCUMENT_BYTES:
        return None
    text = strip_html(raw.decode("latin-1"))
    classes = read_registered_classes(text)
    extract = extract_identity_evidence(text)
    symbols = {s for s in candidate_symbols(extract) if s not in NOT_A_SYMBOL}
    if len(symbols) != 1:
        # Ambiguous or silent. The cover-page verdict is still worth carrying:
        # "declared no registered class" is a finding, not a failure.
        return DocumentRead(symbol=None, statement="", classes=classes)
    symbol = symbols.pop()
    for statement in extract.symbol_statements:
        if symbol in statement.text.upper():
            return DocumentRead(symbol, statement.text.strip(), classes)
    for row in extract.section_12b_rows:
        blob = " ".join(row.cells)
        if symbol in blob.upper():
            return DocumentRead(symbol, blob.strip(), classes)
    return DocumentRead(symbol=None, statement="", classes=classes)


def _self_test(user_agent: str) -> bool:
    """Apple's latest 10-K must yield AAPL, through the same path."""
    reports = _annual_reports(320193, user_agent)
    if not reports:
        print("SELF-TEST: could not list Apple's annual reports")
        return False
    accession, document, filed = reports[0]
    found = _symbol_from(320193, accession, document, user_agent)
    if found is None or found.symbol != "AAPL":
        print(f"SELF-TEST FAILED: Apple's {filed} 10-K yielded {found!r}; no run will start")
        return False
    print(f"SELF-TEST passed: {accession} ({filed}) yields AAPL")

    # The offering route needs its own proof. It reaches a different form set
    # through a different document path -- these filings name no primary
    # document, so they exercise the complete-submission fallback as well.
    # CIK 786617's 1994 and 1995 offering prospectuses both state: "The
    # Company's Class A common stock is listed on the New York Stock Exchange
    # under the symbol \"HFI\"".
    offerings = _annual_reports(786617, user_agent, OFFERING)
    if not offerings:
        print("SELF-TEST: could not list CIK 786617's offering prospectuses")
        return False
    accession, document, filed = offerings[-1]
    found = _symbol_from(786617, accession, document, user_agent)
    if found is None or found.symbol != "HFI":
        print(f"SELF-TEST FAILED: the {filed} 424B1 yielded {found!r}; no run will start")
        return False
    print(f"SELF-TEST passed: {accession} ({filed}) yields HFI via the offering route")
    return True


def _fund_only(security_ids: set[int], session: Session) -> set[int]:
    """Securities whose issuer reports only under the Investment Company Act.

    One grouped query rather than a lookup per registrant: the archive already
    holds every form type, and asking it 20,000 times would cost more than the
    SEC requests it saves.
    """
    # Grouped by issuer and filtered in Python rather than with an IN clause:
    # the target set runs to tens of thousands and SQLite's bind-parameter cap
    # is 32,766, which is the fragility that already broke the fundamentals
    # import once. A query whose size depends on the caller is one that fails
    # on a bigger corpus.
    by_issuer: dict[int, set[str]] = {}
    for issuer_id, form_type in session.execute(
        select(Filing.issuer_id, Filing.form_type).distinct()
    ).all():
        by_issuer.setdefault(issuer_id, set()).add(form_type)
    fund_issuers = {
        issuer_id
        for issuer_id, kinds in by_issuer.items()
        if reporting_regime(kinds) is ReportingRegime.INVESTMENT_COMPANY
    }
    return {
        security_id
        for security_id, issuer_id in session.execute(
            select(Security.security_id, Security.issuer_id)
        ).all()
        if issuer_id in fund_issuers and security_id in security_ids
    }


def _prune(session: Session, *, apply: bool) -> dict[str, int]:
    """Remove bindings that cannot be attributed to one registrant.

    Applied to what is already stored as well as to a fresh run, because the
    rules were learned from the data the first run produced.
    """
    rows = session.execute(
        select(SymbolAlias.id, SymbolAlias.alias_value, SymbolAlias.citation).where(
            SymbolAlias.alias_kind == "ticker",
            SymbolAlias.knowledge_source == KNOWLEDGE_SOURCE,
            SymbolAlias.valid_to.is_not(None),
        )
    ).all()
    by_symbol: dict[str, list[tuple[int, str]]] = {}
    for alias_id, symbol, citation in rows:
        found = _QUOTED.search(citation or "")
        quoted = found.group(1) if found else f"__unparsed__{alias_id}"
        by_symbol.setdefault(symbol, []).append((alias_id, quoted))

    doomed: set[int] = set()
    tally = {"many_claimants": 0, "identical_sentence": 0}
    for _symbol, claims in by_symbol.items():
        if len(claims) < 2:
            continue
        if len(claims) >= 3:
            doomed.update(alias_id for alias_id, _ in claims)
            tally["many_claimants"] += len(claims)
            continue
        if len({sentence for _, sentence in claims}) == 1:
            doomed.update(alias_id for alias_id, _ in claims)
            tally["identical_sentence"] += len(claims)

    if apply and doomed:
        for alias in session.scalars(
            select(SymbolAlias).where(SymbolAlias.id.in_(sorted(doomed)))
        ).all():
            session.delete(alias)
        session.commit()
    tally["total"] = len(doomed)
    return tally


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prune-only", action="store_true", help="apply the rules to stored bindings")
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--edgar-cache", required=True)
    ap.add_argument("--progress", default=".research01_dead_tickers.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--shard",
        default="",
        metavar="I/N",
        help=(
            "process only registrants where cik %% N == I. Shards are disjoint by "
            "construction, so N processes cover the scope exactly once between them "
            "with no coordination. Each needs its own --progress file."
        ),
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    session_only: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    if args.prune_only:
        tally = _prune(session_only, apply=not args.dry_run)
        print(f"pruned: {tally}")
        return 0

    user_agent = resolve_user_agent()
    if not _self_test(user_agent):
        return 1

    facts = json.loads(Path(args.edgar_cache).read_text())
    exits = {int(k): dt.date.fromisoformat(v) for k, v in facts["exits"].items()}
    last_seen = {int(k): dt.date.fromisoformat(v) for k, v in facts["last_seen"].items()}

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
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
            .where(
                IssuerIdentifier.namespace == "sec_cik",
                # Both seeded cohorts, because the second exists precisely to
                # reach registrants the first could not: 2006-2008 has 3,648
                # dated exits and the Data Sets contain none of them.
                Issuer.source.in_(("sec_fsds_sub", "edgar_full_index")),
            )
        ).all()
        if security_id not in have
    ]
    targets = sorted(t for t in targets if t[0] in exits)
    print(f"dead registrants without a ticker: {len(targets):,}")

    # Registrants whose only periodic filings are Investment Company Act forms
    # never traded (§7g: 11 of 1,811 had ever registered a class on an
    # exchange). Spending SEC requests on them cannot produce a ticker, so they
    # are skipped rather than attempted and reported separately -- an
    # unreachable target counted as a failure would look like a coverage gap.
    fund_only = _fund_only({t[1] for t in targets}, session)
    before = len(targets)
    targets = [t for t in targets if t[1] not in fund_only]
    if before != len(targets):
        print(
            f"  skipped, Investment Company Act reporting only: {before - len(targets):,} "
            "(never traded; see EDGAR_DELISTING_DENOMINATOR.md §7g)"
        )

    if args.shard:
        index, _, count = args.shard.partition("/")
        shard_i, shard_n = int(index), int(count)
        if not 0 <= shard_i < shard_n:
            raise SystemExit(f"--shard {args.shard} is not a valid I/N")
        targets = [t for t in targets if t[0] % shard_n == shard_i]
        print(f"  shard {shard_i}/{shard_n}: {len(targets):,} of this scope")

    progress = Path(args.progress)
    done = set(json.loads(progress.read_text())) if progress.exists() else set()
    # The cover-page verdict per registrant, beside the progress file rather
    # than in the database: eight shards are writing to SQLite concurrently and
    # a migration under them is the clash worth avoiding. A sidecar is durable,
    # mergeable and costs no lock. Its home is a column once the run is idle.
    verdict_path = progress.with_name(progress.stem + "_classes.json")
    verdicts: dict[str, str] = json.loads(verdict_path.read_text()) if verdict_path.exists() else {}
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
        # The issuer's own offering prospectus is a fallback, never a
        # preference: an annual report names the symbol the series *ends*
        # under, a prospectus the one it *starts* under, and where both exist
        # the later statement is the one that describes the series we price.
        route = "annual"
        if not reports:
            reports = _annual_reports(cik, user_agent, OFFERING)[-MAX_OFFERING_ATTEMPTS:]
            route = "offering"
            time.sleep(PAUSE_S)
        if not reports:
            tally["no_annual_report"] += 1
            done.add(str(cik))
            continue

        found = None
        for accession, document, filed in reports[
            : MAX_ATTEMPTS if route == "annual" else MAX_OFFERING_ATTEMPTS
        ]:
            found = _symbol_from(cik, accession, document, user_agent)
            time.sleep(PAUSE_S)
            if found is not None and route == "annual":
                # Recorded from the first annual report read, whether or not it
                # bound a symbol: the cover page is the registrant's own
                # statement about what it registered, and the newest filing is
                # the one whose answer describes the security we would price.
                verdicts.setdefault(str(cik), found.classes.value)
            if found is not None and found.symbol is not None:
                anchor = max(filter(None, (last_seen.get(cik), exits.get(cik))), default=filed)
                resolved.append(
                    Resolution(
                        cik=cik,
                        security_id=security_id,
                        ticker=found.symbol,
                        accession=accession,
                        filed=filed,
                        citation=(
                            f"symbol bound to CIK {cik} by {accession} filed {filed}: "
                            f'"{found.statement[:400]}". Interval start NOT '
                            "evidenced by the filing; "
                            f"closed at {anchor}, the registrant's last evidenced activity."
                        ),
                    )
                )
                tally[f"resolved_{route}"] += 1
                break
        if found is None or found.symbol is None:
            verdict = verdicts.get(str(cik), RegisteredClasses.UNDETERMINED.value)
            if verdict == RegisteredClasses.NONE_AT_ALL.value:
                # Not a failure. The registrant declared no registered class,
                # so there is no ticker to find and the question is closed.
                tally["no_registered_class"] += 1
            else:
                tally[f"not_established_{route}"] += 1
        done.add(str(cik))

        if attempted % 50 == 0:
            verdict_path.write_text(json.dumps(verdicts))
            if not args.dry_run:
                _write(session, resolved, last_seen, exits)
                resolved.clear()
                progress.write_text(json.dumps(sorted(done)))
            print(f"  {attempted:,} attempted  {dict(tally)}", flush=True)

    if not args.dry_run:
        _write(session, resolved, last_seen, exits)
        progress.write_text(json.dumps(sorted(done)))
    verdict_path.write_text(json.dumps(verdicts))

    print(f"\nattempted {attempted:,}: {dict(tally)}")
    # Summed across routes: the tally is split by route so the two are never
    # averaged, and a summary reading tally["resolved"] would silently print
    # zero now that no key by that name exists.
    resolved_total = tally["resolved_annual"] + tally["resolved_offering"]
    rate = 100 * resolved_total / attempted if attempted else 0.0
    print(f"resolved {resolved_total:,} ({rate:.1f}%)")
    closed = tally["no_registered_class"]
    if closed:
        print(
            f"no registered class on the cover: {closed:,} "
            f"({100 * closed / attempted:.1f}%) -- the registrant states it has none"
        )
    if not args.dry_run:
        print(f"prune: {_prune(session, apply=True)}")
    print(
        "\nEvery binding carries the filing's own sentence and the accession it came "
        "from. Nothing was resolved from a filing naming more than one symbol, and "
        "nothing survives that two registrants claimed in the same words."
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
