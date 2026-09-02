#!/usr/bin/env python
"""Find the registrant that took a ticker after ours stopped using it.

**The shape this exists for.** Sixteen series in ``research-01`` run 8 to 25
years past their registrant's last filing with **no dormancy and no regime
break** -- an unbroken quote whose level and liquidity never change. Every
structural rule in ``adjudicate.py`` needs a discontinuity, and these have none.
What is left is to ask EDGAR who else claimed the symbol.

**Full-text search proposes; our own extractor disposes.** ``efts.sec.gov``
finds documents *containing* a token, which is not the same as documents that
*bind a security to it* -- ``AWS``, ``BIR`` and ``TSX`` occur in ordinary
English and in other companies' prose. Every hit is therefore fetched and run
through ``confirm_ticker``, the same function that established these identities
in the first place, with the same refusals: no symbol sentence, a different
symbol, or several symbols all mean *not established*.

**Two queries, because one of them cannot answer for a common token.** A bare
token search for ``AWS`` returns over ten thousand filings -- Amazon Web
Services -- and the hundred that relevance happens to rank first are a needle in
a haystack, so a negative drawn from them means nothing. The phrase ``"symbol
AWS"`` returns **zero**, which is a real answer. The same narrowing takes
``IGL`` from 3,117 to 3 and ``BIR`` from 5,045 to 33 -- and one of those 33 is a
genuine candidate the bare search had buried.

**The rule is: use the tightest query that can be exhausted, and say which.**
A ladder of progressively narrower phrases is tried and the first whose whole
result set fits inside :data:`EXHAUSTIBLE` is the one used, because a search
whose every hit was examined supports a negative and a search that saw the first
hundred of ten thousand does not. ``TSX`` needs the bottom rung -- "symbol TSX"
still returns thousands, because that is how every Canadian filer writes the
Toronto Stock Exchange.

The bare token is unioned in as well whenever *it* is exhaustible, since the two
miss different things: the phrase misses a Section 12(b) table, whose header
says "Trading Symbol" a column away from the ticker rather than beside it, and
the bare token misses nothing but drowns.

**Restricting the phrase to the 10-K family was a mistake, found by reading the
counters.** Item 5 of a 10-K is where an *established* registrant states its
symbol -- but a successor that has just taken the ticker states it first in a
registration statement or an 8-K, and may not file a 10-K for a year or ever.
``"symbol BIR"`` returns one 10-K and **thirty-three filings overall**. The
narrowing that justified the restriction is now done by the phrase itself, so
the form filter only lost evidence.

**Both totals and the rung used are reported per ticker**, because they are what
says whether a negative is worth anything.

**No laxer variant is used to raise the hit rate.** A 10-K naming common stock
and warrants names two symbols and is refused, which is a false negative -- and
a false negative here costs coverage, while a false positive would cut away a
registrant's real trading on the strength of a coincidence.

**This script proposes and never applies, and that is a decision taken on
evidence.** The structural rules in ``adjudicate.py`` write boundaries by
themselves because each has a null test behind it: the regime-break detector
fires on none of 768 single-company lifetimes, so its false-positive rate is
measured. This route has no such number, and its observed rate is **two out of
two** -- both were the ``VENUE: TICKER`` form, "symbol TSX: XPL" and "symbol
TSX-V: GGC", where the ticker is the half after the colon. Both were caught by
reading the citation rather than counting the hit, and the second only because
the fix for the first was checked instead of assumed.

Applying automatically would be applying an unquantified detector to an
irreversible-looking cut. So findings are written to JSON with their citations
for a person to read, which is the same rule ``acquire.py`` enforces
structurally for ``MANUAL_VERIFIED``: a program that reads a filing has not
satisfied the requirement that a person read it. Use
``research01_adjudicate.py`` to write a boundary once a finding has been read.

**Where the boundary would go, and why it is the conservative end.** A successor's
filing dates when the symbol *had* moved, not when it moved: the handover lies
somewhere between our registrant's last evidenced activity and that filing.
Closing the interval at the anchor discards any over-the-counter trading our
registrant did in between -- and that is the right failure. Attributing another
company's prices to this one is the error the whole corpus exists to prevent;
losing coverage is a cost. Nothing is deleted either way.

**A zero from an unvalidated pipeline is worthless, so the pipeline validates
itself first.** This repository has already had one "confirmed 0 of 40" that was
a bug in the extractor rather than a fact about the filings. Before any search
runs, ``--self-test`` fetches Apple's most recent 10-K and requires that it bind
``AAPL`` to CIK 320193 through the same fetch, strip, extract and confirm path.
If that fails, nothing else runs: a broken pipeline must not be able to report
an empty result.

**Full-text search begins in 2001.** A handover completed before then is
invisible to this, and a ``not established`` result therefore means *this search
could not show it*, never *it did not happen*.

    EDGAR_USER_AGENT='... (you@example.com)' \\
      PYTHONPATH=src .venv/bin/python scripts/research01_successor.py \\
      --verdicts verdicts.json
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")

from tradeit.edgar.acquire import extract_identity_evidence, resolve_user_agent, strip_html
from tradeit.research01.confirm import comparison_form, confirm_ticker

FTS = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

#: EDGAR full-text search holds nothing earlier. Asking for less is a silently
#: empty window rather than an error, so the floor is explicit.
FTS_EARLIEST = dt.date(2001, 1, 1)

#: SEC fair access. Ten a second is the published ceiling; this stays well under.
PAUSE_S = 0.15


class SearchUnavailable(RuntimeError):
    """Full-text search would not answer. Distinct from answering "nothing"."""


@dataclass(frozen=True)
class Hit:
    accession: str
    document: str
    cik: int
    name: str
    filed: dt.date
    ticker: str


#: A result set at or under this size is fetched whole, so a negative drawn from
#: it is a statement about the corpus. Above it, only what relevance ranked
#: first is seen, and a negative is a statement about the ranking. Measured: the
#: residual tickers split cleanly, sixteen under a hundred and seven in the
#: thousands, with nothing in between to make the line arbitrary.
EXHAUSTIBLE = 300


def _query(
    q: str, *, ticker: str, start: dt.date, user_agent: str, limit: int, forms: str | None
) -> tuple[list[Hit], int]:
    """Run one full-text query. Returns its hits and the corpus-wide total.

    The total is the point of returning it: it is what distinguishes "we looked
    at everything and found nothing" from "we looked at the first hundred of ten
    thousand".
    """
    hits: list[Hit] = []
    total = 0
    for offset in range(0, max(limit, 1), 10):
        parameters = {
            "q": q,
            "dateRange": "custom",
            "startdt": max(start, FTS_EARLIEST).isoformat(),
            "enddt": dt.datetime.now(dt.UTC).date().isoformat(),
            "from": offset,
        }
        if forms:
            parameters["forms"] = forms
        query = urllib.parse.urlencode(parameters)
        request = urllib.request.Request(
            f"{FTS}?{query}", headers={"User-Agent": user_agent, "Accept": "application/json"}
        )
        # The service returns 500 for queries it will not run as well as for
        # its own transient failures, and the two are indistinguishable from
        # here. Retry a few times with a widening pause; a query that fails
        # every time is reported as a failed search, never as an empty one --
        # "we found nothing" and "we could not look" are different answers.
        payload = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.load(response)
                break
            except Exception:
                time.sleep(1.0 * (attempt + 1))
        if payload is None:
            raise SearchUnavailable(ticker)
        total = payload.get("hits", {}).get("total", {}).get("value", 0)
        page = payload.get("hits", {}).get("hits", [])
        for entry in page:
            accession, _, document = entry["_id"].partition(":")
            source = entry["_source"]
            ciks = source.get("ciks") or []
            names = source.get("display_names") or [""]
            if not ciks:
                continue
            hits.append(
                Hit(
                    accession=accession,
                    document=document,
                    cik=int(ciks[0]),
                    name=names[0],
                    filed=dt.date.fromisoformat(source["file_date"]),
                    ticker=ticker,
                )
            )
        # The service decides its own page size -- it returned sixty where ten
        # were asked for -- so paging stops on a short page relative to the one
        # before it, never on an assumed constant.
        if not page or len(hits) >= limit:
            break
        time.sleep(PAUSE_S)
    return hits[:limit], total


def _search(
    ticker: str, *, start: dt.date, user_agent: str, limit: int
) -> tuple[list[Hit], str, int]:
    """Hits, the query rung actually used, and that rung's corpus-wide total.

    Rungs, widest first. The first that can be **exhausted** wins; if none can,
    the narrowest is used and its total says so.
    """
    rungs: list[tuple[str, str, str | None]] = [
        ("phrase/all-forms", f'"symbol {ticker}"', None),
        ("tight-phrase/all-forms", f'"under the symbol {ticker}"', None),
        ("phrase/10-K", f'"symbol {ticker}"', "10-K,10-K405,10-KSB"),
    ]
    chosen: tuple[list[Hit], str, int] | None = None
    for label, query, forms in rungs:
        hits, total = _query(
            query, ticker=ticker, start=start, user_agent=user_agent, limit=limit, forms=forms
        )
        chosen = (hits, label, total)
        if total <= EXHAUSTIBLE:
            break

    assert chosen is not None
    hits, label, total = chosen

    # The bare token joins in only when it too can be exhausted -- it is the one
    # that catches a 12(b) table, and the one that drowns.
    bare, bare_total = _query(
        f'"{ticker}"',
        ticker=ticker,
        start=start,
        user_agent=user_agent,
        limit=limit,
        forms=None,
    )
    if bare_total <= EXHAUSTIBLE:
        seen = {h.accession + h.document for h in hits}
        hits = hits + [h for h in bare if h.accession + h.document not in seen]
        label = f"{label}+bare"

    return hits, label, total


#: Documents larger than this are not stripped. ``strip_html`` over a modern
#: filing carrying inline XBRL costs seconds of CPU, and this bounds a run.
#: The cap applies **after** the byte pre-filter, so it is paid only by
#: documents that could still bind; skips are counted and reported, because a
#: skipped document is an unanswered question and not a negative answer.
MAX_DOCUMENT_BYTES = 40 * 1024 * 1024


def _fetch(hit: Hit, user_agent: str) -> tuple[str | None, str]:
    """The document's text, or ``None`` with the reason it was not read.

    **The cheap pre-filter is exactly equivalent, not an approximation.**
    ``confirm_ticker`` can only confirm through ``_BOUND_SYMBOL``, which
    requires the literal word "symbol" before the token, so a document
    containing no "symbol" -- or not containing the ticker at all -- cannot
    produce a confirmation however carefully it is parsed. Testing that on the
    raw bytes skips the expensive HTML strip on documents whose answer is
    already known.
    """
    plain = hit.accession.replace("-", "")
    url = f"{ARCHIVES}/{hit.cik}/{plain}/{hit.document}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
    except Exception:
        return None, "fetch_failed"
    # **The cheap test comes first, and the order matters.** A byte scan costs
    # nothing at any size, so applying it before the size cap converts a large
    # document that cannot bind from an unanswered question into an answered
    # one. With the cap applied first, 407 documents in one run were recorded as
    # unread when most of them had already been settled.
    upper = raw.upper()
    if b"SYMBOL" not in upper or hit.ticker.encode() not in upper:
        return None, "cannot_bind"
    if len(raw) > MAX_DOCUMENT_BYTES:
        return None, "too_large"
    return strip_html(raw.decode("latin-1")), "read"


#: The positive control. Apple's 10-K states its symbol in Item 5 and lists it
#: in the Section 12(b) table, so a pipeline that cannot confirm this one cannot
#: confirm anything, and its silence would mean nothing.
CONTROL_TICKER, CONTROL_CIK = "AAPL", 320193


def _self_test(user_agent: str) -> bool:
    """Confirm a binding we already know, through the whole path. Fail closed."""
    try:
        hits, _rung, _total = _search(
            CONTROL_TICKER, start=dt.date(2020, 1, 1), user_agent=user_agent, limit=60
        )
    except SearchUnavailable:
        print("SELF-TEST: full-text search unavailable")
        return False
    for hit in hits:
        if hit.cik != CONTROL_CIK:
            continue
        text, _why = _fetch(hit, user_agent)
        if text is None:
            continue
        if confirm_ticker(
            ticker=CONTROL_TICKER,
            cik=hit.cik,
            accession=hit.accession,
            extract=extract_identity_evidence(text),
        ):
            print(f"SELF-TEST passed: {hit.accession} binds {CONTROL_TICKER} to CIK {hit.cik}")
            return True
    print("SELF-TEST FAILED: the control binding was not confirmed; no search will be run")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", required=True, help="JSON from research01_adjudicate.py")
    ap.add_argument("--edgar-cache", required=True, help="JSON from research01_adjudicate.py")
    ap.add_argument("--max-hits", type=int, default=100)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    user_agent = resolve_user_agent()
    if not _self_test(user_agent):
        return 1
    cache = json.loads(Path(args.edgar_cache).read_text())
    last_seen = {int(k): dt.date.fromisoformat(v) for k, v in cache["last_seen"].items()}
    exits = {int(k): dt.date.fromisoformat(v) for k, v in cache["exits"].items()}

    targets = [
        row
        for row in json.loads(Path(args.verdicts).read_text())
        if row["verdict"] in {"unresolved", "contaminated_boundary_unknown"} and row["ticker"]
    ]
    print(f"{len(targets)} series with no located boundary\n")

    findings = []
    totals: collections.Counter[str] = collections.Counter()
    unavailable: list[str] = []
    for row in targets:
        plain = comparison_form(row["ticker"])
        anchor = last_seen.get(row["cik"])
        exit_date = exits.get(row["cik"])
        if anchor and exit_date:
            anchor = max(anchor, exit_date)
        anchor = anchor or exit_date
        if anchor is None:
            print(f"  {plain:<8} no anchor; skipped")
            continue

        try:
            hits, rung, rung_total = _search(
                plain, start=anchor, user_agent=user_agent, limit=args.max_hits
            )
        except SearchUnavailable:
            unavailable.append(plain)
            print(f"  {plain:<8} sid {row['security_id']:>4}  SEARCH UNAVAILABLE, not answered")
            continue
        others = [h for h in hits if h.cik != row["cik"] and h.filed > anchor]
        confirmed = None
        tally: collections.Counter[str] = collections.Counter()
        for hit in sorted(others, key=lambda h: h.filed):
            text, why = _fetch(hit, user_agent)
            time.sleep(PAUSE_S)
            tally[why] += 1
            if text is None:
                continue
            result = confirm_ticker(
                ticker=plain,
                cik=hit.cik,
                accession=hit.accession,
                extract=extract_identity_evidence(text),
            )
            tally["confirmed" if result is not None else "read_and_refused"] += 1
            if result is not None:
                confirmed = (hit, result)
                break

        if confirmed is None:
            print(
                f"  {plain:<8} sid {row['security_id']:>4}"
                f"  {rung:<24} corpus {rung_total:>5}"
                f"{'' if rung_total <= EXHAUSTIBLE else ' NOT EXHAUSTED'}"
                f"  {len(others):>3} by others"
                f"  read {tally['read']:>3}  refused {tally['read_and_refused']:>3}"
                f"  no-symbol-token {tally['cannot_bind']:>3}"
                f"  too-large {tally['too_large']:>2}  fetch-failed {tally['fetch_failed']:>2}"
                "   NONE confirmed"
            )
            totals.update(tally)
            continue
        hit, result = confirmed
        print(
            f"  {plain:<8} sid {row['security_id']:>4}  SUCCESSOR CIK {hit.cik} "
            f"({hit.name}) by {hit.accession} filed {hit.filed}"
        )
        totals.update(tally)
        findings.append(
            {
                "security_id": row["security_id"],
                "cik": row["cik"],
                "ticker": row["ticker"],
                "anchor": str(anchor),
                "query_rung": rung,
                "rung_total": rung_total,
                "successor_cik": hit.cik,
                "successor_name": hit.name,
                "accession": hit.accession,
                "filed": str(hit.filed),
                "citation": result.citation,
            }
        )

    print(f"\nsuccessors established: {len(findings)} of {len(targets)}")
    print(
        "  documents: "
        + ", ".join(f"{k} {v}" for k, v in sorted(totals.items()))
        + "\n  A zero here means only what the counts above support: "
        "'read_and_refused' is a filing that named no symbol, a different one, or "
        "several; 'cannot_bind' is a document that cannot produce a confirmation "
        "however parsed, because it contains no 'symbol' token or not the ticker."
    )
    if unavailable:
        print(
            f"searches that could not be run at all: {len(unavailable)} -- {', '.join(unavailable)}"
        )
    if args.out:
        Path(args.out).write_text(json.dumps(findings, indent=1))
        print(f"wrote {args.out}")

    print(
        "\nNothing was written. Read each citation above before acting on it: "
        "both findings this route has ever produced were the VENUE: TICKER form, "
        "and both looked like confirmations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
