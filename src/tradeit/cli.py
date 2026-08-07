"""Command line entry point.

Phase 1 exposes only what exists: configuration inspection, schema creation,
and a demo ingest against the synthetic provider. Screening, backtesting and
trading subcommands arrive with the phases that build them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradeit.config import get_settings
from tradeit.core.calendar import get_calendar
from tradeit.core.clock import AsOfClock
from tradeit.core.enums import TradingMode
from tradeit.core.models import Instrument, SymbolMapping
from tradeit.data.registry import available, get_provider
from tradeit.ingest.pipeline import Ingestor
from tradeit.logging import configure_logging
from tradeit.storage import tables
from tradeit.storage.repositories import BarRepository
from tradeit.storage.session import get_engine, session_scope
from tradeit.storage.tables import Base

log = structlog.get_logger(__name__)

#: Universe name the demo loader populates, so the reference data it writes is
#: reachable through InstrumentRepository.universe rather than only by id.
DEMO_UNIVERSE = "us_equity"


def cmd_config(_: argparse.Namespace) -> int:
    settings = get_settings()
    calendar = get_calendar(settings.data.exchange_calendar)
    print(f"environment          : {settings.environment}")
    print(f"trading mode         : {settings.trading_mode}")
    print(f"orders simulated     : {settings.orders_are_simulated}")
    print(f"database             : {settings.database.dsn}")
    print(f"price provider       : {settings.data.price_provider}")
    print(f"providers registered : {', '.join(available())}")
    print(f"calendar             : {calendar.name}")
    if calendar.uses_fallback:
        print("  WARNING: using the approximate built-in holiday list; session")
        print("  counts around one-off closures will be wrong.")
    return 0


def cmd_init_db(_: argparse.Namespace) -> int:
    """Create tables directly. Development convenience; production uses Alembic."""
    Base.metadata.create_all(get_engine())
    print("schema created")
    return 0


def cmd_demo_ingest(args: argparse.Namespace) -> int:
    """Load synthetic data end-to-end and read it back through the as-of clock."""
    settings = get_settings()
    provider = get_provider(settings.data.price_provider)
    end = dt.date.fromisoformat(args.end) if args.end else dt.datetime.now(tz=dt.UTC).date()
    start = end - dt.timedelta(days=args.days)

    with session_scope() as session:
        ingestor = Ingestor(session, provider.name)
        instruments = list(provider.list_instruments(end))
        _seed_reference_data(session, instruments, provider.name)

        total = 0
        for instrument, _ in instruments:
            bars = provider.fetch_bars(instrument.instrument_id, start, end)
            result = ingestor.ingest_bars(bars, start=start, end=end)
            total += result.rows_written
            ingestor.ingest_corporate_actions(
                provider.fetch_corporate_actions(instrument.instrument_id, start, end)
            )
        print(f"ingested {total} bars for {len(instruments)} instruments")

        clock = AsOfClock.at(
            dt.datetime.combine(end, dt.time(21, 0), tzinfo=dt.UTC),
            mode=TradingMode.BACKTEST,
        )
        repo = BarRepository(session)
        history = repo.history(clock, instruments[0][0].instrument_id, limit=5)
        print(f"\nlast {len(history)} split-adjusted bars for instrument 1 as of {clock.as_of}:")
        for bar in history:
            print(f"  {bar.session_date}  close={bar.close:>12}  volume={bar.volume:>14}")
    return 0


def _seed_reference_data(
    session: Session,
    instruments: list[tuple[Instrument, SymbolMapping]],
    source: str,
) -> None:
    """Insert instruments, tickers and universe membership if not already present.

    Reference data is inserted by natural key rather than merged by surrogate
    key: a symbol mapping is identified by (ticker, valid_from), and re-running
    the loader must not create a second interval claiming the same ticker at the
    same time -- PostgreSQL's exclusion constraint would reject it, correctly.
    """
    for instrument, mapping in instruments:
        if session.get(tables.Instrument, instrument.instrument_id) is None:
            session.add(
                tables.Instrument(
                    instrument_id=instrument.instrument_id,
                    primary_exchange=instrument.primary_exchange.value,
                    asset_class=instrument.asset_class.value,
                    name=instrument.name,
                    first_trade_date=instrument.first_trade_date,
                    listing_status=instrument.listing_status.value,
                    source=source,
                )
            )

        existing_symbol = session.execute(
            select(tables.SymbolMapping.id).where(
                tables.SymbolMapping.ticker == mapping.ticker,
                tables.SymbolMapping.valid_from == mapping.valid_from,
            )
        ).first()
        if existing_symbol is None:
            session.add(
                tables.SymbolMapping(
                    instrument_id=mapping.instrument_id,
                    ticker=mapping.ticker,
                    valid_from=mapping.valid_from,
                    source=source,
                )
            )

        existing_membership = session.execute(
            select(tables.UniverseMembership.id).where(
                tables.UniverseMembership.universe == DEMO_UNIVERSE,
                tables.UniverseMembership.instrument_id == instrument.instrument_id,
                tables.UniverseMembership.valid_from == mapping.valid_from,
            )
        ).first()
        if existing_membership is None:
            session.add(
                tables.UniverseMembership(
                    universe=DEMO_UNIVERSE,
                    instrument_id=instrument.instrument_id,
                    valid_from=mapping.valid_from,
                    source=source,
                )
            )
    session.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradeit", description=__doc__)
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("config", help="show effective configuration").set_defaults(func=cmd_config)
    sub.add_parser("init-db", help="create tables (dev only)").set_defaults(func=cmd_init_db)

    demo = sub.add_parser("demo-ingest", help="load synthetic data and read it back")
    demo.add_argument("--days", type=int, default=400)
    demo.add_argument("--end", default=None, help="ISO end date (default: today)")
    demo.set_defaults(func=cmd_demo_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
