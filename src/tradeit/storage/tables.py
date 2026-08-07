"""Physical schema.

Design notes that are not obvious from the column list:

* Fact tables are append-only. A vendor revision inserts a new row with a later
  ``knowledge_time``; nothing is ever updated in place. This costs storage and
  buys the ability to reproduce any historical decision exactly.
* Every fact table has an index leading with ``(instrument_id, ...,
  knowledge_time)`` because *every* read filters on knowledge_time. A query that
  does not is a bug, and the index layout makes the correct query the fast one.
* Prices are ``NUMERIC(18, 6)``, never float. See ``tradeit.core.money``.
* Uniqueness on facts includes ``knowledge_time`` so that revisions coexist,
  but excludes it on identity tables (instruments, symbol mappings) where
  intervals rather than versions express change over time.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    DateTime as SADateTime,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

UTC = dt.UTC


class UTCDateTime(TypeDecorator[dt.datetime]):
    """A timestamp column that is timezone-aware UTC on both sides of the wire.

    PostgreSQL's ``timestamptz`` already round-trips awareness, but SQLite --
    which the unit suite runs on -- returns naive datetimes, and a naive
    ``knowledge_time`` compared against an aware ``as_of`` is either a crash or,
    worse, a silently wrong comparison. Normalising in one place means the
    point-in-time guarantee does not depend on which database is underneath.
    """

    impl = SADateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("refusing to persist a naive datetime; UTC-aware values only")
        return value.astimezone(UTC)

    def process_result_value(
        self, value: dt.datetime | None, dialect: Dialect
    ) -> dt.datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


DateTime = UTCDateTime

#: Surrogate primary key. PostgreSQL gets BIGSERIAL; SQLite only autoincrements
#: a column declared exactly INTEGER PRIMARY KEY, so the unit-test backend gets
#: the narrower type. Nothing else in the codebase depends on the width.
PK = BigInteger().with_variant(Integer, "sqlite")

PRICE = Numeric(18, 6)
QTY = Numeric(24, 6)
RATIO = Numeric(18, 8)
VALUE = Numeric(28, 6)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """Row-level ingestion audit trail.

    Distinct from ``knowledge_time``: ``ingested_at`` is when *our* pipeline
    wrote the row, which is useful for debugging a bad load but must never be
    used for point-in-time filtering, because a backfill run today would then
    make 2019 data invisible to a 2019 clock.
    """

    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)


class Instrument(Base, TimestampMixin):
    __tablename__ = "instruments"

    instrument_id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    primary_exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    first_trade_date: Mapped[dt.date | None] = mapped_column(Date)
    listing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    delisted_date: Mapped[dt.date | None] = mapped_column(Date)
    figi: Mapped[str | None] = mapped_column(String(12), unique=True)
    cik: Mapped[str | None] = mapped_column(String(10), index=True)

    __table_args__ = (
        CheckConstraint(
            "(listing_status = 'active' AND delisted_date IS NULL) "
            "OR (listing_status <> 'active' AND delisted_date IS NOT NULL)",
            name="ck_instrument_lifecycle",
        ),
    )


class SymbolMapping(Base, TimestampMixin):
    """Ticker to instrument over a half-open date interval.

    The exclusion of overlapping intervals for the same ticker is enforced in
    the migration with a PostgreSQL ``EXCLUDE`` constraint; SQLAlchemy's
    portable layer cannot express it, and a unique index would wrongly forbid a
    ticker from ever being reassigned.
    """

    __tablename__ = "symbol_mappings"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)

    __table_args__ = (
        Index("ix_symbol_lookup", "ticker", "valid_from", "valid_to"),
        Index("ix_symbol_instrument", "instrument_id", "valid_from"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_symbol_interval"),
    )


class UniverseMembership(Base, TimestampMixin):
    """Survivorship-safe universe membership intervals."""

    __tablename__ = "universe_memberships"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    universe: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    exit_reason: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        Index("ix_universe_asof", "universe", "valid_from", "valid_to"),
        Index("ix_universe_instrument", "instrument_id", "universe"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_universe_interval"),
    )


class OhlcvBar(Base, TimestampMixin):
    """Unadjusted price bars. Append-only; revisions are new rows."""

    __tablename__ = "ohlcv_bars"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    event_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_source: Mapped[str] = mapped_column(String(16), nullable=False)

    open: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    volume: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    vwap: Mapped[Decimal | None] = mapped_column(PRICE)
    quality: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")

    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "timeframe",
            "session_date",
            "knowledge_time",
            name="uq_bar_revision",
        ),
        # The shape of every point-in-time bar read: one instrument, one
        # timeframe, a date window, bounded by knowledge_time.
        Index("ix_bar_pit", "instrument_id", "timeframe", "session_date", "knowledge_time"),
        CheckConstraint("high >= low", name="ck_bar_high_low"),
        CheckConstraint("high >= open AND high >= close", name="ck_bar_high"),
        CheckConstraint("low <= open AND low <= close", name="ck_bar_low"),
        CheckConstraint("volume >= 0", name="ck_bar_volume"),
        CheckConstraint("knowledge_time >= event_time", name="ck_bar_knowledge"),
    )


class CorporateAction(Base, TimestampMixin):
    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(24), nullable=False)
    ex_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    event_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_source: Mapped[str] = mapped_column(String(16), nullable=False)
    ratio: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=Decimal(1))
    cash_amount: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal(0))
    new_ticker: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "action_type", "ex_date", "knowledge_time", name="uq_action_revision"
        ),
        Index("ix_action_pit", "instrument_id", "ex_date", "knowledge_time"),
        CheckConstraint("ratio > 0", name="ck_action_ratio"),
        CheckConstraint("cash_amount >= 0", name="ck_action_cash"),
    )


class FundamentalFact(Base, TimestampMixin):
    """Narrow (metric-per-row) financial facts with restatement history."""

    __tablename__ = "fundamental_facts"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(4), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    event_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_source: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(VALUE)
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="USD")
    restatement_of: Mapped[int | None] = mapped_column(
        ForeignKey("fundamental_facts.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "metric",
            "fiscal_year",
            "fiscal_period",
            "knowledge_time",
            name="uq_fundamental_revision",
        ),
        Index("ix_fundamental_pit", "instrument_id", "metric", "knowledge_time", "period_end"),
        CheckConstraint("knowledge_time >= event_time", name="ck_fundamental_knowledge"),
    )


class EarningsEvent(Base, TimestampMixin):
    __tablename__ = "earnings_events"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    scheduled_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    session_hint: Mapped[str | None] = mapped_column(String(16))
    fiscal_period: Mapped[str] = mapped_column(String(4), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    event_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_source: Mapped[str] = mapped_column(String(16), nullable=False)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    eps_actual: Mapped[Decimal | None] = mapped_column(VALUE)
    eps_estimate: Mapped[Decimal | None] = mapped_column(VALUE)

    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "fiscal_year",
            "fiscal_period",
            "knowledge_time",
            name="uq_earnings_revision",
        ),
        Index("ix_earnings_pit", "instrument_id", "scheduled_date", "knowledge_time"),
    )


class IngestionRun(Base):
    """Audit record for every ingestion job.

    Reproducing a historical decision means knowing not only what the data said
    but which pipeline version wrote it and whether that run completed. A
    partially-failed load that nobody noticed is otherwise indistinguishable
    from a market holiday.
    """

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_from: Mapped[dt.date | None] = mapped_column(Date)
    requested_to: Mapped[dt.date | None] = mapped_column(Date)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    code_version: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_ingestion_recent", "dataset", "started_at"),)


class QuarantinedRow(Base):
    """Rows rejected by validation, kept verbatim for diagnosis.

    Dropping malformed vendor data on the floor makes gaps invisible. Keeping
    the raw payload alongside the reason turns "why is there no bar for
    2021-03-04?" into a one-query answer.
    """

    __tablename__ = "quarantined_rows"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    ingestion_run_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_quarantine_run", "ingestion_run_id"),)
