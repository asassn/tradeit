from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.core.clock import AsOfClock
from tradeit.core.enums import TradingMode
from tradeit.storage.tables import Base

UTC = dt.UTC


@pytest.fixture
def db_session() -> Session:
    """In-memory SQLite session.

    Unit tests run against SQLite so the suite needs no container. The queries
    under test use window functions and ON CONFLICT, both of which behave the
    same here as on PostgreSQL; anything genuinely dialect-specific belongs in
    tests/integration and is marked accordingly.
    """
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = maker()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def clock_factory():
    def make(date_str: str, hour: int = 21, mode: TradingMode = TradingMode.BACKTEST) -> AsOfClock:
        moment = dt.datetime.combine(dt.date.fromisoformat(date_str), dt.time(hour, 0), tzinfo=UTC)
        return AsOfClock.at(moment, mode=mode)

    return make
