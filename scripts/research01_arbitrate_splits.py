#!/usr/bin/env python
"""Settle contradicted splits with Sharadar's printed close.

``RESEARCH_01_DATA_DICTIONARY.md`` §0.1a: at 1,872 recorded splits the corpus's
own prints contradict each other or the recorded ratio, so
:func:`~tradeit.research01.series.split_evidence` can decide nothing and the
reads withhold the 1,634,744 prints that precede them.

Sharadar publishes both the **printed** close (``closeunadj``) and the
split-adjusted one (``close``). On the last session before an ex-date they
differ by the cumulative split factor, so comparing what this corpus stores as
``raw`` against the two settles the question:

    stored raw == Sharadar printed close    -> the split is genuine, apply it
    stored raw == Sharadar adjusted close   -> raw is already adjusted, skip it

**Where Sharadar shows no adjustment on that session the verdict is withheld.**
The two closes are then equal and the comparison proves nothing -- measured at
10% of a 120-split sample. Nothing is guessed, and a split with no verdict keeps
withholding what precedes it.

**Nothing is overwritten.** Verdicts are appended to
``security_split_price_verdicts`` with the three closes they rest on, so the
call can be re-checked without re-fetching, and a later source that disagrees
becomes a newer revision rather than an edit.

Staged like every other run here: ``--limit 5``, then ``--limit 100``, then all.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
import time
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from research01_probe_sharadar import OUT, TICKERS, _get, _key, _rows
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.research01 import split_evidence
from tradeit.research01.series import SplitEvidence
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import (
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SecuritySplitPriceVerdict,
)

CIK_IN_URL = re.compile(r"CIK=0*(\d+)")
#: Two closes within this of each other are the same number as far as a vendor's
#: rounding is concerned -- and, when printed and adjusted agree, are evidence of
#: nothing.
TOLERANCE = 0.02
PROGRESS = OUT / "split_arbitration_progress.json"


def _sharadar_rows(key: str, ticker: str, day: str) -> list[dict]:
    status, text = _get(
        f"stocks?ticker={ticker}&date.gte={day}&date.lte={day}&fields=date,close,closeunadj", key
    )
    return _rows(text) if status == 200 else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    key = _key()
    if not key:
        print("SHARADAR_API_KEY is not set in .env")
        return 2
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    by_cik: dict[int, list[dict]] = collections.defaultdict(list)
    for row in json.loads(TICKERS.read_text()):
        found = CIK_IN_URL.search(row.get("secfilings") or "")
        if found:
            by_cik[int(found.group(1))].append(row)
    cik_of = dict(
        session.execute(
            select(Security.security_id, IssuerIdentifier.value_normalized)
            .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
            .where(IssuerIdentifier.namespace == "sec_cik")
        ).all()
    )

    splits = session.execute(
        select(
            SecurityCorporateActionFact.security_id,
            SecurityCorporateActionFact.ex_date,
            SecurityCorporateActionFact.ratio,
        ).where(
            SecurityCorporateActionFact.action_type.in_(("split", "reverse_split")),
            SecurityCorporateActionFact.ratio > 0,
        )
    ).all()
    done: set[str] = set(json.loads(PROGRESS.read_text())) if PROGRESS.exists() else set()
    todo = []
    for security_id, ex_date, ratio in splits:
        token = f"{security_id}|{ex_date}"
        if token in done:
            continue
        if split_evidence(session, security_id, ex_date, ratio) is SplitEvidence.CONTRADICTED:
            todo.append((security_id, ex_date))
    print(
        f"contradicted splits without a verdict: {len(todo):,}; "
        f"this run {min(len(todo), args.limit):,}"
    )

    tally: collections.Counter[str] = collections.Counter()
    now = dt.datetime.now(dt.UTC)
    for security_id, ex_date in todo[: args.limit]:
        token = f"{security_id}|{ex_date}"
        cik = cik_of.get(security_id)
        candidates = [
            row
            for row in by_cik.get(int(cik), [])
            if cik
            and (row.get("firstpricedate") or "9999") < str(ex_date)
            and (row.get("lastpricedate") or "9999") >= str(ex_date)
        ]
        if not candidates:
            tally["no Sharadar series covering the ex-date"] += 1
            done.add(token)
            continue
        prior = session.execute(
            select(SecurityPriceFact.session_date, SecurityPriceFact.close)
            .where(
                SecurityPriceFact.security_id == security_id,
                SecurityPriceFact.adjustment_basis == "raw",
                SecurityPriceFact.close > 0,
                SecurityPriceFact.session_date < ex_date,
            )
            .order_by(SecurityPriceFact.session_date.desc())
            .limit(1)
        ).first()
        if prior is None:
            tally["no stored raw before the ex-date"] += 1
            done.add(token)
            continue
        day, ours = prior[0], float(prior[1])
        rows = _sharadar_rows(key, candidates[0]["ticker"], day.isoformat())
        time.sleep(args.interval)
        if not rows or not rows[0].get("closeunadj") or not rows[0].get("close"):
            tally["Sharadar has no bar that session"] += 1
            done.add(token)
            continue
        printed, adjusted = float(rows[0]["closeunadj"]), float(rows[0]["close"])
        if abs(printed / adjusted - 1) < TOLERANCE:
            # The two agree, so matching either proves nothing.
            tally["undecidable: Sharadar shows no adjustment yet"] += 1
            done.add(token)
            continue
        if abs(ours / printed - 1) < TOLERANCE:
            verdict = "in_raw"
        elif abs(ours / adjusted - 1) < TOLERANCE:
            verdict = "already_adjusted"
        else:
            tally["undecidable: stored raw matches neither"] += 1
            done.add(token)
            continue
        session.add(
            SecuritySplitPriceVerdict(
                security_id=security_id,
                ex_date=ex_date,
                verdict=verdict,
                decided_by="sharadar",
                compared_session=day,
                stored_raw_close=Decimal(str(ours)),
                vendor_printed_close=Decimal(str(printed)),
                vendor_adjusted_close=Decimal(str(adjusted)),
                knowledge_time=now,
                citation=f"sharadar stocks {candidates[0]['ticker']} {day.isoformat()}",
                source="sharadar-arbitration",
            )
        )
        session.commit()
        done.add(token)
        PROGRESS.write_text(json.dumps(sorted(done)))
        tally[f"decided: {verdict}"] += 1
        if args.show:
            print(
                f"  sec {security_id:<6} {ex_date}  stored {ours:<10.4f} printed {printed:<10.4f} "
                f"adjusted {adjusted:<10.4f} -> {verdict}"
            )
    PROGRESS.write_text(json.dumps(sorted(done)))
    print(json.dumps(dict(tally), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
