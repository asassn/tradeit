#!/usr/bin/env python
"""Classify why each died-arm security stopped trading, from its EDGAR filings.

Three stages, so the expensive ones run once and classification can be re-run
freely:

``fetch``
    The header of every 8-K each registrant filed from twelve months before its
    last price to six months after -- about 850 bytes each -- for the item
    numbers (:mod:`tradeit.edgar.eightk`).

``fetch-docs``
    Two kinds of full filing, because the header alone was wrong once: every
    8-K whose header declares a bankruptcy item, so its text can be checked
    (General Instrument's header said bankruptcy and its text said acquisition),
    and every Form 15 near the stop, for the holders of record it certifies
    (:mod:`tradeit.edgar.doctext`).

``classify``
    One row per security through :func:`~tradeit.edgar.exit_cause.classify_exit`.
    Reads the caches and the corpus, read-only, and touches nothing else.

Every fetch is capped by :class:`~tradeit.edgar.fetching.RateGate`, identified
by ``EDGAR_USER_AGENT``, backs off on 429/503 and timeouts, and appends to a
cache in the diagnostics directory -- **not the scratchpad**, which is wiped
between sessions and has already cost this project one input file. Resumable:
an answer is never fetched twice, and only transient failures are retried.

**The died population** is every security tradeable on 2000-01-03 with at least
250 bars whose raw series ends before 2009-12-31: the population
``backtest_survivorship.py`` samples its died arm from, taken whole, so every
``--offset`` sample is covered.

**A filing is only evidence about the CIK it was asked about.** Each 8-K
header's filer CIKs are checked against the one requested -- joint filings
count for every filer, subject companies for none -- and a mismatch is never
classified on.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, "src")

from tradeit.edgar.acquire import resolve_user_agent
from tradeit.edgar.doctext import bankruptcy_heading_present, form15_holders_of_record
from tradeit.edgar.eightk import EightKHeader, ItemMeaning, item_meanings, parse_8k_header
from tradeit.edgar.exit_cause import (
    DEREGISTRATION_WINDOW,
    EIGHT_K_WINDOW,
    ExitCause,
    classify_exit,
)
from tradeit.edgar.fetching import DEFAULT_RATE, RateGate, header_url

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
HEADER_FIELDS = [
    "accession",
    "requested_cik",
    "status",
    "header_cik",
    "form_type",
    "filing_date",
    "period",
    "items",
]
DOC_FIELDS = ["accession", "requested_cik", "kind", "status", "bytes"]
FORM_15 = ("15-12B", "15-12G", "15-15D", "15F-12B", "15F-12G", "15F-15D")
#: Failures a later run should try again. A 404 is an answer, not an accident.
#:
#: ``cik_mismatch`` is here because the parser once read only the first filer
#: of a joint filing; a mismatch recorded before that was fixed is not an
#: answer. Re-asking a genuine one costs a request.
TRANSIENT = {
    "http_429",
    "http_503",
    "http_500",
    "http_502",
    "http_504",
    "URLError",
    "TimeoutError",
    "RemoteDisconnected",
    "ConnectionResetError",
    "IncompleteRead",
    "retry_exhausted",
    "cik_mismatch",
}


# -- the population ------------------------------------------------------------


def _restrict(died: dict[int, dt.date], only: Path | None) -> dict[int, dt.date]:
    """Keep only the ids listed, when a list is given."""
    if only is None:
        return died
    wanted = {int(line) for line in only.read_text().split() if line.strip()}
    return {sid: stop for sid, stop in died.items() if sid in wanted}


def _died(
    spans: Path, alive_on: str = "2000-01-03", died_before: str = "2009-12-31"
) -> dict[int, dt.date]:
    """Securities alive on ``alive_on`` whose prices stop before ``died_before``.

    The window is an argument because §16 classified the 2000s population and
    ``DELISTING_RECOVERY_2026-09-23`` needs the same classifier over 2013-2019.
    The defaults reproduce §16 exactly, so an unargumented run still answers the
    question that produced ``exit_causes.csv``.
    """
    with spans.open() as handle:
        rows = [(int(a), b, c, int(d)) for a, b, c, d in csv.reader(handle)]
    return {
        sid: dt.date.fromisoformat(last)
        for sid, first, last, count in rows
        if first <= alive_on <= last and count >= 250 and last < died_before
    }


def _registrants(con: sqlite3.Connection, ids: list[int]) -> dict[int, tuple[int, str, str]]:
    """security_id -> (issuer_id, cik, display name)."""
    out: dict[int, tuple[int, str, str]] = {}
    for sid in ids:
        row = con.execute(
            "select s.issuer_id, i.value, n.display_name from securities s "
            "join issuer_identifiers i on i.issuer_id = s.issuer_id and i.namespace = 'sec_cik' "
            "join issuers n on n.issuer_id = s.issuer_id where s.security_id = ?",
            (sid,),
        ).fetchone()
        if row is not None:
            out[sid] = (int(row[0]), str(row[1]), str(row[2]))
    return out


def _filings(con: sqlite3.Connection, issuer_id: int) -> list[tuple[str, dt.date, str]]:
    return [
        (str(form), dt.date.fromisoformat(str(day)[:10]), str(accession))
        for form, day, accession in con.execute(
            "select form_type, filed_at, accession from filings where issuer_id = ?", (issuer_id,)
        )
    ]


def _within(day: dt.date, stop: dt.date, window: tuple[int, int]) -> bool:
    before, after = window
    return stop - dt.timedelta(days=before) <= day <= stop + dt.timedelta(days=after)


def _thin(ids: list[int], limit: int | None) -> list[int]:
    if limit is None or len(ids) <= limit:
        return ids
    step = len(ids) / limit
    return [ids[int(i * step)] for i in range(limit)]


def _population(
    args: argparse.Namespace,
) -> tuple[sqlite3.Connection, dict[int, dt.date], list[int], dict[int, tuple[int, str, str]]]:
    con = sqlite3.connect("file:research01.sqlite?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=300000")
    died = _restrict(_died(args.spans, args.alive_on, args.died_before), args.only)
    chosen = _thin(sorted(died), args.limit_securities)
    return con, died, chosen, _registrants(con, chosen)


# -- caches ----------------------------------------------------------------------


def _read_cache(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    latest: dict[str, dict[str, str]] = {}
    with path.open() as handle:
        for row in csv.DictReader(handle):
            latest[row["accession"]] = row  # a later line supersedes an earlier failure
    return latest


def _doc_path(accession: str) -> Path:
    return OUT / "edgar_docs" / f"{accession}.txt"


# -- fetching --------------------------------------------------------------------


def _fetch_all(
    todo: list[tuple[str, str]],
    url_of: Callable[[str, str], str],
    handle_body: Callable[[str, str, str], dict[str, str]],
    cache: Path,
    fields: list[str],
    args: argparse.Namespace,
) -> None:
    """Fetch every (accession, cik), rate-capped, appending one row per answer."""
    user_agent = resolve_user_agent()
    gate = RateGate(args.rate)

    def one(item: tuple[str, str]) -> dict[str, str]:
        accession, cik = item

        def failed(status: str) -> dict[str, str]:
            return {"accession": accession, "requested_cik": cik, "status": status}

        for attempt in (1, 2, 3):
            gate.wait()
            try:
                request = urllib.request.Request(
                    url_of(cik, accession), headers={"User-Agent": user_agent}
                )
                with urllib.request.urlopen(request, timeout=90) as response:
                    body = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                if error.code in (429, 503) and attempt < 3:
                    # Being throttled is a request to slow down; answering it
                    # with an immediate identical request turns it into a block.
                    time.sleep(args.backoff * attempt)
                    continue
                return failed(f"http_{error.code}")
            except Exception as error:
                if attempt < 3:
                    time.sleep(args.backoff)
                    continue
                return failed(type(error).__name__)
            return handle_body(accession, cik, body)
        return failed("retry_exhausted")

    new = not cache.exists()
    tally: Counter[str] = Counter()
    started = time.time()
    with cache.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if new:
            writer.writeheader()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for n, row in enumerate(pool.map(one, todo), 1):
                writer.writerow({field: row.get(field, "") for field in fields})
                tally[row["status"]] += 1
                if n % 250 == 0 or n == len(todo):
                    handle.flush()
                    rate = n / max(1e-9, time.time() - started)
                    print(f"  {n:>6,}/{len(todo):,}  {rate:4.1f}/s  {dict(tally)}", flush=True)
    print(f"done: {dict(tally)}")


def fetch_headers(args: argparse.Namespace) -> int:
    con, died, chosen, reg = _population(args)
    plan: dict[str, str] = {}
    for sid in chosen:
        if sid in reg:
            issuer_id, cik, _name = reg[sid]
            for form, day, accession in _filings(con, issuer_id):
                if form in ("8-K", "8-K/A") and _within(day, died[sid], EIGHT_K_WINDOW):
                    plan[accession] = cik
    cache = _read_cache(args.headers)
    todo = [
        (acc, cik)
        for acc, cik in sorted(plan.items())
        if acc not in cache or cache[acc]["status"] in TRANSIENT
    ]
    print(
        f"{len(chosen):,} securities; {len(plan):,} 8-K headers in window; "
        f"{len(plan) - len(todo):,} already answered; fetching {len(todo):,} at <= {args.rate}/s"
    )
    if args.dry_run or not todo:
        return 0

    def handle(accession: str, cik: str, body: str) -> dict[str, str]:
        row = {"accession": accession, "requested_cik": cik}
        header = parse_8k_header(body)
        if header is None:
            row["status"] = "unrecognised_body"
        elif not header.filed_by(int(cik)):
            row.update(status="cik_mismatch", header_cik=" ".join(map(str, header.filer_ciks)))
        else:
            row.update(
                status="ok",
                header_cik=" ".join(map(str, header.filer_ciks)),
                form_type=header.form_type,
                filing_date=header.filing_date.isoformat(),
                period=header.period.isoformat() if header.period else "",
                items=" ".join(header.items),
            )
        return row

    _fetch_all(todo, header_url, handle, args.headers, HEADER_FIELDS, args)
    return 0


def _header(accession: str, cached: dict[str, str]) -> EightKHeader:
    return EightKHeader(
        accession=accession,
        filer_ciks=tuple(int(x) for x in cached["header_cik"].split()),
        form_type=cached["form_type"],
        filing_date=dt.date.fromisoformat(cached["filing_date"]),
        period=dt.date.fromisoformat(cached["period"]) if cached["period"] else None,
        items=tuple(cached["items"].split()),
    )


def _documents_wanted(
    con: sqlite3.Connection,
    died: dict[int, dt.date],
    chosen: list[int],
    reg: dict[int, tuple[int, str, str]],
    headers: dict[str, dict[str, str]],
) -> dict[str, tuple[str, str]]:
    """accession -> (cik, kind) for every document classification will read."""
    wanted: dict[str, tuple[str, str]] = {}
    for sid in chosen:
        if sid not in reg:
            continue
        issuer_id, cik, _name = reg[sid]
        stop = died[sid]
        for form, day, accession in _filings(con, issuer_id):
            cached = headers.get(accession)
            if (
                cached is not None
                and cached["status"] == "ok"
                and _within(day, stop, EIGHT_K_WINDOW)
                and ItemMeaning.BANKRUPTCY in item_meanings(_header(accession, cached))
            ):
                wanted[accession] = (cik, "bankruptcy_8k")
            if form in FORM_15 and _within(day, stop, DEREGISTRATION_WINDOW):
                wanted[accession] = (cik, "form_15")
    return wanted


def fetch_docs(args: argparse.Namespace) -> int:
    con, died, chosen, reg = _population(args)
    wanted = _documents_wanted(con, died, chosen, reg, _read_cache(args.headers))
    cache = _read_cache(args.docs)
    todo = [
        (acc, cik)
        for acc, (cik, _kind) in sorted(wanted.items())
        if not _doc_path(acc).exists() and (acc not in cache or cache[acc]["status"] in TRANSIENT)
    ]
    kinds = Counter(kind for _cik, kind in wanted.values())
    print(
        f"{len(chosen):,} securities; documents wanted {dict(kinds)}; "
        f"fetching {len(todo):,} at <= {args.rate}/s"
    )
    if args.dry_run or not todo:
        return 0
    _doc_path("x").parent.mkdir(parents=True, exist_ok=True)

    def url_of(cik: str, accession: str) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}.txt"

    def handle(accession: str, cik: str, body: str) -> dict[str, str]:
        if "<SEC-DOCUMENT>" not in body and "<SEC-HEADER>" not in body:
            return {"accession": accession, "requested_cik": cik, "status": "unrecognised_body"}
        _doc_path(accession).write_text(body)
        return {
            "accession": accession,
            "requested_cik": cik,
            "kind": wanted[accession][1],
            "status": "ok",
            "bytes": str(len(body)),
        }

    _fetch_all(todo, url_of, handle, args.docs, DOC_FIELDS, args)
    return 0


# -- classification ---------------------------------------------------------------


def classify(args: argparse.Namespace) -> int:
    con, died, chosen, reg = _population(args)
    headers = _read_cache(args.headers)
    statuses: Counter[str] = Counter()
    rows: list[dict[str, object]] = []
    causes: Counter[str] = Counter()
    by_era: dict[str, Counter[str]] = defaultdict(Counter)
    unread = unverified = contradicted = 0
    for sid in chosen:
        if sid not in reg:
            continue
        stop = died[sid]
        issuer_id, cik, name = reg[sid]
        filings = _filings(con, issuer_id)
        eightks: list[EightKHeader] = []
        texts: dict[str, bool] = {}
        holders: list[tuple[dt.date, int | None]] = []
        for form, day, accession in filings:
            if form in ("8-K", "8-K/A") and _within(day, stop, EIGHT_K_WINDOW):
                cached = headers.get(accession)
                statuses["not_fetched" if cached is None else cached["status"]] += 1
                if cached is not None and cached["status"] == "ok":
                    header = _header(accession, cached)
                    eightks.append(header)
                    if ItemMeaning.BANKRUPTCY in item_meanings(header) and (
                        _doc_path(accession).exists()
                    ):
                        texts[accession] = bankruptcy_heading_present(
                            _doc_path(accession).read_text()
                        )
            if form in FORM_15 and _within(day, stop, DEREGISTRATION_WINDOW):
                path = _doc_path(accession)
                holders.append(
                    (day, form15_holders_of_record(path.read_text()) if path.exists() else None)
                )
        found = classify_exit(
            stop,
            [(f, d) for f, d, _ in filings],
            eightks,
            bankruptcy_text=texts,
            form15_holders=holders,
        )
        causes[str(found.cause)] += 1
        by_era["2000-2004" if stop < dt.date(2004, 8, 23) else "2004-2009"][str(found.cause)] += 1
        unread += not found.fully_read
        unverified += any("document unread" in c for c in found.conflicts)
        contradicted += any("document does not" in c for c in found.conflicts)
        rows.append(
            {
                "security_id": sid,
                "issuer_id": issuer_id,
                "cik": cik,
                "name": name,
                "stop": stop.isoformat(),
                "cause": str(found.cause),
                "strength": str(found.strength),
                "eightks_listed": found.eightks_listed,
                "eightks_read": found.eightks_read,
                "evidence": " | ".join(found.evidence),
                "conflicts": " | ".join(found.conflicts),
            }
        )
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    total = len(rows)
    print(f"{total:,} securities classified -> {args.out}")
    print(f"8-K headers in window: {dict(statuses)}")
    print(f"findings with unread 8-Ks in their window: {unread:,}")
    print(
        f"bankruptcy headers the document contradicted: {contradicted:,}; "
        f"left unverified (document unread): {unverified:,}\n"
    )
    print(f"{'cause':<28}{'n':>6}{'share':>8}{'  2000-2004':>12}{'  2004-2009':>12}")
    for cause in ExitCause:
        n = causes[str(cause)]
        print(
            f"{cause:<28}{n:>6,}{n / max(1, total):>8.1%}"
            f"{by_era['2000-2004'][str(cause)]:>12,}{by_era['2004-2009'][str(cause)]:>12,}"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["fetch", "fetch-docs", "classify"])
    ap.add_argument("--spans", type=Path, default=OUT / "security_spans.csv")
    ap.add_argument("--headers", type=Path, default=OUT / "exit_8k_headers.csv")
    ap.add_argument("--docs", type=Path, default=OUT / "exit_documents.csv")
    ap.add_argument("--out", type=Path, default=OUT / "exit_causes.csv")
    ap.add_argument("--limit-securities", type=int, default=None)
    ap.add_argument("--alive-on", default="2000-01-03")
    ap.add_argument("--died-before", default="2009-12-31")
    ap.add_argument(
        "--only",
        type=Path,
        default=None,
        help="restrict to the security ids in this file, one per line",
    )
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--backoff", type=float, default=30.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.stage == "fetch":
        return fetch_headers(args)
    if args.stage == "fetch-docs":
        return fetch_docs(args)
    return classify(args)


if __name__ == "__main__":
    raise SystemExit(main())
