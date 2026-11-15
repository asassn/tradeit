"""``tradeit forward ...`` -- read the forward record, compute nothing it owns.

A command group rather than one flat command, deliberately. The forward record
has more questions queued against it than this one -- revisions, session
coverage -- and renaming a flat ``forward-discontinuities`` later is churn for
no gain. Where `cli_status.py` reports the whole system and computes nothing,
this reads one record and answers one question about it.

**The library function is the primary and this is a thin wrapper.** The nightly
job in `ops/forward_daily.py` calls
:func:`~tradeit.forward.discontinuity.scan_record` directly, because routing an
unattended job through argparse to get a string back would make the one piece of
Python nobody watches run depend on a CLI surface.
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.forward.discontinuity import DISCONTINUITY_RATIO, scan_record
from tradeit.forward.silence import classify_silences, load_index_filings

__all__ = ["add_forward_commands"]

#: Where the unattended capture writes. Named here so the command works with no
#: arguments, which is the difference between a diagnostic that gets run and one
#: that does not.
DEFAULT_RECORD = Path("/Users/ericsasson/Documents/TradeItData/out/forward.sqlite")


def _sqlite_url(path: Path) -> str:
    """An absolute ``sqlite://`` URL for a local file.

    **Resolved first, and that is the whole point.** ``sqlite:///`` plus a
    relative path is read relative to the process's working directory, and
    ``sqlite:////`` plus one is read from the filesystem root -- so
    ``sqlite:////research01.sqlite`` looks for ``/research01.sqlite`` and fails
    with "unable to open database file", an error that never mentions the path
    it actually tried. That has cost this project time once already; the default
    corpus argument is a relative name, so it would have cost it again.
    """
    return f"sqlite:///{path.expanduser().resolve()}"


def cmd_discontinuities(args: argparse.Namespace) -> int:
    record = Path(args.record)
    if not record.exists():
        print(f"no record at {record}")
        return 2
    try:
        ratio = Decimal(str(args.ratio))
    except InvalidOperation:
        print(f"--ratio must be a number, not {args.ratio!r}")
        return 2
    if ratio <= 1:
        # A threshold of 1 or less flags every pair, which is not a lower
        # sensitivity but a different and useless report.
        print("--ratio must exceed 1; a direction-free ratio of 1 is no move at all")
        return 2

    engine = create_engine(_sqlite_url(record), future=True)
    with sessionmaker(bind=engine, future=True)() as session:
        scan = scan_record(session, ratio=ratio)
    print(scan.describe())
    if ratio != DISCONTINUITY_RATIO:
        print(
            f"\n  note: run at {ratio}x rather than the derived {DISCONTINUITY_RATIO}x. "
            "See tradeit.forward.discontinuity for the derivation; a threshold moved "
            "to quiet a flag is not a measurement."
        )
    # **Zero even when it finds something.** This reports; it does not gate. A
    # non-zero exit would make the nightly job fail on a true finding, and a job
    # that fails on a correct observation gets its reporting switched off.
    return 0


def add_forward_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    forward = sub.add_parser("forward", help="read the forward record")
    inner = forward.add_subparsers(dest="forward_command", required=True)

    scan = inner.add_parser(
        "discontinuities",
        help="report single-session moves too large to be trading (report only)",
    )
    scan.add_argument(
        "--record",
        default=str(DEFAULT_RECORD),
        help="the forward record to read (default: the unattended capture's)",
    )
    scan.add_argument(
        "--ratio",
        default=str(DISCONTINUITY_RATIO),
        help=(
            "direction-free threshold; a price at least this multiple up or down "
            "between consecutive sessions. The default is derived from the record "
            "and should not be moved to silence a flag."
        ),
    )
    scan.set_defaults(func=cmd_discontinuities)

    silences = inner.add_parser(
        "silences",
        help="say whether each silent symbol really stopped trading (report only)",
    )
    silences.add_argument("--record", default=str(DEFAULT_RECORD), help="the forward record")
    silences.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help="read only, and only to resolve a symbol to its issuer's CIK",
    )
    silences.add_argument(
        "--index-root",
        default=str(DEFAULT_INDEX_ROOT),
        help="directory of <year>/QTR<n>/form.idx",
    )
    silences.add_argument(
        "--max-index-age",
        type=int,
        default=14,
        help=(
            "days before the filing index is called stale in the report. It is "
            "reported every run regardless; this only decides whether it is flagged."
        ),
    )
    silences.set_defaults(func=cmd_silences)


#: Where the EDGAR full-index lives. Named here for the same reason as
#: `DEFAULT_RECORD`: a diagnostic that needs arguments is a diagnostic nobody
#: runs, and this one went three months without being run at all.
DEFAULT_INDEX_ROOT = Path("/Users/ericsasson/Documents/TradeItData/edgar/full-index")
DEFAULT_CORPUS = Path("research01.sqlite")

#: How far back to read the filing index. Must cover `RENAME_WINDOW_DAYS` plus
#: the exit window, with room for a silence that is itself weeks old.
SILENCE_LOOKBACK_DAYS = 260


def _silent_symbols(session: Session) -> dict[str, dt.date | None]:
    """Symbols that did not print on the latest session the record asked about.

    **A symbol that never printed is included**, with ``None``. It is a
    different question -- it was dead before the universe was written rather
    than dying in front of us -- and `classify_silence` says so rather than
    inventing a stop date for it.
    """
    latest = session.execute(text("select max(session_date) from forward_observations")).scalar()
    if latest is None:
        return {}
    rows = session.execute(
        text(
            "select symbol, max(case when rows_returned > 0 then session_date end) as last_print "
            "from forward_observations group by symbol "
            "having last_print is null or last_print < :latest"
        ),
        {"latest": latest},
    ).all()
    out: dict[str, dt.date | None] = {}
    for symbol, last_print in rows:
        out[str(symbol)] = dt.date.fromisoformat(str(last_print)[:10]) if last_print else None
    return out


def _ciks_for(corpus: Session, symbols: Iterable[str]) -> dict[str, int | None]:
    """Resolve each symbol to the CIK of the issuer behind it, or ``None``.

    Read from the corpus because that is where identity lives; the forward
    record deliberately holds no issuers, so that forward data cannot start a
    second, parallel universe of identifiers.

    **Returns ``None`` rather than guessing** when a symbol resolves to no
    security, several securities, or an issuer with no ``sec_cik``. A guessed
    CIK here would attach one company's filings to another's silence, which is
    the worst possible failure for a check whose whole job is identity.
    """
    out: dict[str, int | None] = {}
    for symbol in symbols:
        rows = (
            corpus.execute(
                text(
                    "select distinct ii.value from symbol_aliases sa "
                    "join securities s on s.security_id = sa.security_id "
                    "join issuer_identifiers ii on ii.issuer_id = s.issuer_id "
                    "where sa.alias_value = :sym and ii.namespace = 'sec_cik'"
                ),
                {"sym": symbol},
            )
            .scalars()
            .all()
        )
        unique = {str(value).strip() for value in rows if str(value).strip().isdigit()}
        out[symbol] = int(next(iter(unique))) if len(unique) == 1 else None
    return out


def cmd_silences(args: argparse.Namespace) -> int:
    record = Path(args.record)
    if not record.exists():
        print(f"no record at {record}")
        return 2
    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"no corpus at {corpus_path}; it is where issuer identity lives")
        return 2

    engine = create_engine(_sqlite_url(record), future=True)
    with sessionmaker(bind=engine, future=True)() as session:
        silent = _silent_symbols(session)
    if not silent:
        print("no silent symbols in the record")
        return 0

    corpus_engine = create_engine(_sqlite_url(corpus_path), future=True)
    with sessionmaker(bind=corpus_engine, future=True)() as corpus:
        ciks = _ciks_for(corpus, silent)

    # UTC rather than the local clock: the index's age is a property of the
    # archive, not of where the operator is sitting, and a naive `date.today()`
    # would report a different age either side of midnight local.
    today = dt.datetime.now(dt.UTC).date()
    filings, through = load_index_filings(
        Path(args.index_root),
        since=today - dt.timedelta(days=SILENCE_LOOKBACK_DAYS),
        until=today,
        ciks=[c for c in ciks.values() if c is not None],
    )

    # **Printed every run, not only when something fails.** The index was three
    # months stale on 2026-11-15 and nothing said so; every answer it gave was
    # confident and wrong-by-omission. An age printed nightly is the cheapest
    # possible guard against that recurring.
    if through is None:
        print(f"filing index at {args.index_root}: EMPTY -- no row was read at all")
    else:
        age = (today - through).days
        flag = "  <-- STALE, refresh it" if age > args.max_index_age else ""
        print(f"filing index reaches {through} ({age} day(s) old){flag}")

    findings = classify_silences(
        {sym: (ciks[sym], stop) for sym, stop in silent.items()},
        filings,
        index_through=through,
    )
    print(f"\n{len(findings)} silent symbol(s), the ones that are not exits first:\n")
    for finding in findings:
        print(finding.describe())
        print()
    wrong = [f for f in findings if f.verdict.contradicts_an_exit]
    if wrong:
        print(
            f"{len(wrong)} silence(s) are NOT delistings: "
            f"{', '.join(f.symbol for f in wrong)}. Writing them into a corpus as "
            "survivorship exits would be a false delisting in the one record built "
            "not to have any. This reports; a person adjudicates."
        )
    # Zero regardless. See `cmd_discontinuities`: a job that goes red on a
    # correct observation is a job whose reporting gets switched off.
    return 0
