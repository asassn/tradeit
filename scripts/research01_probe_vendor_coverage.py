#!/usr/bin/env python
"""Can a second vendor price the dead companies EODHD could not?

``EDGAR_DELISTING_DENOMINATOR.md`` §7h measured the gap: 1,358 more priced exits
reach 45%, and **Pool A** -- dated exits whose identity is resolved but which
have no price series -- is about three times that. EODHD has already been asked
for all of them (7.2% yield). Tiingo and FMP have never been asked.

**This measures; it ingests nothing.** No bar reaches ``research-01``. Whether a
vendor's bars ever enter the corpus is a separate decision, taken on this
measurement, because adding them changes what the corpus claims.

**The sample is random.** EODHD's first probe sampled only failures that
appeared in its own delisted list, got ten of ten, and was wrong: a random
sample of twenty returned zero. So targets here are drawn uniformly from Pool A
with a fixed seed, and stages widen the same shuffled order -- a handful, then a
hundred, then the rest -- so each stage re-uses the requests already spent.

**A response is not a price series for the company.** A dead registrant's
ticker is often held by someone else today, and a vendor that splices the two
returns a continuous series that looks complete. The identity test inverts, as
``CLAUDE.md`` warns: each request runs from the registrant's first filing to a
year past its exit, and **bars continuing well past the exit are evidence
against identity, not for coverage**. Grades, failing closed:

    PLAUSIBLE           bars in the registrant's lifetime, last bar near the
                        exit, nothing continuing after it
    SUSPECT_SUCCESSOR   bars in the lifetime AND bars continuing past the exit
    STOPS_EARLY         bars in the lifetime, but ending long before the exit
    NO_BARS_IN_LIFETIME a response with nothing inside the registrant's lifetime
    NOT_FOUND           unknown symbol, or an empty answer
    PAYWALLED           the plan refuses the request
    RATE_LIMITED        the vendor's quota was reached; the stage stops there

Symbols claimed by more than one dated exit are excluded and counted: the later
registrant's bars would read as a successor to the earlier one, and grading
that honestly needs per-bar resolution this probe does not do.

**Keys.** Read from ``.env`` by name and never printed. Tiingo's travels in a
header; FMP's travels in the query string, so every recorded URL and error
passes through :func:`~tradeit.acquisition.redaction.redact_url` and
:func:`~tradeit.acquisition.redaction.redact_text`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.acquisition.redaction import redact_text, redact_url
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import IssuerIdentifier, Security, SymbolAlias

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
SEED = 20260914
#: Same floor rule as research01_backfill_exits.build_windows.
FLOOR = dt.date(1990, 1, 1)
ARCHIVE_START_YEAR = 1994
#: How far past the exit a request runs, so continuation can be seen.
LOOK_PAST = dt.timedelta(days=400)
#: A last bar this close before the exit "reaches" it. Exchanges delist weeks
#: after trading halts, and a Form 15 can trail the last trade by months.
REACH = dt.timedelta(days=180)
#: Bars this far past the exit mean the series belongs to someone later.
CONTINUES = dt.timedelta(days=120)
MIN_BARS = 20
TIMEOUT_S = 30.0


def _keys(env: Path) -> dict[str, str]:
    """The two keys, parsed from .env by name. Never shell-sourced, never printed."""
    wanted = {"TIINGO_API_KEY", "FMP_API_KEY"}
    found: dict[str, str] = {}
    for line in env.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() in wanted:
            found[name.strip()] = value.strip().strip('"').strip("'")
    return found


def _targets(session: Session, exits_path: Path, facts_path: Path) -> tuple[list[dict], Counter]:
    """Pool A at registrant level: resolved ticker, dated exit, no prices."""
    payload = json.loads(exits_path.read_text())
    exits = {int(k): dt.date.fromisoformat(v) for k, v in payload["exits"].items()}
    priced = {int(c) for c in payload["priced"]}
    first_seen = {
        int(c): dt.date.fromisoformat(v)
        for c, v in json.loads(facts_path.read_text())["first_seen"].items()
    }
    rows = session.execute(
        select(IssuerIdentifier.value_normalized, SymbolAlias.alias_value, SymbolAlias.valid_to)
        .join(Security, Security.issuer_id == IssuerIdentifier.issuer_id)
        .join(SymbolAlias, SymbolAlias.security_id == Security.security_id)
        .where(IssuerIdentifier.namespace == "sec_cik", SymbolAlias.alias_kind == "ticker")
    ).all()
    by_symbol: dict[str, dict[int, dict]] = {}
    for cik_text, ticker, valid_to in rows:
        cik = int(cik_text)
        exit_date = exits.get(cik)
        if exit_date is None or cik in priced:
            continue
        ceiling = valid_to - dt.timedelta(days=1) if valid_to is not None else exit_date
        registered = first_seen.get(cik)
        floor = (
            max(FLOOR, registered)
            if registered is not None and registered.year > ARCHIVE_START_YEAR
            else FLOOR
        )
        if ceiling <= floor:
            continue
        symbol = ticker.strip().upper()
        by_symbol.setdefault(symbol, {})[cik] = {
            "symbol": symbol,
            "cik": cik,
            "exit": exit_date,
            "floor": floor,
            "ceiling": ceiling,
        }
    tally: Counter = Counter()
    targets: list[dict] = []
    for registrants in by_symbol.values():
        if len(registrants) > 1:
            tally["multi_registrant_symbols"] += 1
            tally["multi_registrant_exits"] += len(registrants)
            continue
        targets.append(next(iter(registrants.values())))
    targets.sort(key=lambda t: (t["symbol"], t["cik"]))
    random.Random(SEED).shuffle(targets)
    tally["single_registrant_exits"] = len(targets)
    return targets, tally


def _get(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "tradeit-probe", **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read() or b""


def _fetch(vendor: str, target: dict, keys: dict[str, str]) -> tuple[str, list[dt.date], str]:
    """(status, bar dates, redacted note). Status is OK, or a failure grade."""
    start = target["floor"].isoformat()
    end = min(target["exit"] + LOOK_PAST, dt.datetime.now(dt.UTC).date()).isoformat()
    symbol = target["symbol"]
    if vendor == "tiingo":
        key = keys["TIINGO_API_KEY"]
        url = (
            f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
            f"?startDate={start}&endDate={end}&format=json"
        )
        status, body = _get(url, {"Authorization": f"Token {key}"})
    else:
        key = keys["FMP_API_KEY"]
        url = (
            "https://financialmodelingprep.com/stable/historical-price-eod/light"
            f"?symbol={symbol}&from={start}&to={end}&apikey={key}"
        )
        status, body = _get(url, {})
    text = body.decode("utf-8", "replace")
    note = redact_text(text[:240], key)
    lowered = text.lower()
    if status == 429 or "limit reach" in lowered or "too many requests" in lowered:
        return "RATE_LIMITED", [], note
    if status in (401, 402, 403) or "premium" in lowered or "subscription" in lowered:
        return "PAYWALLED", [], note
    if status == 404:
        return "NOT_FOUND", [], note
    if status != 200:
        return f"HTTP_{status}", [], note
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return "MALFORMED", [], note
    if isinstance(decoded, dict):
        # Both vendors answer an unknown or refused symbol with an object.
        return "NOT_FOUND", [], note
    dates: list[dt.date] = []
    for row in decoded if isinstance(decoded, list) else []:
        raw = str(row.get("date", ""))[:10]
        try:
            dates.append(dt.date.fromisoformat(raw))
        except ValueError:
            continue
    if not dates:
        return "NOT_FOUND", [], note
    return "OK", sorted(dates), redact_url(url)


def _grade(target: dict, dates: list[dt.date]) -> str:
    exit_date = target["exit"]
    lifetime = [d for d in dates if target["floor"] <= d <= exit_date + dt.timedelta(days=30)]
    after = [d for d in dates if d > exit_date + CONTINUES]
    if len(lifetime) < MIN_BARS:
        return "NO_BARS_IN_LIFETIME"
    if after:
        return "SUSPECT_SUCCESSOR"
    if lifetime[-1] < exit_date - REACH:
        return "STOPS_EARLY"
    return "PLAUSIBLE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", choices=("tiingo", "fmp"), required=True)
    ap.add_argument("--stage", type=int, required=True, help="probe the first N shuffled targets")
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--exits", type=Path, default=OUT / "dated_exits.json")
    ap.add_argument("--edgar-facts", type=Path, default=OUT / "edgar_facts.json")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between requests")
    ap.add_argument("--show", action="store_true", help="print each target's grade")
    args = ap.parse_args()

    keys = _keys(Path(".env"))
    needed = "TIINGO_API_KEY" if args.vendor == "tiingo" else "FMP_API_KEY"
    if not keys.get(needed):
        print(f"{needed} is not set in .env; refusing to send an unauthenticated request")
        return 2

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    targets, tally = _targets(session, args.exits, args.edgar_facts)
    print(
        f"Pool A: {tally['single_registrant_exits']:,} single-registrant exits probed from; "
        f"{tally['multi_registrant_exits']:,} exits on {tally['multi_registrant_symbols']:,} "
        "shared symbols excluded"
    )

    ledger_path = OUT / f"vendor_probe_{args.vendor}.jsonl"
    done: dict[str, dict] = {}
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            record = json.loads(line)
            if record["grade"] != "RATE_LIMITED":
                done[f"{record['symbol']}|{record['cik']}"] = record

    stage = targets[: args.stage]
    spent = 0
    with ledger_path.open("a") as ledger:
        for target in stage:
            ident = f"{target['symbol']}|{target['cik']}"
            if ident in done:
                continue
            status, dates, note = _fetch(args.vendor, target, keys)
            spent += 1
            grade = _grade(target, dates) if status == "OK" else status
            record = {
                "symbol": target["symbol"],
                "cik": target["cik"],
                "exit": target["exit"].isoformat(),
                "floor": target["floor"].isoformat(),
                "grade": grade,
                "bars": len(dates),
                "first_bar": dates[0].isoformat() if dates else None,
                "last_bar": dates[-1].isoformat() if dates else None,
                "note": note if status != "OK" else "",
                "probed_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            ledger.write(json.dumps(record) + "\n")
            ledger.flush()
            if args.show:
                print(
                    f"  {target['symbol']:<7} cik {target['cik']:<8} exit {target['exit']}  "
                    f"{grade:<20} bars {len(dates):>5}  "
                    f"{record['first_bar'] or '':>10} .. {record['last_bar'] or ''}"
                    + (f"  | {note[:90]}" if status != "OK" else "")
                )
            if grade == "RATE_LIMITED":
                print(f"  quota reached after {spent} requests this run; stopping")
                break
            done[ident] = record
            time.sleep(args.interval)

    graded = [
        done[f"{t['symbol']}|{t['cik']}"] for t in stage if f"{t['symbol']}|{t['cik']}" in done
    ]
    counts = Counter(r["grade"] for r in graded)
    print(
        f"\n{args.vendor}: {len(graded):,} of {len(stage):,} targets graded "
        f"({spent} requests this run)"
    )
    for grade, n in counts.most_common():
        print(f"  {grade:<20} {n:>5}  {n / max(1, len(graded)):6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
