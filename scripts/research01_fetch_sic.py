#!/usr/bin/env python
"""Fetch each issuer's SIC code from the header of one of its filings.

**It asks for the header, not the filing.** EDGAR publishes a ``.hdr.sgml``
beside every submission -- measured at **906 bytes** against filings of 2.4 MB
and 10.3 MB -- stating the code as ``<ASSIGNED-SIC>3571``. Pulling headers out
of full submissions would have moved an estimated **26 GB** from sec.gov to
read about 12 KB of it. This moves about 12 MB.

An HTTP ``Range`` request was tried first and sec.gov ignored it, returning the
whole file with a 200. The dedicated header file is the right way to ask.

**One filing per issuer, and it is the earliest on record.** SIC changes
rarely, and the earliest filing gives the longest span over which the
observation is the best available answer. It is recorded as an *observation*
with its filing date and accession, not as a label -- so a later run that adds
more filings sharpens the history instead of overwriting it.

Resumable by construction: an issuer already carrying an observation for that
accession is skipped, so an interrupted run continues where it stopped.

**A few workers, because the bottleneck is latency, not bandwidth.** Measured
sequentially: 100 issuers in 1.3 minutes, 0.78 s each, of which about 0.66 s is
waiting on sec.gov. Sequentially the full run takes close to three hours while
asking sec.gov for barely one request a second.

**The rate is capped, not estimated, and that is a correction.** The first full
run set four workers from a measured 6.25/s and had no cap. Latency improved as
it went, throughput climbed to 11.8/s, and sec.gov answered with 889 HTTP 429s
and then throttled the address outright -- 1,918 of 12,940 issuers were lost to
it. A worker count is not a rate limit: it is a guess about latency, and it
stops being true the moment latency changes.

:class:`RateGate` enforces an aggregate ceiling across all workers regardless
of how fast any single request returns. The default sits at half the published
limit, because the cost of being slow is minutes and the cost of being rude is
an address that stops being served.

Only fetching is concurrent. Every database write happens on the main thread,
because the session is not thread-safe and a corpus is not the place to find
that out. ``EDGAR_USER_AGENT`` identifies the caller, as SEC fair-access
guidance requires; without it this refuses to issue an anonymous request.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tradeit.edgar.sic import parse_sic_header
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import IssuerSicObservation

SOURCE = "edgar_header_sgml"

#: Bodies that are not an SGML header at all.
#:
#: A throttle page arrives with HTTP 200 often enough to matter, and it
#: contains no ``<ASSIGNED-SIC>`` -- which is indistinguishable from a real
#: header that states no classification unless the body is checked. The first
#: run could not tell them apart and its "no SIC in header" tally climbed from
#: 1% to 18% as the throttling began, which is how the overrun was noticed.
#: **A missing tag in an error page is not evidence that a filing lacks a SIC.**
_HEADER_MARKERS = ("<SEC-HEADER>", "<ASSIGNED-SIC>", "STANDARD INDUSTRIAL CLASSIFICATION")


class RateGate:
    """An aggregate request ceiling, shared across worker threads.

    Enforced rather than estimated. Workers pace themselves against a shared
    next-slot time, so the rate holds whatever the latency does.
    """

    def __init__(self, per_second: float) -> None:
        if per_second <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


#: (issuer_id, cik, filed_at, accession)
PlanRow = tuple[int, str, str, str]


def _header_url(cik: str, accession: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{accession}.hdr.sgml"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--plan", required=True, help="CSV of issuer_id,cik,filed_at,accession")
    ap.add_argument("--limit", type=int, default=None, help="stop after this many fetches")
    ap.add_argument("--workers", type=int, default=4, help="concurrent fetches")
    ap.add_argument(
        "--rate",
        type=float,
        default=5.0,
        help="hard ceiling on requests per second across all workers. SEC "
        "publishes 10; this defaults to half of it.",
    )
    ap.add_argument(
        "--backoff",
        type=float,
        default=2.0,
        help="seconds to wait after a 429 or 503 before the one retry",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    user_agent = os.environ.get("EDGAR_USER_AGENT")
    if not user_agent:
        raise SystemExit(
            "EDGAR_USER_AGENT is not set. SEC fair-access guidance requires a "
            "descriptive User-Agent with contact details, and this script will "
            "not issue an anonymous request."
        )

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()

    with open(args.plan) as handle:
        plan: list[PlanRow] = [
            (int(i), cik, filed, acc) for i, cik, filed, acc in csv.reader(handle)
        ]

    done = set(
        session.execute(
            select(IssuerSicObservation.issuer_id, IssuerSicObservation.accession)
        ).all()
    )
    todo = [row for row in plan if (row[0], row[3]) not in done]
    print(f"plan {len(plan):,}; already recorded {len(done):,}; to fetch {len(todo):,}")
    if args.limit is not None:
        todo = todo[: args.limit]
        print(f"limited to {len(todo):,} this run")
    if args.dry_run:
        for row in todo[:5]:
            print(f"  would GET {_header_url(row[1], row[3])}")
        return 0

    gate = RateGate(args.rate)

    def fetch(row: PlanRow) -> tuple[PlanRow, str | None, str]:
        """One header. Runs on a worker thread and touches no database.

        A 429 or 503 is retried once after a pause. Being throttled is a
        request to slow down, and answering it with an immediate identical
        request is how a slowdown becomes a block.
        """
        for attempt in (1, 2):
            gate.wait()
            try:
                request = urllib.request.Request(
                    _header_url(row[1], row[3]), headers={"User-Agent": user_agent}
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read().decode("utf-8", "replace")
                if not any(marker in body for marker in _HEADER_MARKERS):
                    return row, None, "unrecognised_body"
                return row, body, "ok"
            except urllib.error.HTTPError as error:
                if error.code in (429, 503) and attempt == 1:
                    time.sleep(args.backoff)
                    continue
                return row, None, f"http_{error.code}"
            except Exception as error:
                return row, None, type(error).__name__
        return row, None, "retry_exhausted"

    tally: Counter[str] = Counter()
    started = time.time()
    index = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row, body, status in pool.map(fetch, todo):
            index += 1
            issuer_id, _cik, filed, accession = row
            if body is None:
                tally[status] += 1
            else:
                found = parse_sic_header(body)
                if found is None:
                    # A real case: some headers state no classification at all.
                    tally["no_sic_in_header"] += 1
                else:
                    observed_on = dt.date.fromisoformat(filed)
                    session.add(
                        IssuerSicObservation(
                            issuer_id=issuer_id,
                            sic_code=found.code,
                            sic_description=found.description,
                            division=found.division,
                            observed_on=observed_on,
                            accession=accession,
                            # The header was published with the filing, so that
                            # is when this became knowable. Not "now" -- a
                            # backtest reading this table at an as-of date needs
                            # the earlier instant.
                            knowledge_time=dt.datetime.combine(
                                observed_on, dt.time(21), tzinfo=dt.UTC
                            ),
                            source=SOURCE,
                        )
                    )
                    tally["recorded"] += 1

            if index % 500 == 0:
                session.commit()
                rate = index / (time.time() - started)
                remaining = (len(todo) - index) / rate if rate else 0
                print(
                    f"  {index:>6,}/{len(todo):,}  {rate:>4.1f}/s  "
                    f"~{remaining / 60:>4.1f} min left  {dict(tally)}"
                )

    session.commit()
    print(f"\ndone in {(time.time() - started) / 60:.1f} min: {dict(tally)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
