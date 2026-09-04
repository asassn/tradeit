"""Engine and session management."""

from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tradeit.config import get_settings


@functools.lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(
        str(settings.database.dsn),
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_pre_ping=True,
        echo=settings.database.echo_sql,
        future=True,
    )
    _install_statement_timeout(engine, settings.database.statement_timeout_s)
    return engine


def _install_statement_timeout(engine: Engine, seconds: int) -> None:
    """Cap query runtime so one pathological scan cannot wedge a daily job."""
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine, "connect")
    def _set_timeout(dbapi_conn: object, _record: object) -> None:
        with dbapi_conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(f"SET statement_timeout = {seconds * 1000}")


#: How long a SQLite writer waits for another connection's lock before giving up.
#:
#: Python's ``sqlite3`` defaults to **five seconds** -- measured, not assumed;
#: an earlier version of this comment said zero and was wrong. Five seconds is
#: ample against another writer and useless against a long reader: a ``pragma
#: integrity_check`` over 45 GB holds its lock for half an hour and killed a
#: multi-hour paid backfill on its second symbol. Minutes, not seconds.
SQLITE_BUSY_TIMEOUT_MS = 300_000


def install_sqlite_busy_timeout(engine: Engine, timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS) -> Engine:
    """Make SQLite writers wait for a lock rather than fail on it.

    Defined here rather than repeated in each script: several long-running jobs
    now write to the same file, and a pragma copied into six places is six
    places for one of them to be forgotten. A no-op on any other dialect.

    This raises the driver's five-second default, it does not introduce a wait
    where there was none.
    """
    if engine.dialect.name != "sqlite":
        return engine

    @event.listens_for(engine, "connect")
    def _set_busy_timeout(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute(f"PRAGMA busy_timeout = {timeout_ms}")
        cursor.close()

    return engine


@functools.lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
