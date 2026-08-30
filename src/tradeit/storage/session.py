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
