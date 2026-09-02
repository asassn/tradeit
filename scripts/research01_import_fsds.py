#!/usr/bin/env python
"""Load the SEC Financial Statement Data Sets into research-01.

The reader and the importer already existed and were tested; **what did not
exist was anything that called them**, which is why
``security_fundamental_facts`` held zero rows while 5.3 GB of quarterly
archives sat on disk. This is that runner.

**Measure the yield before believing it.** Only CIKs already carrying evidenced
identity resolve -- the corpus is never grown by an import -- and research-01's
identity is a dot-com cohort that stopped filing between 1998 and 2005. The
Data Sets begin in 2009. Fifteen of the corpus's 891 CIKs appear in them at
all, and they are the large survivors: Apple, Microsoft, Amazon, Cisco, GM.
**A fundamentals set consisting entirely of survivors, attached to a corpus
built to avoid survivorship bias, is worse than none** -- so what lands here is
labelled for what it is and is not a fundamentals spine.

Resumable per quarter, because 24.5 GB of uncompressed ``num.txt`` is long
enough that an interruption should not cost the run.

    PYTHONPATH=src .venv/bin/python scripts/research01_import_fsds.py --dry-run
    PYTHONPATH=src .venv/bin/python scripts/research01_import_fsds.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from tradeit.research01.fsds import import_fsds_quarter, read_quarter
from tradeit.storage.tables import IssuerIdentifier, SecurityFundamentalFact

DEFAULT_FSDS = "/Users/ericsasson/Documents/TradeItData/edgar/fsds"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fsds", default=DEFAULT_FSDS)
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--progress", default=".research01_fsds.json")
    ap.add_argument("--dry-run", action="store_true", help="report the yield, write nothing")
    args = ap.parse_args()

    session = sessionmaker(bind=create_engine(args.db, future=True), future=True)()
    ours = {
        int(v)
        for v in session.scalars(
            select(IssuerIdentifier.value_normalized).where(IssuerIdentifier.namespace == "sec_cik")
        ).all()
    }
    print(f"CIKs with evidenced identity: {len(ours):,}")

    quarters = sorted(Path(args.fsds).glob("*.zip"))
    print(f"quarterly archives on disk: {len(quarters)}")

    if args.dry_run:
        # sub.txt only -- a few megabytes a quarter against num.txt's half a
        # gigabyte. The point is the yield, and sub.txt alone decides it.
        total = 0
        for path in quarters:
            submissions, _facts = read_quarter(path)
            hit = {s.cik for s in submissions.values()} & ours
            total += len(hit)
            flag = "" if hit else "   (nothing to load)"
            print(f"  {path.stem}: {len(submissions):>6,} filings, {len(hit):>3} ours{flag}")
        print(f"\nfilings-by-our-issuers across all quarters: {total:,} CIK-quarters")
        return 0

    done = (
        set(json.loads(Path(args.progress).read_text())) if Path(args.progress).exists() else set()
    )
    landed_total = 0
    started = time.time()
    for path in quarters:
        if path.stem in done:
            continue
        result = import_fsds_quarter(session, path)
        session.commit()
        landed_total += result.landed
        done.add(path.stem)
        Path(args.progress).write_text(json.dumps(sorted(done)))
        print(
            f"  {path.stem}: landed {result.landed:>7,}  "
            f"securities {len(result.securities_touched):>3}  "
            f"unmapped registrants {len(result.rejected):>6,}  "
            f"[{time.time() - started:6.0f}s]",
            flush=True,
        )

    rows = session.scalar(select(func.count()).select_from(SecurityFundamentalFact)) or 0
    secs = (
        session.scalar(select(func.count(func.distinct(SecurityFundamentalFact.security_id)))) or 0
    )
    print(f"\nlanded this run: {landed_total:,}")
    print(f"security_fundamental_facts now: {rows:,} rows over {secs} securities")
    print(
        "\nWhat this is and is not: only issuers with evidenced identity resolve, and "
        "research-01's identity is a cohort that stopped filing before the Data Sets "
        "begin. These rows are the surviving controls, not a fundamentals spine."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
