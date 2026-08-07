"""End-to-end: synthetic provider -> ingestion -> point-in-time reads.

Runs on SQLite so it stays part of the default suite. The PostgreSQL-specific
behaviour (EXCLUDE constraints, DISTINCT ON performance) is exercised
separately by tests marked ``integration``.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeit.core.clock import AsOfClock
from tradeit.core.enums import AdjustmentPolicy, TradingMode
from tradeit.data.providers.synthetic import SyntheticProvider
from tradeit.ingest.pipeline import Ingestor
from tradeit.storage import tables
from tradeit.storage.repositories import (
    BarRepository,
    EarningsRepository,
    FundamentalRepository,
    InstrumentRepository,
)

UTC = dt.UTC
START = dt.date(2022, 1, 3)
END = dt.date(2023, 12, 29)


@pytest.fixture
def loaded(db_session):
    """A database populated exactly as the daily pipeline would populate it."""
    provider = SyntheticProvider(seed=99, universe_size=3)
    ingestor = Ingestor(db_session, provider.name)

    for instrument, mapping in provider.list_instruments(END):
        db_session.add(
            tables.Instrument(
                instrument_id=instrument.instrument_id,
                primary_exchange=instrument.primary_exchange.value,
                asset_class=instrument.asset_class.value,
                name=instrument.name,
                first_trade_date=instrument.first_trade_date,
                source=provider.name,
            )
        )
        db_session.add(
            tables.SymbolMapping(
                instrument_id=mapping.instrument_id,
                ticker=mapping.ticker,
                valid_from=mapping.valid_from,
                source=provider.name,
            )
        )
        db_session.add(
            tables.UniverseMembership(
                universe="us_equity",
                instrument_id=instrument.instrument_id,
                valid_from=mapping.valid_from,
                source=provider.name,
            )
        )
    db_session.flush()

    for instrument, _ in provider.list_instruments(END):
        iid = instrument.instrument_id
        ingestor.ingest_bars(provider.fetch_bars(iid, START, END), start=START, end=END)
        ingestor.ingest_corporate_actions(provider.fetch_corporate_actions(iid, START, END))
        ingestor.ingest_fundamentals(provider.fetch_fundamentals(iid, ["revenue"], START, END))
        ingestor.ingest_earnings(provider.fetch_earnings(iid, START, END))
    db_session.flush()
    return db_session, provider


def _clock(day: str) -> AsOfClock:
    return AsOfClock.at(
        dt.datetime.combine(dt.date.fromisoformat(day), dt.time(23, 0), tzinfo=UTC),
        mode=TradingMode.BACKTEST,
    )


def test_the_pipeline_loads_every_dataset(loaded):
    session, _ = loaded
    assert session.query(tables.OhlcvBar).count() > 1000
    assert session.query(tables.FundamentalFact).count() > 0
    assert session.query(tables.EarningsEvent).count() > 0
    assert session.query(tables.QuarantinedRow).count() == 0

    runs = session.query(tables.IngestionRun).all()
    assert runs and all(r.status == "completed" for r in runs)


def test_universe_resolves_through_tickers(loaded):
    session, _ = loaded
    repo = InstrumentRepository(session)
    clock = _clock("2023-06-01")
    ids = repo.universe(clock, "us_equity")
    assert len(ids) == 3
    for iid in ids:
        ticker = repo.ticker_for(clock, iid)
        assert repo.resolve_ticker(clock, ticker) == iid


def test_a_walk_forward_never_sees_the_future(loaded):
    """The core guarantee, exercised the way a backtest actually reads.

    Stepping the clock forward one month at a time, the history returned must
    grow monotonically and must never contain a session dated after the clock.
    """
    session, _ = loaded
    repo = BarRepository(session)
    previous_length = 0

    for month in range(1, 13):
        clock = _clock(f"2023-{month:02d}-15")
        history = repo.history(clock, 1, adjustment=AdjustmentPolicy.NONE)

        assert all(b.session_date <= clock.date for b in history), (
            f"clock at {clock.date} returned a bar from the future"
        )
        assert all(b.knowledge_time <= clock.as_of for b in history)
        assert len(history) >= previous_length
        previous_length = len(history)

    assert previous_length > 200


def test_the_last_visible_bar_tracks_the_clock(loaded):
    session, _ = loaded
    repo = BarRepository(session)
    early = repo.latest(_clock("2022-06-15"), 1)
    late = repo.latest(_clock("2023-06-15"), 1)
    assert early.session_date < late.session_date <= dt.date(2023, 6, 15)


def test_fundamentals_are_invisible_until_filed(loaded):
    session, _ = loaded
    repo = FundamentalRepository(session)

    # Q4 2022 ends 2022-12-31 and the synthetic provider files it 40 days later.
    just_after_period_end = repo.latest_metric(_clock("2023-01-05"), 1, "revenue")
    assert just_after_period_end.period_end < dt.date(2022, 12, 31)

    after_filing = repo.latest_metric(_clock("2023-02-20"), 1, "revenue")
    assert after_filing.period_end == dt.date(2022, 12, 31)


def test_an_earnings_date_is_invisible_until_the_company_announces_it(loaded):
    """Knowing *when* a company will report is itself point-in-time information.

    The synthetic provider announces 18 days ahead. Q1 2023 is scheduled for
    2023-05-10 and announced 2023-04-22, so a screen run in mid-March correctly
    sees no upcoming print even though one exists -- exactly as a live screen
    would have. A system that returned it anyway would be dodging earnings risk
    with information it did not have.
    """
    repo = EarningsRepository(loaded[0])

    assert repo.next_scheduled(_clock("2023-03-15"), 1, horizon_days=90) is None

    after_announcement = _clock("2023-04-25")
    event = repo.next_scheduled(after_announcement, 1, horizon_days=90)
    assert event is not None
    assert event.scheduled_date == dt.date(2023, 5, 10)
    assert event.knowledge_time <= after_announcement.as_of


def test_adjusted_and_raw_series_agree_on_dollar_volume(loaded):
    session, _ = loaded
    repo = BarRepository(session)
    clock = _clock("2023-12-29")
    raw = repo.history(clock, 1, adjustment=AdjustmentPolicy.NONE)
    adjusted = repo.history(clock, 1, adjustment=AdjustmentPolicy.SPLIT_ONLY)

    assert len(raw) == len(adjusted)
    for r, a in zip(raw, adjusted, strict=True):
        assert abs(r.close * r.volume - a.close * a.volume) < r.close * r.volume / 1000
