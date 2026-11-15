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
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradeit.forward.discontinuity import DISCONTINUITY_RATIO, scan_record

__all__ = ["add_forward_commands"]

#: Where the unattended capture writes. Named here so the command works with no
#: arguments, which is the difference between a diagnostic that gets run and one
#: that does not.
DEFAULT_RECORD = Path("/Users/ericsasson/Documents/TradeItData/out/forward.sqlite")


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

    # **Four slashes.** `sqlite:///` plus an absolute path needs the leading
    # slash of the path too, and `sqlite:///Users/...` is read as *relative*,
    # which produced an empty database and an error that never said so.
    engine = create_engine(f"sqlite:////{record.as_posix().lstrip('/')}", future=True)
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
