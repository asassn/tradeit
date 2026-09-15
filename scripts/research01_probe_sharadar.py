#!/usr/bin/env python
"""Can Sharadar price the dated exits the corpus cannot? Measured, not ingested.

``docs/PROPOSAL_SURVIVORSHIP_DATA_2026-09-14.md`` fixed the tests before the
owner bought one month of Sharadar's Prices plan. **Nothing here writes to
``research-01``.** Everything downloaded goes to the diagnostics directory,
which is not committed.

**The join is on the SEC CIK, not the ticker.** Sharadar's ``tickers`` table
carries each issuer's EDGAR URL, and the CIK in it is the identifier the
denominator is built on. Stage 1 showed why that matters: ``PSIX`` in Sharadar
is Power Solutions International (CIK 1137091), not PSINet (CIK 940716), and
``AOL`` is the 2009-2015 AOL Inc, not the original -- reused tickers that a
ticker join would have credited to the wrong company. A CIK in a URL is still a
vendor's assertion, so the price check below tests it against EDGAR's dates.

Subcommands, staged the way the owner asked -- a handful, then about a hundred,
then the rest:

    tickers   download the tickers table's rows for table=stocks (metadata only)
    match     join every dated exit to Sharadar by CIK and grade the metadata
    prices    fetch real bars for the first N of a random sample of matches
              and grade them against the EDGAR exit date

**Metadata grades** (``match``), for an exit on date E:

    DATES_FIT        Sharadar prices start on or before E and end within
                     REACH before E to CONTINUES after it
    ENDS_EARLY       prices end more than REACH before E
    ENDS_LATE        prices continue more than CONTINUES past E -- a later
                     security under the same CIK, or a spliced series
    STARTS_AFTER     first price after E
    NO_CIK_MATCH     no SEP row carries this CIK

**The key.** Sharadar reads it from the ``api_key`` query parameter, so every
URL and error text passes through
:func:`~tradeit.acquisition.redaction.redact_text` before it is printed or saved.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")

from tradeit.acquisition.redaction import redact_text

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
BASE = "https://api.sharadar.com/v1.0/data"
TICKERS = OUT / "sharadar_tickers_sep.json"
MATCHES = OUT / "sharadar_match.json"
PRICE_LEDGER = OUT / "sharadar_price_probe.jsonl"
SEED = 20260914
PAGE = 10_000
REACH = dt.timedelta(days=180)
CONTINUES = dt.timedelta(days=120)
MIN_BARS = 20
CIK_IN_URL = re.compile(r"CIK=0*(\d+)")
COLUMNS = (
    "table,permaticker,ticker,name,exchange,isdelisted,category,"
    "firstpricedate,lastpricedate,secfilings,relatedtickers"
)


def _key() -> str:
    for line in Path(".env").read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == "SHARADAR_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def _get(path: str, key: str) -> tuple[int, str]:
    url = f"{BASE}/{path}&api_key={key}&format=json"
    request = urllib.request.Request(url, headers={"User-Agent": "tradeit-probe"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, (error.read() or b"").decode("utf-8", "replace")


def _rows(text: str) -> list[dict]:
    decoded = json.loads(text)
    if isinstance(decoded, list):
        return decoded
    if isinstance(decoded, dict) and isinstance(decoded.get("data"), list):
        return decoded["data"]
    return []


def cmd_tickers(key: str) -> int:
    rows: list[dict] = []
    skip = 0
    while True:
        status, text = _get(f"tickers?table=stocks&fields={COLUMNS}&limit={PAGE}&skip={skip}", key)
        if status != 200:
            print(f"HTTP {status}: {redact_text(text[:300], key)}")
            return 1
        page = _rows(text)
        rows.extend(page)
        print(f"  skip {skip:>6}: {len(page):,} rows", flush=True)
        if len(page) < PAGE:
            break
        skip += PAGE
        time.sleep(1)
    TICKERS.write_text(json.dumps(rows))
    tables = collections.Counter(r.get("table") for r in rows)
    delisted = sum(1 for r in rows if r.get("isdelisted") == "Y")
    with_cik = sum(1 for r in rows if CIK_IN_URL.search(r.get("secfilings") or ""))
    print(
        f"{len(rows):,} rows -> {TICKERS}\n  tables {dict(tables)}\n"
        f"  delisted {delisted:,}; carrying a CIK {with_cik:,}"
    )
    return 0


def _date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _grade_meta(exit_date: dt.date, first: dt.date | None, last: dt.date | None) -> str:
    if first is None or last is None:
        return "NO_CIK_MATCH"
    if first > exit_date:
        return "STARTS_AFTER"
    if last > exit_date + CONTINUES:
        return "ENDS_LATE"
    if last < exit_date - REACH:
        return "ENDS_EARLY"
    return "DATES_FIT"


def cmd_match() -> int:
    exits_payload = json.loads((OUT / "dated_exits.json").read_text())
    exits = {int(c): dt.date.fromisoformat(d) for c, d in exits_payload["exits"].items()}
    priced = {int(c) for c in exits_payload["priced"]}
    rows = json.loads(TICKERS.read_text())
    by_cik: dict[int, list[dict]] = collections.defaultdict(list)
    for row in rows:
        found = CIK_IN_URL.search(row.get("secfilings") or "")
        if found:
            by_cik[int(found.group(1))].append(row)

    results = {}
    for cik, exit_date in exits.items():
        candidates = by_cik.get(cik, [])
        best, best_grade = None, "NO_CIK_MATCH"
        rank = {
            "DATES_FIT": 0,
            "ENDS_LATE": 1,
            "ENDS_EARLY": 2,
            "STARTS_AFTER": 3,
            "NO_CIK_MATCH": 4,
        }
        for row in candidates:
            grade = _grade_meta(
                exit_date, _date(row.get("firstpricedate")), _date(row.get("lastpricedate"))
            )
            if rank[grade] < rank[best_grade]:
                best, best_grade = row, grade
        results[cik] = {
            "cik": cik,
            "exit": exit_date.isoformat(),
            "priced_already": cik in priced,
            "grade": best_grade,
            "ticker": best.get("ticker") if best else None,
            "permaticker": best.get("permaticker") if best else None,
            "exchange": best.get("exchange") if best else None,
            "first": best.get("firstpricedate") if best else None,
            "last": best.get("lastpricedate") if best else None,
            "securities_under_cik": len({r.get("permaticker") for r in candidates}),
        }
    MATCHES.write_text(json.dumps(results))

    def summary(label: str, group: list[dict]) -> None:
        counts = collections.Counter(r["grade"] for r in group)
        print(f"\n{label}: {len(group):,}")
        for grade, n in counts.most_common():
            print(f"  {grade:<14}{n:>7,}  {n / max(1, len(group)):6.1%}")

    everything = list(results.values())
    unpriced = [r for r in everything if not r["priced_already"]]
    summary(
        "already priced in research-01 (a control: these should mostly fit)",
        [r for r in everything if r["priced_already"]],
    )
    summary("UNPRICED dated exits (the gap)", unpriced)
    fit = sum(1 for r in unpriced if r["grade"] == "DATES_FIT")
    total, have = len(exits), len(priced)
    print(
        f"\nif every unpriced DATES_FIT exit priced: {have + fit:,}/{total:,} = "
        f"{(have + fit) / total:.2%} (need {int(0.45 * total) + 1:,} for 45%)"
    )
    era = collections.Counter()
    for r in unpriced:
        if r["grade"] == "DATES_FIT":
            y = int(r["exit"][:4])
            era[
                "pre-1999"
                if y < 1999
                else "1999-2006"
                if y < 2007
                else "2007-2014"
                if y < 2015
                else "2015+"
            ] += 1
    print(f"  unpriced DATES_FIT by exit era: {dict(sorted(era.items()))}")
    return 0


def cmd_prices(key: str, stage: int) -> int:
    matches = json.loads(MATCHES.read_text())
    pool = sorted(
        (r for r in matches.values() if not r["priced_already"] and r["grade"] == "DATES_FIT"),
        key=lambda r: r["cik"],
    )
    random.Random(SEED).shuffle(pool)
    done = {}
    if PRICE_LEDGER.exists():
        for line in PRICE_LEDGER.read_text().splitlines():
            record = json.loads(line)
            done[record["cik"]] = record
    with PRICE_LEDGER.open("a") as ledger:
        for target in pool[:stage]:
            if target["cik"] in done:
                continue
            exit_date = dt.date.fromisoformat(target["exit"])
            start = (dt.date.fromisoformat(target["first"]) - dt.timedelta(days=5)).isoformat()
            end = min(exit_date + dt.timedelta(days=400), dt.datetime.now(dt.UTC).date())
            status, text = _get(
                f"stocks?ticker={target['ticker']}&date.gte={start}&date.lte={end.isoformat()}"
                f"&limit={PAGE}",
                key,
            )
            dates: list[dt.date] = []
            if status == 200:
                for row in _rows(text):
                    d = _date(row.get("date"))
                    if d:
                        dates.append(d)
            dates.sort()
            lifetime = [d for d in dates if d <= exit_date + dt.timedelta(days=30)]
            if status != 200:
                grade = f"HTTP_{status}"
            elif len(lifetime) < MIN_BARS:
                grade = "NO_BARS_IN_LIFETIME"
            elif any(d > exit_date + CONTINUES for d in dates):
                grade = "SUSPECT_SUCCESSOR"
            elif lifetime[-1] < exit_date - REACH:
                grade = "STOPS_EARLY"
            else:
                grade = "PLAUSIBLE"
            record = {
                "cik": target["cik"],
                "ticker": target["ticker"],
                "permaticker": target["permaticker"],
                "exit": target["exit"],
                "grade": grade,
                "bars": len(dates),
                "first_bar": dates[0].isoformat() if dates else None,
                "last_bar": dates[-1].isoformat() if dates else None,
                "note": "" if status == 200 else redact_text(text[:200], key),
            }
            ledger.write(json.dumps(record) + "\n")
            ledger.flush()
            done[target["cik"]] = record
            time.sleep(0.5)
    graded = [done[t["cik"]] for t in pool[:stage] if t["cik"] in done]
    counts = collections.Counter(r["grade"] for r in graded)
    print(
        f"prices, stage {stage}: {len(graded):,} graded of {len(pool):,} DATES_FIT unpriced exits"
    )
    for grade, n in counts.most_common():
        print(f"  {grade:<20}{n:>6}  {n / max(1, len(graded)):6.1%}")
    return 0


#: The 30 manually verified controls, by the identifier `tradeit edgar controls`
#: prints. FRC files with the FDIC, not the SEC, so it has no CIK to join on.
CONTROLS: tuple[tuple[str, int | None, str], ...] = (
    ("AAPL", 320193, "survivor"),
    ("MSFT", 789019, "survivor"),
    ("CSCO", 858877, "survivor"),
    ("AMZN", 1018724, "survivor"),
    ("SPY", 884394, "ETF"),
    ("QQQ", 1067839, "ETF"),
    ("IPET", 1100683, "short-lived failure"),
    ("ETYS", 1052245, "short-lived failure"),
    ("WBVN", 1092657, "short-lived failure"),
    ("TGLO", 1066684, "collapse, shell"),
    ("KOOP", 1073794, "short-lived failure"),
    ("MPPP", 1078073, "acquired failing"),
    ("ENE", 72859, "large-cap collapse"),
    ("WCOM", 723527, "collapse, reorganised"),
    ("EXDS", 1013740, "infrastructure failure"),
    ("PSIX", 940716, "infrastructure failure"),
    ("GCTY", 1062777, "peak acquisition"),
    ("BCST", 1061236, "peak acquisition"),
    ("CPQ", 714154, "merger"),
    ("BEL", 732712, "rename"),
    ("BBBY", 886158, "ticker reuse"),
    ("GM", 40730, "ticker reuse"),
    ("AOL", 883780, "ticker reuse"),
    ("JDSU", 912093, "reverse split"),
    ("PCLN", 1075531, "reverse split"),
    ("QCOM", 804328, "forward split"),
    ("LEH", 806085, "crisis failure"),
    ("CC", 104599, "crisis liquidation"),
    ("FRC", None, "FDIC filer"),
    ("RDDT", 1713445, "recent IPO"),
)


def cmd_controls(key: str) -> int:
    """Gate G4 and G5: is each control present under its own CIK, and do the
    reused tickers stay separate -- the old company's series ending at its own
    death rather than running on into the ticker's next holder."""
    rows = json.loads(TICKERS.read_text())
    by_cik: dict[int, list[dict]] = collections.defaultdict(list)
    for row in rows:
        found = CIK_IN_URL.search(row.get("secfilings") or "")
        if found:
            by_cik[int(found.group(1))].append(row)
    funds_status, funds_text = _get(
        "tickers?table=funds&ticker=SPY,QQQ&fields=ticker,secfilings,firstpricedate,lastpricedate",
        key,
    )
    funds = _rows(funds_text) if funds_status == 200 else []
    present = 0
    for ticker, cik, why in CONTROLS:
        hits = by_cik.get(cik, []) if cik else []
        if not hits and why == "ETF":
            hits = [r for r in funds if r.get("ticker") == ticker]
        if not hits and cik is None:
            _, text = _get(
                f"tickers?table=stocks&ticker={ticker}&fields=ticker,name,firstpricedate,lastpricedate,isdelisted",
                key,
            )
            hits = [r for r in _rows(text) if "FIRST REPUBLIC" in str(r.get("name", "")).upper()]
        present += bool(hits)
        shown = "; ".join(
            f"{r.get('ticker')} {r.get('firstpricedate')}..{r.get('lastpricedate')}"
            + (f" [{r.get('name')}]" if r.get("name") else "")
            for r in hits[:3]
        )
        print(f"  {ticker:<5} {why:<24} {'present' if hits else 'ABSENT ':<8} {shown}")
        time.sleep(0.3) if cik is None else None
    print(f"\ncontrols present: {present} of {len(CONTROLS)}")
    return 0


