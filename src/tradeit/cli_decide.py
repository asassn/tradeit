"""``tradeit plan`` — what one portfolio should do today, and what it refused.

The first command in this project that produces a **plan for a declared
portfolio** — what it holds, what it would do, and what it refused — rather
than a backtest result. The ranker it uses has occupied the allocation seat
since ADR-0027; what had no surface was the decision itself. Everything it prints comes from
:func:`tradeit.decide.plan_declared`, which is shared with the web service so
the two cannot drift: a second copy of that wiring would be a second set of
answers to *which ranker, which rules, which declared score range* — and the
whole point of the ranker swap is that those answers are now consequential.

Two ways to say what the portfolio is, and they are the same thing underneath.
``--declaration`` reads a JSON file holding one entry per horizon, which is
what the dashboard serves and what an operator can version and diff. The flags
are the one-off form, for a single horizon on the command line.

**Every number is declared, never invented** — equity, cash, each holding's
stop, the session held count, and the bounds of the raw setup score. Each would
decide something material while appearing to decide nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradeit.decide import (
    DeclarationError,
    DeclaredHolding,
    DeclaredPortfolio,
    PlanResult,
    load_declaration,
    plan_declared,
)
from tradeit.portfolio.mandate import Mandate

__all__ = ["add_decide_commands"]

DEFAULT_CORPUS = Path("research01.sqlite")


def _holding(spec: str) -> DeclaredHolding:
    """Parse ``TICKER:QUANTITY:ENTRY:STOP:SESSIONS_HELD``.

    All five parts are required. A holding without a stop has undefined risk
    and cannot be sized or summed into heat; one without a session count would
    look freshly opened and suppress the time stop that should close it.
    """
    parts = spec.split(":")
    if len(parts) != 5:
        raise SystemExit(
            f"--hold expects TICKER:QUANTITY:ENTRY:STOP:SESSIONS_HELD, got {spec!r}. "
            "All five are required: a holding with no stop has undefined risk, and one "
            "with no session count would suppress its own time stop."
        )
    ticker, quantity, entry, stop, held = parts
    try:
        return DeclaredHolding(
            ticker=ticker.strip().upper(),
            quantity=Decimal(quantity),
            entry_price=Decimal(entry),
            stop_price=Decimal(stop),
            sessions_held=int(held),
            opened_on=dt.date(1970, 1, 1),
        )
    except (ArithmeticError, ValueError) as exc:
        raise SystemExit(f"--hold {spec!r} has a part that is not a number: {exc}") from exc


def _from_flags(args: argparse.Namespace) -> DeclaredPortfolio:
    universe = tuple(dict.fromkeys(t.strip().upper() for t in args.tickers.split(",") if t.strip()))
    return DeclaredPortfolio(
        mandate=Mandate(args.mandate),
        equity=Decimal(str(args.equity)),
        cash=Decimal(str(args.cash)),
        universe=universe,
        warmup_from=dt.date.fromisoformat(args.start),
        as_of=dt.date.fromisoformat(args.as_of),
        holdings=tuple(_holding(spec) for spec in (args.hold or [])),
        score_lower=args.score_lower,
        score_upper=args.score_upper,
        fast=args.fast,
        slow=args.slow,
        stop_pct=Decimal(str(args.stop_pct)),
    )


def _report(result: PlanResult) -> None:
    if result.unresolved:
        # Reported, never dropped silently: a universe quietly smaller than the
        # one declared produces a plan about a portfolio nobody described.
        print(f"unresolved, and therefore not considered: {', '.join(result.unresolved)}")
    print(
        f"loaded   : {result.sessions_loaded} sessions, {result.bars_loaded} bars, "
        f"{result.sectors_classified} classified, in {result.seconds:,.1f}s"
    )
    for ticker, day in result.carried:
        # A stale mark is a fact the reader should see, not one to hide.
        print(f"carried  : {ticker} did not print; marked at its {day} close")
    print()
    print(result.plan.describe())


def cmd_plan(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus)
    if not corpus.exists():
        # Checked before connecting. SQLite creates an empty database on
        # connect, so a mistyped path would otherwise produce a plan that reads
        # exactly like a corpus in which nothing ever traded.
        print(f"no corpus at {corpus.expanduser().resolve()}")
        return 1

    try:
        if args.declaration:
            declared = load_declaration(Path(args.declaration))
        else:
            if not args.tickers or not args.start or not args.as_of:
                print("without --declaration, --tickers, --start and --as-of are all required")
                return 1
            declared = (_from_flags(args),)
    except DeclarationError as exc:
        print(f"declaration refused: {exc}")
        return 1

    session = sessionmaker(bind=create_engine(f"sqlite:///{corpus.expanduser().resolve()}"))()
    failures = 0
    for index, portfolio in enumerate(declared):
        if index:
            print()
            print("-" * 72)
        if portfolio.note:
            print(f"note     : {portfolio.note}")
        try:
            _report(plan_declared(session, portfolio))
        except DeclarationError as exc:
            # One horizon failing must not hide the others: a Day portfolio
            # with no intraday corpus is an expected absence, not a reason to
            # withhold the Swing plan.
            print(f"{portfolio.mandate}: {exc}")
            failures += 1
    return 1 if failures == len(declared) else 0


def add_decide_commands(sub: Any) -> None:
    plan = sub.add_parser(
        "plan", help="what one portfolio should do on one session, and what it refused"
    )
    plan.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="read only")
    plan.add_argument(
        "--declaration",
        default=None,
        help="JSON file, one entry per horizon; the form the dashboard serves",
    )
    plan.add_argument("--mandate", default="swing", choices=[str(m) for m in Mandate])
    plan.add_argument("--tickers", default=None, help="comma separated")
    plan.add_argument("--start", default=None, help="warmup start; needs --slow sessions")
    plan.add_argument("--as-of", default=None, help="the session to plan")
    plan.add_argument("--equity", default="100000")
    plan.add_argument("--cash", default="100000")
    plan.add_argument(
        "--hold",
        action="append",
        metavar="TICKER:QTY:ENTRY:STOP:HELD",
        help="a declared open position; repeatable. All five parts required.",
    )
    plan.add_argument("--fast", type=int, default=50)
    plan.add_argument("--slow", type=int, default=200)
    plan.add_argument("--stop-pct", default="0.08")
    plan.add_argument(
        "--score-lower", type=float, default=0.0, help="declared lower bound of the setup score"
    )
    plan.add_argument(
        "--score-upper",
        type=float,
        default=0.5,
        help="declared upper bound; a raw spread is not a score",
    )
    plan.set_defaults(func=cmd_plan)
