#!/usr/bin/env python
"""Adjudicate the research-01 series whose shape says they hold two companies.

``series.py`` measured that 9.2% of the corpus's price series end more than
seven years after their registrant's last EDGAR filing, and deliberately stopped
there: :func:`series_coherence` reports and never filters, because truncating on
a shape would discard legitimate post-delisting trading and dropping would hide
a splice rather than name it.

This is the next step. It gathers the evidence each series can be judged on,
runs :func:`~tradeit.research01.adjudicate.adjudicate_series`, and -- with
``--apply`` -- closes the ticker's ``valid_to`` at the boundary the evidence
located, citing how. Nothing is deleted: the bars stay, and the interval says
which of them belong to this security. Discarding them would destroy the record
of a defect the corpus took three attempts to find.

    PYTHONPATH=src .venv/bin/python scripts/research01_adjudicate.py            # report
    PYTHONPATH=src .venv/bin/python scripts/research01_adjudicate.py --apply    # and write

The EDGAR pass is the slow part and produces two things at once: the registrant's
last lifecycle-relevant filing, and its confirmed dated exit where one exists.
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
from sqlalchemy.orm import Session, sessionmaker

from tradeit.edgar.index import IndexQuarter
from tradeit.edgar.pipeline import BuildOptions, build_denominator
from tradeit.research01.adjudicate import (
    Adjudication,
    Verdict,
    adjudicate_series,
    close_alias_interval,
    detect_regime_break,
)
from tradeit.research01.confirm import comparison_form
from tradeit.storage.tables import (
    IssuerIdentifier,
    Security,
    SecurityCorporateActionFact,
    SecurityPriceFact,
    SymbolAlias,
)

DEFAULT_INDEX = "/Users/ericsasson/Documents/TradeItData/edgar/full-index"
DEFAULT_TICKERS = "/Users/ericsasson/Documents/TradeItData/edgar/reference/company_tickers.json"


def _current_holders(path: Path) -> dict[str, int]:
    """Plain ticker -> the CIK that holds it today, from the SEC's own file.

    Covers only what is listed *now*, which is why it answers so few of these
    and why nothing here depends on it alone. The question it does answer is
    exactly the one asked: has somebody else got this symbol?
    """
    raw = json.loads(path.read_text())
    entries = raw.values() if isinstance(raw, dict) else raw
    holders: dict[str, int] = {}
    for entry in entries:
        holders.setdefault(str(entry["ticker"]).upper(), int(entry["cik_str"]))
    return holders


def _edgar_facts(
    index_root: Path, as_of: dt.date, cache: Path | None = None
) -> tuple[dict[int, dt.date], dict[int, dt.date]]:
    """One pass over the index: last lifecycle filing, and confirmed dated exits.

    The pass takes minutes and its inputs do not move between runs, so ``cache``
    exists to make re-running the adjudication cheap. It is a cache of a
    measurement, never a substitute for one: delete it whenever the archive or
    the resolution rules change.
    """
    if cache is not None and cache.exists():
        blob = json.loads(cache.read_text())
        return (
            {int(k): dt.date.fromisoformat(v) for k, v in blob["last_seen"].items()},
            {int(k): dt.date.fromisoformat(v) for k, v in blob["exits"].items()},
        )
    denominator = build_denominator(
        BuildOptions(
            index_root=index_root,
            start=IndexQuarter(1994, 3),
            end=IndexQuarter(2026, 2),
            as_of=as_of,
        )
    )
    last_seen = {cik: t.last_seen for cik, t in denominator.timelines.items()}
    exits = {
        r.cik: r.evidence_date
        for r in denominator.resolutions
        if r.is_confirmed and r.evidence_date is not None
    }
    if cache is not None:
        cache.write_text(
            json.dumps(
                {
                    "last_seen": {str(k): str(v) for k, v in last_seen.items()},
                    "exits": {str(k): str(v) for k, v in exits.items()},
                }
            )
        )
    return last_seen, exits


def _corpus(session: Session) -> list[tuple[int, int, str | None]]:
    """(security_id, cik, ticker) for every security holding at least one bar."""
    rows = session.execute(
        select(Security.security_id, IssuerIdentifier.value_normalized, SymbolAlias.alias_value)
        .join(IssuerIdentifier, IssuerIdentifier.issuer_id == Security.issuer_id)
        .join(SecurityPriceFact, SecurityPriceFact.security_id == Security.security_id)
        .outerjoin(
            SymbolAlias,
            (SymbolAlias.security_id == Security.security_id)
            & (SymbolAlias.alias_kind == "ticker"),
        )
        .where(IssuerIdentifier.namespace == "sec_cik")
        .distinct()
    ).all()
    return [(int(sid), int(cik), ticker) for sid, cik, ticker in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-root", default=DEFAULT_INDEX)
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--as-of", default="2026-09-01")
    ap.add_argument("--apply", action="store_true", help="write the located boundaries")
    ap.add_argument("--out", default="", help="write the full per-security verdicts as JSON")
    ap.add_argument(
        "--edgar-cache", default="", help="cache the index pass here; delete it to remeasure"
    )
    args = ap.parse_args()

    as_of = dt.date.fromisoformat(args.as_of)
    session = sessionmaker(bind=create_engine(args.db, future=True), future=True)()

    holders = _current_holders(Path(args.tickers))
    print(f"company_tickers.json: {len(holders):,} symbols listed today")

    print("one pass over the EDGAR index (last filing + confirmed dated exits) ...")
    last_seen, exits = _edgar_facts(
        Path(args.index_root), as_of, Path(args.edgar_cache) if args.edgar_cache else None
    )
    print(f"  timelines {len(last_seen):,}   confirmed dated exits {len(exits):,}")

    results: list[tuple[int, int, str | None, Adjudication]] = []
    for security_id, cik, ticker in _corpus(session):
        rows = session.execute(
            select(
                SecurityPriceFact.session_date,
                SecurityPriceFact.close,
                SecurityPriceFact.volume,
            )
            .where(
                SecurityPriceFact.security_id == security_id,
                SecurityPriceFact.adjustment_basis == "raw",
            )
            .distinct()
            .order_by(SecurityPriceFact.session_date)
        ).all()
        # Dormancy asks whether the vendor emitted a row at all, so it must see
        # EVERY session -- dropping a zero or absent close here would manufacture
        # a hole and with it a boundary. The regime detector needs positive
        # closes to take a ratio, and gets its own narrower list.
        sessions = [row[0] for row in rows]
        bars = [(d, float(c), float(v or 0)) for d, c, v in rows if c is not None and float(c) > 0]
        anchor = last_seen.get(cik)
        exit_date = exits.get(cik)
        if anchor and exit_date:
            anchor = max(anchor, exit_date)
        elif exit_date:
            anchor = exit_date
        splits = list(
            session.scalars(
                select(SecurityCorporateActionFact.ex_date).where(
                    SecurityCorporateActionFact.security_id == security_id,
                    SecurityCorporateActionFact.action_type.in_(("split", "reverse_split")),
                )
            ).all()
        )
        plain = comparison_form(ticker) if ticker else ""
        verdict = adjudicate_series(
            sessions=sessions,
            last_filing=last_seen.get(cik),
            filed_exit=exit_date,
            registrant_cik=cik,
            current_holder_cik=holders.get(plain),
            regime_break=detect_regime_break(
                bars,
                after=anchor,
                split_ex_dates=[d for d in splits if d is not None],
            ),
        )
        results.append((security_id, cik, ticker, verdict))

    counts = collections.Counter(v.verdict for _, _, _, v in results)
    print("\n=== verdicts over every priced security ===")
    for verdict in Verdict:
        print(f"  {verdict.value:<32} {counts.get(verdict, 0):>5}")

    actionable = [r for r in results if r[3].is_actionable]
    dropped = sum(r[3].dropped_bars for r in actionable)
    print(
        f"\nboundaries located: {len(actionable)}   bars they place outside the "
        f"security's interval: {dropped:,}"
    )

    print("\n=== every located boundary ===")
    for security_id, cik, ticker, verdict in sorted(actionable, key=lambda r: -r[3].dropped_bars):
        print(
            f"  {ticker!s:<12} sid {security_id:>4} cik {cik:>8} "
            f"{verdict.verdict.value:<22} boundary {verdict.boundary} "
            f"keep {verdict.kept_bars:>5} drop {verdict.dropped_bars:>5}"
        )

    unrepaired = [r for r in results if r[3].verdict is Verdict.CONTAMINATED_BOUNDARY_UNKNOWN]
    if unrepaired:
        print("\n=== known wrong, and NOT repairable: no evidence dates the handover ===")
        for security_id, cik, ticker, verdict in unrepaired:
            print(f"  {ticker!s:<12} sid {security_id:>4} cik {cik:>8}  {verdict.note}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                [
                    {
                        "security_id": sid,
                        "cik": cik,
                        "ticker": ticker,
                        "verdict": v.verdict.value,
                        "boundary": str(v.boundary) if v.boundary else None,
                        "evidence": [e.value for e in v.evidence],
                        "kept_bars": v.kept_bars,
                        "dropped_bars": v.dropped_bars,
                        "dormancy_days": v.dormancy_days,
                        "note": v.note,
                    }
                    for sid, cik, ticker, v in results
                ],
                indent=1,
            )
        )
        print(f"\nwrote {args.out}")

    if not args.apply:
        print("\n(report only; pass --apply to close the intervals)")
        return 0

    written = _apply(session, actionable, last_seen, as_of)
    session.commit()
    print(f"\nclosed {written} ticker intervals")
    return 0


def _apply(
    session: Session,
    actionable: list[tuple[int, int, str | None, Adjudication]],
    last_seen: dict[int, dt.date],
    as_of: dt.date,
) -> int:
    """Close each located interval, citing how the boundary was reached.

    The refusals -- never widen an existing close, never write an empty
    interval, never replace a citation -- live in ``close_alias_interval``
    beside the rules, because the successor search writes boundaries too and
    two copies would be two places for them to be forgotten.
    """
    written = 0
    for security_id, cik, _ticker, verdict in actionable:
        # WHOLLY_MISATTRIBUTED locates no boundary inside the series, so the
        # interval closes at the last date the binding is evidenced at all.
        boundary = verdict.boundary or last_seen.get(cik)
        if boundary is None:
            continue
        written += close_alias_interval(
            session,
            security_id,
            boundary,
            f"interval closed {boundary} on {as_of} by research01.adjudicate "
            f"({verdict.verdict.value}; evidence "
            f"{'+'.join(e.value for e in verdict.evidence)}): {verdict.note}",
        )
    return written


if __name__ == "__main__":
    raise SystemExit(main())
