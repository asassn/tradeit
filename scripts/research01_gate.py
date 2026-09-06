#!/usr/bin/env python
"""Milestone 8: measure research-01 against the EDGAR denominator.

**The question this answers, and the one it cannot.** The denominator counts
registrants that EDGAR shows exiting. This corpus holds prices for some of them.
Dividing the second by the first measures how much of the *dead* population we
can actually price -- which is the survivorship question.

What it cannot answer is whether the corpus is survivorship-safe in general,
for two reasons that are stated in the output rather than left to a reader:

* **The denominator is blind to exchange delistings before 2006**
  (`EDGAR_DELISTING_DENOMINATOR.md` §7be): 1994-2001 contains no listing
  evidence at all, only Form 15 reporting exits. A coverage ratio measured
  against it in that window is a ratio against reporting exits.
* **The cohort was selected by name-matchability**, not at random. Companies
  whose EDGAR name matched a vendor symbol are over-represented by
  construction, so coverage of them is not an estimate of coverage overall.

Reported with its limitations, never asserted as a grade the evidence does not
support.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from tradeit.edgar.denominator import CoverageBounds, classify_corpus
from tradeit.edgar.index import IndexQuarter
from tradeit.edgar.pipeline import BuildOptions, build_denominator
from tradeit.storage.tables import (
    IssuerIdentifier,
    Security,
    SecurityPriceFact,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--index-root", default="/Users/ericsasson/Documents/TradeItData/edgar/full-index"
    )
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--as-of", default="2026-09-01")
    args = ap.parse_args()

    session = sessionmaker(bind=create_engine(args.db, future=True), future=True)()

    # Which CIKs do we hold PRICES for? Holding an identity is not holding data,
    # and conflating the two would count a company we cannot price.
    priced = {
        int(v)
        for v in session.scalars(
            select(IssuerIdentifier.value_normalized)
            .join(Security, Security.issuer_id == IssuerIdentifier.issuer_id)
            .join(SecurityPriceFact, SecurityPriceFact.security_id == Security.security_id)
            .where(IssuerIdentifier.namespace == "sec_cik")
            .distinct()
        ).all()
    }
    print(f"CIKs in research-01 with at least one price bar: {len(priced):,}")

    print("building the denominator from EDGAR ...")
    denom = build_denominator(
        BuildOptions(
            index_root=Path(args.index_root),
            start=IndexQuarter(1994, 3),
            end=IndexQuarter(2026, 2),
            as_of=dt.date.fromisoformat(args.as_of),
        )
    )

    all_dated = {
        r.cik: r for r in denom.resolutions if r.is_confirmed and r.evidence_date is not None
    }
    # Registrants whose only periodic filings are Investment Company Act forms
    # are scoped OUT of the coverage denominator and stay fully dated. Measured
    # 2026-09-06 (§7g): of the 1,811 the N-8F recognition added, 11 had ever
    # registered a class on an exchange and 1,800 had not. They are denominator
    # that can never have a numerator -- not a coverage failure, a different
    # population -- and counting them depresses coverage for a reason unrelated
    # to survivorship. Their exits remain resolved and dated so a fund corpus
    # can use them; only this ratio excludes them.
    dated_exits = {cik: r for cik, r in all_dated.items() if r.is_exchange_act}
    funds = len(all_dated) - len(dated_exits)
    print(f"denominator dated confirmed exits: {len(all_dated):,}")
    print(
        f"  scoped out, Investment Company Act reporting only: {funds:,} "
        f"({funds / len(all_dated):.1%}) -- still dated, see §7g"
    )
    print(f"  coverage denominator (Exchange Act reporters): {len(dated_exits):,}")

    covered = priced & set(dated_exits)
    print(
        f"  of those, priced by research-01: {len(covered):,} "
        f"({100 * len(covered) / len(dated_exits):.2f}%)"
    )

    print("\n=== coverage of dated exits, by exit year ===")
    by_year: dict[int, list[int]] = collections.defaultdict(list)
    for cik, r in dated_exits.items():
        assert r.evidence_date is not None
        by_year[r.evidence_date.year].append(cik)
    for year in sorted(by_year):
        if not (1998 <= year <= 2005):
            continue
        names = by_year[year]
        hit = len(set(names) & priced)
        print(f"  {year}  {hit:>5,} of {len(names):>6,}  ({100 * hit / len(names):5.2f}%)")

    # full_denominator stays len(denom.resolutions) by the owner's decision of
    # 2026-09-05 (§7e): the pessimistic reading is kept deliberately, and this
    # script does not quietly change it.
    bounds = CoverageBounds(
        matched_numerator=len(covered),
        resolved_denominator=len(dated_exits),
        full_denominator=len(denom.resolutions),
    )
    classification = classify_corpus(bounds, controls_passed=30, controls_total=30)
    print("\n=== classification ===")
    print(json.dumps(classification.summary(), indent=1, default=str))

    print("\n=== limitations, which are part of the result ===")
    print("  * The denominator holds NO exchange-listing evidence before 2006")
    print("    (§7be). For 1998-2001 these are Form 15 reporting exits only, so")
    print("    the ratio above is coverage of REPORTING exits, not delistings.")
    print("  * The cohort was selected by name-matchability, not at random, so")
    print("    its coverage is not an estimate of coverage over the population.")
    print("  * Holding a price bar is not the same as holding a COMPLETE series;")
    print("    completeness per name is not measured here.")
    print("  * Registrants reporting only under the Investment Company Act are")
    print("    excluded from the coverage denominator and remain dated (§7g).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
