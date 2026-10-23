"""One command that reports the whole system's state.

Phase 12 asks for monitoring and operational runbooks. Before either is worth
writing, there has to be a single place that answers *what is this system's
state right now* — and until this command existed the answer was spread across
seven scripts, and a reader had to already know which to run to find out
whether anything was wrong.

What this is, and what it refuses to be
---------------------------------------

**It reports. It changes nothing and runs no backtest.** Every figure is read
from the corpus, the configuration or the filesystem. A status command that
computed results would be slow enough that nobody ran it, and a status nobody
runs is worse than none because it exists as an excuse.

**It distinguishes "no" from "not measured", everywhere.** A row reading
``UNKNOWN`` is a different operational fact from one reading ``NO``, and a
diagnostic that printed the second when it meant the first would send somebody
to fix a thing that was never broken. This is the same discipline the readiness
framework applies to criteria, for the same reason.

**It says when a control is absent rather than implying protection.** The
roadmap puts kill switch, stale-data and duplicate-order protection above the
strategy; whether they are *installed in a given run* is a property of that
run's wiring, not of the repository, so this reports what the repository can
offer and says plainly that installation is per-run.

**It is honest about live trading.** The interlock state is reported because it
is the single most consequential fact about the system, and reported as what it
is: an interlock that is engaged, not a feature that is missing.
"""

from __future__ import annotations

import argparse
import datetime as dt

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.config import LIVE_AUTHORIZATION_PATH, get_settings
from tradeit.readiness import criteria_for
from tradeit.readiness.criteria import PROMOTION_CRITERIA, ThresholdSource
from tradeit.strategy.lifecycle import LADDER

__all__ = ["add_status_commands", "cmd_status"]

UNKNOWN = "UNKNOWN"

#: The instant `forward_observations.rows_returned` started counting the
#: **session** rather than the whole fetch window.
#:
#: Before this, `rows = len(outcome.rows)` was the row count for the entire
#: `start..end` window, stored against `session_date = end` -- so a security
#: that stopped trading still returned the previous six sessions and was
#: recorded as having printed. Measured on the live record 2026-10-23 and fixed
#: the same day; the read rule and the record's five asks are tabulated in
#: `ops/UNIVERSE.md`.
#:
#: **A literal date, deliberately.** It is a historical instant and cannot rot,
#: which is what distinguishes it from the counts this repository keeps out of
#: prose. The alternative -- a semantics version stamped on each row -- needs a
#: migration to label 152 legacy rows and would carry the same constant anyway,
#: in the migration instead of here.
#:
#: Keyed on `asked_at`, never on `session_date`: one session holds rows of both
#: kinds, because the verification run re-asked about it correctly.
SESSION_COUNTS_FROM = "2026-10-23 17:50"


def _redact(dsn: str) -> str:
    """A DSN safe to print.

    Credentials in a connection string are a secret, and a status command is
    exactly the output somebody pastes into a chat window to ask for help.
    """
    if "@" not in dsn or "//" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("//")
    _, _, host = rest.rpartition("@")
    return f"{scheme}//***@{host}"


def _scalar(session: Session, sql: str) -> int | str | None:
    """A count, or a short reason it could not be read.

    Never 0 for a missing table: a missing table and an empty one are different
    facts, and reporting the first as the second would have a reader looking
    for lost rows instead of a missing migration. The reason is returned rather
    than swallowed, because "no such table" and "connection refused" send
    somebody to different places.

    **Rolls back on failure, so the session must be one the caller owns.**
    ``cmd_status`` opens its own for exactly this reason: rolling back a
    session somebody else was using would discard their uncommitted work to
    print a diagnostic.
    """
    try:
        return int(session.execute(text(sql)).scalar() or 0)
    except Exception as problem:
        # **Roll back, or one failure misreports every later probe.** On
        # PostgreSQL a failed statement aborts the transaction, so the next
        # query returns "current transaction is aborted" and this function
        # classified it as `unreadable` -- the first row said "no such table"
        # and five identical situations below it said something else. Each
        # probe is independent and must report its own reason.
        session.rollback()
        message = str(problem).lower()
        if "no such table" in message or "does not exist" in message:
            return "no such table"
        if "could not connect" in message or "connection refused" in message:
            return "unreachable"
        return "unreadable"


def _scalar_raw(session: Session, sql: str) -> object | None:
    """A single value of any type, or None when it cannot be read."""
    try:
        return session.execute(text(sql)).scalar()
    except Exception:
        session.rollback()
        return None


def _age_in_days(stamp: str, as_of: str) -> int | None:
    """Days between a stored date and a caller-supplied one.

    Both parsed rather than assumed: SQLite has no date type, so a session_date
    arrives as a string, and this project has already written zero rows once by
    passing a `datetime.date` to raw SQL.
    """
    try:
        return (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(stamp[:10])).days
    except ValueError:
        return None