def cmd_crosscheck(key: str, stage: int) -> int:
    """Independent quality check: Sharadar's unadjusted close against the raw
    close research-01 already holds from EODHD, for exits both vendors price."""
    import sqlite3
    import statistics

    matches = json.loads(MATCHES.read_text())
    pool = sorted(
        (r for r in matches.values() if r["priced_already"] and r["grade"] == "DATES_FIT"),
        key=lambda r: r["cik"],
    )
    random.Random(SEED + 1).shuffle(pool)
    con = sqlite3.connect("file:research01.sqlite?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=300000")
    agree_all: list[float] = []
    per_security: list[tuple[str, int, float]] = []
    for target in pool[:stage]:
        securities = [
            sid
            for (sid,) in con.execute(
                "select distinct s.security_id from issuer_identifiers i join securities s "
                "on s.issuer_id=i.issuer_id where i.namespace='sec_cik' and i.value_normalized=?",
                (str(target["cik"]),),
            )
        ]
        if not securities:
            continue
        ours: dict[str, float] = {}
        for sid in securities:
            for day, close in con.execute(
                "select session_date, close from security_price_facts where security_id=? and "
                "adjustment_basis='raw' and close>0 and volume>0",
                (sid,),
            ):
                ours[str(day)[:10]] = float(close)
        status, text = _get(
            f"stocks?ticker={target['ticker']}&fields=date,closeunadj&limit={PAGE}", key
        )
        theirs = (
            {
                str(r["date"])[:10]: float(r["closeunadj"])
                for r in _rows(text)
                if r.get("closeunadj")
            }
            if status == 200
            else {}
        )
        common = [d for d in theirs if d in ours and ours[d] > 0]
        if len(common) < 20:
            continue
        within = [abs(theirs[d] / ours[d] - 1) <= 0.01 for d in common]
        share = sum(within) / len(within)
        per_security.append((target["ticker"], len(common), share))
        agree_all.extend(abs(theirs[d] / ours[d] - 1) for d in common)
        time.sleep(0.5)
    print(f"cross-checked {len(per_security)} securities, {len(agree_all):,} common sessions")
    if per_security:
        shares = sorted(x[2] for x in per_security)
        print(
            f"  sessions within 1%: median security {statistics.median(shares):.1%}, "
            f"worst {shares[0]:.1%}"
        )
        print(
            f"  securities with >= 95% of sessions within 1%: "
            f"{sum(1 for x in shares if x >= 0.95)} of {len(shares)}"
        )
        print(f"  median absolute difference over all sessions: {statistics.median(agree_all):.3%}")
        for t, n, sh in sorted(per_security, key=lambda x: x[2])[:5]:
            print(f"    lowest: {t:<8} {n:>5} sessions, {sh:.1%} within 1%")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("tickers", "match", "prices", "controls", "crosscheck"))
    ap.add_argument("--stage", type=int, default=5)
    args = ap.parse_args()
    key = _key()
    if args.command != "match" and not key:
        print("SHARADAR_API_KEY is not set in .env")
        return 2
    if args.command == "tickers":
        return cmd_tickers(key)
    if args.command == "match":
        return cmd_match()
    if args.command == "controls":
        return cmd_controls(key)
    if args.command == "crosscheck":
        return cmd_crosscheck(key, args.stage)
    return cmd_prices(key, args.stage)


if __name__ == "__main__":
    raise SystemExit(main())
