"""``tradeit corpus ...`` -- maintain the corpus's derived conveniences.

Currently one command: rebuilding the summary that the views listing
securities read. It is here rather than in `cli_data` because it operates on
a prepared corpus rather than on an import, and because it **writes** -- which
is worth having its own command name rather than hiding inside a flag.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradeit.research01.readmodel import build_summary, summary_state

__all__ = ["add_corpus_commands"]

DEFAULT_CORPUS = Path("research01.sqlite")


def _url(path: Path) -> str:
    """Resolve before formatting: `sqlite:///` with a relative path is relative
    to the working directory and `sqlite:////` is relative to the filesystem
    root, and the error when it is wrong never names the path it tried."""
    return f"sqlite:///{path.expanduser().resolve()}"


def cmd_build_summary(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus)
    if not corpus.exists():
        print(f"no corpus at {corpus.expanduser().resolve()}", file=sys.stderr)
        return 2
    factory = sessionmaker(bind=create_engine(_url(corpus), future=True), future=True)
    with factory() as session:
        # **Flushed.** `print` buffers when stdout is not a terminal, so a
        # rebuild that takes minutes showed nothing at all until it finished
        # -- progress nobody sees is not progress, and the operator cannot
        # tell a slow step from a hung one.
        def say(line: str) -> None:
            print(line, flush=True)

        say(f"rebuilding the summary in {corpus.name} -- this takes minutes")
        report = build_summary(session, progress=say)
        say("\n" + report.describe())
    return 0


def cmd_summary_state(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus)
    if not corpus.exists():
        print(f"no corpus at {corpus.expanduser().resolve()}", file=sys.stderr)
        return 2
    factory = sessionmaker(bind=create_engine(_url(corpus), future=True), future=True)
    with factory() as session:
        for key, value in summary_state(session).items():
            print(f"{key:<12}: {value}")
    return 0


def add_corpus_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    corpus = sub.add_parser("corpus", help="maintain the research corpus")
    inner = corpus.add_subparsers(dest="corpus_command", required=True)

    build = inner.add_parser(
        "build-summary",
        help="rebuild the per-security summary the listing views read",
    )
    build.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="the corpus to read")
    build.set_defaults(func=cmd_build_summary)

    state = inner.add_parser("summary-state", help="whether the summary exists, and how old it is")
    state.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="the corpus to read")
    state.set_defaults(func=cmd_summary_state)