def _row(label: str, value: object, note: str = "") -> str:
    shown = UNKNOWN if value is None else value
    tail = f"   {note}" if note else ""
    return f"  {label:<34} {shown}{tail}"


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    print("=== configuration ===")
    print(_row("environment", settings.environment))
    print(_row("trading mode", settings.trading_mode))
    print(_row("orders simulated", settings.orders_are_simulated))

    print("\n=== the live interlock (ADR-0004) ===")
    authorised = LIVE_AUTHORIZATION_PATH.exists()
    print(
        _row(
            "live trading authorised",
            "YES" if authorised else "NO",
            f"({LIVE_AUTHORIZATION_PATH})",
        )
    )
    if not authorised:
        print("    Engaged. This is the interlock working, not a missing feature.")

    # `str()` because the configured DSN is a pydantic PostgresDsn, not a str.
    dsn = str(args.db or settings.database.dsn)
    print("\n=== the corpus ===")
    # Named every time, and this is not decoration. The configured DSN and the
    # research-01 corpus are different databases, so a status command that read
    # one silently while a reader assumed the other would report zero price
    # facts and send somebody hunting a data loss that never happened.
    print(_row("database read", _redact(dsn)))
    try:
        engine = create_engine(dsn, future=True)
        # **Connect explicitly.** `create_engine` is lazy and never fails, so
        # the first version's `return 1` path was unreachable: an entirely
        # unreachable database fell through to the per-probe handler, printed
        # six "unreachable" rows and **exited 0**. A status command reporting
        # success on a database it could not reach is the one failure mode a
        # status command must not have.
        with engine.connect():
            pass
        session = Session(engine)
    except Exception as problem:
        print(f"  unreachable: {problem}")
        print(
            "    Pass --db to point at another database. The corpus the research\n"
            "    scripts use is not the configured DSN."
        )
        return 1
    with session:
        # **Only cheap queries by default, and that is the whole design.** The
        # first version ran `count(*)` and `max(session_date)` over
        # security_price_facts and had to be killed: measured on the 73GB
        # research-01 corpus, `max(rowid)` returns in 0.01s and
        # `max(session_date)` takes 134 seconds, because session_date is not the
        # leading column of an index. A status command that takes two minutes
        # does not get run, and one nobody runs is worse than none -- which this
        # module's own docstring claimed before the code obeyed it.
        print(
            _row(
                "price facts (upper bound)",
                _scalar(session, "select max(rowid) from security_price_facts"),
                "max(rowid); equals the count unless rows were deleted",
            )
        )
        print(
            _row(
                "sector classifications",
                _scalar(session, "select count(*) from sectors where scheme = 'SIC'"),
            )
        )
        print(_row("corporate actions", _scalar(session, "select count(*) from corporate_actions")))
        print(_row("journal entries", _scalar(session, "select count(*) from journal_entries")))
        print(_row("risk snapshots", _scalar(session, "select count(*) from risk_snapshots")))
        print(_row("backtest runs", _scalar(session, "select count(*) from backtest_runs")))

        if not args.deep:
            print(
                "    Exact counts and the most recent priced session need full scans\n"
                "    (134s for one of them here). Pass --deep if you want them."
            )
        else:
            print("    --deep: scanning. This is slow by nature, not by accident.")
            print(
                _row(
                    "price facts (exact)",
                    _scalar(session, "select count(*) from security_price_facts"),
                )
            )
            print(
                _row(
                    "securities with prices",
                    _scalar(
                        session,
                        "select count(distinct security_id) from security_price_facts",
                    ),
                )
            )
            latest = _scalar_raw(session, "select max(session_date) from security_price_facts")
            print(_row("most recent priced session", latest))
            if latest is not None and args.as_of:
                age = _age_in_days(str(latest), args.as_of)
                print(
                    _row(
                        "corpus age at --as-of",
                        UNKNOWN if age is None else f"{age} days",
                        "stale-data guards judge against a caller's as_of, never the clock",
                    )
                )

    print("\n=== the decision record, as a population ===")
    # Only meaningful against a decision record, not the corpus. Reported as
    # "not this database" rather than UNKNOWN when the table is absent, because
    # the corpus legitimately has no opportunity_scores and a reader pointed at
    # it should not think something is missing.
    with session:
        scored = _scalar(session, "select count(*) from opportunity_scores")
    if not isinstance(scored, int):
        print(_row("recorded scores", "not this database", "no opportunity_scores table"))
    elif scored == 0:
        print(_row("recorded scores", 0, "nothing recorded yet"))
    else:
        from tradeit.core.enums import Bartimeframe
        from tradeit.observability.population import check_recorded_scores
        from tradeit.portfolio.mandate import Mandate

        with sessionmaker(bind=engine, future=True)() as fresh:
            result = check_recorded_scores(fresh, mandate=Mandate.SWING, timeframe=Bartimeframe.D1)
        print(_row("recorded scores", f"{result.total_rows:,}"))
        print(
            _row(
                "certified Swing/1d",
                result.certified,
                "the horizon DECLARED here, not inferred from the rows",
            )
        )
        if result.unattributable:
            print(
                _row(
                    "no recorded horizon",
                    result.unattributable,
                    "blocks certification; not skipped",
                )
            )

    print("\n=== the forward record ===")
    # **Freshness, not just presence.** A daily capture that silently stops
    # leaves a record that simply ceases to grow, and nothing else says so --
    # which is the failure mode a scheduled job actually has. The age of the
    # most recent ask is the one number that catches it.
    with sessionmaker(bind=engine, future=True)() as fresh:
        latest = _scalar_raw(fresh, "select max(asked_at) from forward_observations")
        observed = _scalar(fresh, "select count(*) from forward_observations")
        interpretable = _scalar(
            fresh,
            f"select count(*) from forward_observations where asked_at >= '{SESSION_COUNTS_FROM}'",
        )
        silent = _scalar(
            fresh,
            "select count(*) from forward_observations "
            f"where rows_returned = 0 and asked_at >= '{SESSION_COUNTS_FROM}'",
        )
    if not isinstance(observed, int):
        print(_row("forward observations", "not this database", "no forward_observations table"))
    elif observed == 0:
        print(_row("forward observations", 0, "the capture has not run against this record"))
    else:
        print(_row("forward observations", f"{observed:,}", "every row ever written"))
        # **The interpretable subset, reported separately.** Before
        # `SESSION_COUNTS_FROM`, `rows_returned` counted the whole fetch window
        # rather than the session, so a zero meant "nothing in seven days" and
        # said nothing about the session the row is dated to. This command used
        # to print the total beside "of which silent: what a delisting is made
        # of", which handed a reader delisting evidence the record did not hold
        # -- and two of the four zeros were tickers dead since 2018 and 2022.
        legacy = observed - interpretable if isinstance(interpretable, int) else None
        print(
            _row(
                "of which interpretable",
                f"{interpretable:,}" if isinstance(interpretable, int) else UNKNOWN,
                f"asked at or after {SESSION_COUNTS_FROM}; rows_returned counts the session",
            )
        )
        if legacy:
            print(
                _row(
                    "earlier rows",
                    f"{legacy:,}",
                    "rows_returned counts the fetch window, not the session; "
                    "see the read rule in ops/UNIVERSE.md",
                )
            )
        print(
            _row(
                "silent, interpretable only",
                f"{silent:,}" if isinstance(silent, int) else UNKNOWN,
                "asked about a session others printed in, and got nothing: "
                "what a delisting is made of",
            )
        )
        print(_row("most recent ask", latest))
        age = _age_in_days(str(latest), args.as_of) if (latest and args.as_of) else None
        if age is not None:
            print(
                _row(
                    "age at --as-of",
                    f"{age} days",
                    "a daily job that stopped shows up here and nowhere else",
                )
            )

    print("\n=== controls the repository can install ===")
    print(
        _row("kill switch", "available", "risk/controls.py; installation is per-run"),
    )
    print(_row("stale-data guard", "available", "max_age has no default, by design"))
    print(_row("duplicate-order guard", "available"))
    print(_row("execution reconciliation", "available", "execution/service.py, halts on drift"))
    print(
        "    Whether a given run installs these is a property of that run's wiring.\n"
        "    PreTradeControls.configured reports it, so a run with none is visible\n"
        "    as such rather than letting a reader assume protection."
    )

    print("\n=== readiness framework ===")
    print(_row("promotion criteria declared", len(PROMOTION_CRITERIA)))
    for state in LADDER:
        count = len(criteria_for(state))
        if count:
            print(_row(f"  asked when entering {state.value}", count))
    owed = [
        criterion
        for criterion in PROMOTION_CRITERIA
        if criterion.threshold.source is ThresholdSource.UNAPPROVED
    ]
    print(_row("thresholds awaiting approval", len(owed)))
    for criterion in owed:
        print(f"    {criterion.name}: blocks promotion until a value is approved")
    if owed:
        print(
            "    These block rather than defaulting. A threshold nobody chose would\n"
            "    become policy by accident, which is what the roadmap forbids."
        )

    print("\n=== what this command does not tell you ===")
    print(
        "  Whether any strategy is ready. That needs a run's measurements, and this\n"
        "  command deliberately computes nothing -- a status that took minutes would\n"
        "  not get run, and one nobody runs is worse than none. Use\n"
        "  scripts/readiness_report.py for a verdict against recorded evidence."
    )
    return 0


def add_status_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser("status", help="report the whole system's state, computing nothing")
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "database to read; defaults to the configured DSN. The research-01 "
            "corpus is a separate database, so the default is often not the one "
            "a reader means."
        ),
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help=(
            "also run the queries that need full table scans. Off by default "
            "because one of them takes over two minutes on the research-01 corpus."
        ),
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help=(
            "ISO date to measure corpus staleness against. Omitted by default: a "
            "diagnostic that silently used today would report a different answer "
            "tomorrow for the same corpus."
        ),
    )
    parser.set_defaults(func=cmd_status)
