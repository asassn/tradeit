"""``tradeit dashboard`` -- serve the read side over one decision record.

**This is the wrapper the runbook already said was needed and that nobody had
written.** `RUNBOOK.md` documented starting the service with

``uvicorn --factory "tradeit.api:create_app"``

and then explained, correctly, that ``create_app`` takes the record's DSN and
has no default, *"so a wrapper is needed to pass one"*. The wrapper did not
exist, so the documented command raised
``TypeError: create_app() missing 1 required positional argument: 'dsn'`` and
there was no way to start Phase 10's service at all. That is the whole reason
`tradeit.api` was imported by its own test and by two documents and by nothing
else --- the shape the Phase 7 audit named, *"built, tested and standing
beside the decision rather than in it"*.

**``--record`` is required and has no default, and that is inherited rather
than chosen.** The reasoning is :func:`~tradeit.api.create_app`'s own: the
record and the corpus are different databases, and the several records on this
machine hold different runs. A default would serve whichever happened to be
configured while the page said nothing about which one --- the same confusion
`tradeit status` had to be fixed for, where every row read UNKNOWN because it
was pointed at the wrong database without saying so. Supplying it is one
argument; getting it silently wrong is unbounded.

**It binds to localhost and the default cannot be overridden to a wildcard
by accident.** ``--host`` exists, because a wrapper that forbade it would be
worked around with uvicorn directly, but the default is ``127.0.0.1`` and
anything else prints a warning naming what is being exposed. The service is
read-only and has no trading authority, but it serves a research record on a
machine with no authentication in front of it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["add_dashboard_commands"]

#: Bound here rather than at the call site so the warning below and the
#: argparse default cannot drift apart.
LOOPBACK = "127.0.0.1"


def _sqlite_url(path: Path) -> str:
    """A sqlite URL for a path, resolved first.

    ``sqlite:///`` with a relative path is relative to the working directory
    and ``sqlite:////`` with one is relative to the filesystem root, and the
    error when it is wrong never names the path that was tried. Resolving
    first removes the distinction: the path is absolute by the time it is
    formatted. Same helper and same reason as `cli_forward.py`.
    """
    return f"sqlite:///{path.expanduser().resolve()}"


def cmd_dashboard(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ModuleNotFoundError:
        print(
            "the web server is not installed. The research and backtesting path is\n"
            "deliberately runnable without a web framework, so it is an optional extra:\n"
            '    .venv/bin/pip install -e ".[api]"',
            file=sys.stderr,
        )
        return 2

    from tradeit.api import create_app

    record = Path(args.record)
    # Checked before the server starts rather than left to the first request.
    # SQLite creates an empty database on connect, so a mistyped path would
    # otherwise produce a service that starts cleanly, answers every route
    # with nothing, and reads exactly like a record in which nothing happened.
    if args.record.startswith(("sqlite:", "postgresql:", "postgres:")):
        dsn = args.record
    elif record.exists():
        dsn = _sqlite_url(record)
    else:
        print(
            f"no record at {record.expanduser().resolve()}\n"
            "Nothing is created: an empty sqlite file would start a service that\n"
            "answers every route with nothing, which reads as a quiet record rather\n"
            "than a missing one.",
            file=sys.stderr,
        )
        return 2

    if args.host != LOOPBACK:
        print(
            f"warning: binding to {args.host} rather than {LOOPBACK}. The service is\n"
            "read-only and cannot trade, but it serves the decision record to anything\n"
            "that can reach this machine, and nothing authenticates it.",
            file=sys.stderr,
        )

    corpus_dsn: str | None = None
    if args.corpus is not None:
        corpus = Path(args.corpus)
        if args.corpus.startswith(("sqlite:", "postgresql:", "postgres:")):
            corpus_dsn = args.corpus
        elif corpus.exists():
            corpus_dsn = _sqlite_url(corpus)
        else:
            print(f"no corpus at {corpus.expanduser().resolve()}", file=sys.stderr)
            return 2

    forward_dsn: str | None = None
    if args.forward is not None:
        forward = Path(args.forward)
        if args.forward.startswith(("sqlite:", "postgresql:", "postgres:")):
            forward_dsn = args.forward
        elif forward.exists():
            forward_dsn = _sqlite_url(forward)
        else:
            print(f"no forward record at {forward.expanduser().resolve()}", file=sys.stderr)
            return 2

    declaration: Path | None = None
    if args.declaration is not None:
        declaration = Path(args.declaration).expanduser().resolve()
        # Read before the server starts, not left to the first request. A
        # declaration that refuses only when the home page is opened reads as a
        # broken dashboard rather than as a file with a missing stop in it.
        #
        # There is no separate `exists()` check: `load_declaration` already
        # raises "no declaration at <path>" for a missing file, and a mutation
        # test proved no assertion could tell the two apart. A guard whose
        # removal nothing detects is redundant, not defensive — the same
        # finding as the duplicated `require_timeframe` in `decide.plan`.
        from tradeit.decide import DeclarationError, load_declaration

        try:
            declared = load_declaration(declaration)
        except DeclarationError as exc:
            print(f"declaration refused: {exc}", file=sys.stderr)
            return 2
        horizons = ", ".join(str(portfolio.mandate) for portfolio in declared)

    app = create_app(dsn, corpus_dsn, forward_dsn, declaration)
    print(f"reading  : {dsn}")
    print(f"corpus   : {corpus_dsn or 'none -- valuation is disabled'}")
    print(f"forward  : {forward_dsn or 'none -- capture health is disabled'}")
    stated = f"{declaration} ({horizons})" if declaration else "none -- the plan is disabled"
    print(f"portfolio: {stated}")
    print(f"serving  : http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def add_dashboard_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "dashboard",
        help="serve the read-only research interface over one decision record",
    )
    parser.add_argument(
        "--record",
        required=True,
        help=(
            "the decision record to read: a path to a sqlite file, or a full DSN. "
            "Required and with no default -- the record and the corpus are different "
            "databases and there is more than one record"
        ),
    )
    parser.add_argument(
        "--corpus",
        default=None,
        help=(
            "optional: research-01, for the valuation panel. A different database "
            "from the record -- the record holds what the system decided, the "
            "corpus holds what the world did. Without it the valuation route "
            "says so rather than answering empty"
        ),
    )
    parser.add_argument(
        "--forward",
        default=None,
        help=(
            "optional: the nightly capture record, for the system health view. "
            "A third database again -- the only one in this system whose "
            "contents change, and the one a stopped agent shows up in"
        ),
    )
    parser.add_argument(
        "--declaration",
        default=None,
        help=(
            "optional: the declared portfolios, one entry per horizon, for the plan. "
            "Not a database -- nothing in this system writes a portfolios or positions "
            "row, so the operator states what each horizon holds and this file is the "
            "statement. See docs/examples/portfolios.example.json. Without it the plan "
            "route says so rather than serving an empty portfolio"
        ),
    )
    parser.add_argument("--host", default=LOOPBACK, help="interface to bind (default: loopback)")
    parser.add_argument("--port", type=int, default=8000, help="port to bind (default: 8000)")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="uvicorn log level",
    )
    parser.set_defaults(func=cmd_dashboard)
