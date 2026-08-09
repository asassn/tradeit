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
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import (
    DateTime as SADateTime,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

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

#: Structured payloads (score components, config snapshots, journal context).
#: JSONB on PostgreSQL for indexable containment queries; plain JSON on SQLite
#: so the unit suite still runs. Used only where the shape is genuinely open --
#: anything queried by a fixed name gets a real column.
JSONB_OR_JSON = JSONB().with_variant(JSON(), "sqlite")

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

    ``stage``, ``source_file`` and ``line_number`` were added for the offline
    package importer and are what make the answer *actionable*. Knowing a row
    died is not the same as knowing where to look: a row that failed at
    ``normalized`` means the vendor's date format needs declaring, one that
    failed at ``validated`` means the vendor's numbers contradict each other,
    and one that failed at ``point_in_time`` means nothing in the package says
    when the fact became knowable. Three different fixes, three different
    people, one column to tell them apart.
    """

    __tablename__ = "quarantined_rows"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    ingestion_run_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    package_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_packages.id", ondelete="CASCADE")
    )
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(64))
    #: Which pipeline stage rejected it: raw / normalized / validated /
    #: point_in_time / derived.
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="validated")
    source_file: Mapped[str | None] = mapped_column(String(512))
    #: 1-based and counting the header, so it matches what a text editor shows.
    line_number: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_quarantine_run", "ingestion_run_id"),
        Index("ix_quarantine_package", "package_id", "dataset"),
        Index("ix_quarantine_stage", "stage"),
    )


# ===========================================================================
# Phase 2 schema.
#
# Everything below stores *derived* data and *decisions*, as opposed to the
# Phase 1 tables above, which store observed facts. The distinction drives two
# design differences:
#
#   * Derived data is reproducible from facts plus a configuration, so it can be
#     recomputed and does not need revision history. It does need to record
#     WHICH configuration produced it, which is what the ``*_digest`` columns
#     are for.
#   * Decisions are not reproducible after the fact -- they happened -- so they
#     are immutable once written and carry a full manifest reference.
#
# Every table that feeds a trading decision carries ``as_of`` (when the decision
# context was evaluated) and ``session_date`` (the trading day it concerns).
# Those are not the same thing and conflating them is how a scan run on Tuesday
# evening gets attributed to Wednesday.
# ===========================================================================


class Sector(Base, TimestampMixin):
    """Sector/industry classification over a date interval.

    Interval-based for the same reason tickers are: classifications change.
    A company reclassified from Technology to Communication Services in 2018
    was in Technology in 2017, and a sector-rotation backtest that uses today's
    mapping for all history is measuring a different strategy.
    """

    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    scheme: Mapped[str] = mapped_column(String(32), nullable=False)
    sector: Mapped[str] = mapped_column(String(64), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(96))
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)

    __table_args__ = (
        Index("ix_sector_asof", "instrument_id", "scheme", "valid_from", "valid_to"),
        Index("ix_sector_lookup", "scheme", "sector", "valid_from"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_sector_interval"),
    )


class NewsItem(Base, TimestampMixin):
    """Headlines, bitemporal like every other fact."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE")
    )
    event_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_source: Mapped[str] = mapped_column(String(16), nullable=False)
    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024))
    publisher: Mapped[str | None] = mapped_column(String(128))
    sentiment: Mapped[float | None] = mapped_column(Float)
    sentiment_model_version: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        UniqueConstraint("external_id", "knowledge_time", name="uq_news_external"),
        Index("ix_news_pit", "instrument_id", "knowledge_time"),
        CheckConstraint(
            "sentiment IS NULL OR sentiment_model_version IS NOT NULL",
            name="ck_news_sentiment_provenance",
        ),
    )


class MacroObservation(Base, TimestampMixin):
    """Economic series values, keyed by vintage so revisions coexist."""

    __tablename__ = "macro_observations"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String(64), nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    event_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_source: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(VALUE)
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="index")
    vintage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("series_id", "period_end", "vintage", name="uq_macro_vintage"),
        Index("ix_macro_pit", "series_id", "knowledge_time"),
    )


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


class ArtifactVersion(Base):
    """Content-addressed registry of everything that must be pinned to replay.

    The digest is the primary key rather than a surrogate id, because the whole
    point is that identity IS content. Inserting the same configuration twice
    is a no-op, and two rows can never disagree about what a digest means.
    """

    __tablename__ = "artifact_versions"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB_OR_JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_artifact_kind_name", "kind", "name", "created_at"),)


class RunManifest(Base):
    """One row per decision-producing run; the anchor for reproducibility.

    Every scan, backtest and paper session writes one, and every derived row
    references it. Given a manifest id you can reconstruct the exact inputs;
    without one, a stored signal is an assertion nobody can check.
    """

    __tablename__ = "run_manifests"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_date: Mapped[dt.date | None] = mapped_column(Date)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_config_digest: Mapped[str] = mapped_column(
        ForeignKey("artifact_versions.digest"), nullable=False
    )
    data_snapshot_digest: Mapped[str] = mapped_column(
        ForeignKey("artifact_versions.digest"), nullable=False
    )
    feature_set_digest: Mapped[str | None] = mapped_column(ForeignKey("artifact_versions.digest"))
    model_digest: Mapped[str | None] = mapped_column(ForeignKey("artifact_versions.digest"))
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_manifest_asof", "run_kind", "as_of"),
        Index("ix_manifest_digest", "manifest_digest"),
        Index("ix_manifest_session", "session_date", "run_kind"),
    )


class DataSnapshot(Base):
    """The ingestion high-water mark per dataset at a point in time.

    Bounding reads by ``as_of`` is not quite enough for exact replay: a
    backfill that lands after a scan adds rows whose ``knowledge_time`` is
    before that scan's ``as_of``, so replaying it legitimately sees data the
    original run did not. Pinning the maximum ingestion_run_id per dataset
    closes that gap.
    """

    __tablename__ = "data_snapshots"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    max_ingestion_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        UniqueConstraint("digest", "dataset", name="uq_snapshot_dataset"),
        Index("ix_snapshot_digest", "digest"),
    )


# ---------------------------------------------------------------------------
# Derived analytics
# ---------------------------------------------------------------------------


class IndicatorValue(Base):
    """Materialised indicator output. The highest-volume table in the system.

    Volume drives the design: universe x indicators x sessions is roughly
    4,000 x 40 x 252 = 40M rows per year. Consequences:

    * **Range partitioned by session_date** (monthly). Every query is bounded
      by a date window, so partition pruning eliminates almost all of it, and
      dropping old detail is a partition drop rather than a mass DELETE.
    * **Narrow rows, no revision history.** Unlike facts, indicators are
      recomputable from bars plus a feature-set definition, so a recomputation
      replaces rather than appends. ``feature_set_digest`` records which
      definition produced the value; a changed definition writes new rows under
      a new digest rather than silently overwriting.
    * **``value`` is double precision, not NUMERIC.** These are statistics, not
      money -- exactness buys nothing and costs storage and arithmetic speed.

    ``is_warm`` distinguishes "insufficient history" from "genuinely null",
    which the analytics contract requires callers to be able to tell apart.
    """

    __tablename__ = "indicator_values"

    # Natural composite key, no surrogate id. Nothing references an indicator
    # value by id, the natural key is already unique, and on a table of this
    # size a BIGSERIAL plus its index is pure overhead. It also satisfies
    # PostgreSQL's rule that a partitioned table's primary key must contain the
    # partition column.
    instrument_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    indicator: Mapped[str] = mapped_column(String(48), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True, default="1d")
    feature_set_digest: Mapped[str] = mapped_column(String(64), primary_key=True)

    value: Mapped[float | None] = mapped_column(Float)
    is_warm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_indicator_lookup", "instrument_id", "indicator", "session_date"),
        Index("ix_indicator_session", "session_date", "indicator"),
        {"postgresql_partition_by": "RANGE (session_date)"},
    )


class MarketRegimeState(Base):
    """Daily market-environment classification.

    One row per (session, classifier, config): a regime is a property of the
    market, computed once and shared, not recomputed per instrument.
    """

    __tablename__ = "market_regime_states"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    classifier: Mapped[str] = mapped_column(String(48), nullable=False)
    regime: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    benchmark_instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="SET NULL")
    )
    metrics: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    strategy_config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "session_date", "classifier", "strategy_config_digest", name="uq_regime_state"
        ),
        Index("ix_regime_session", "session_date"),
    )


class SectorStrength(Base):
    """Daily sector aggregates for rotation analysis."""

    __tablename__ = "sector_strength"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    scheme: Mapped[str] = mapped_column(String(32), nullable=False)
    sector: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_strength: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pct_above_200ma: Mapped[float | None] = mapped_column(Float)
    feature_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "session_date", "scheme", "sector", "feature_set_digest", name="uq_sector_strength"
        ),
        Index("ix_sector_strength_session", "session_date", "rank"),
    )


# ---------------------------------------------------------------------------
# Opportunity
# ---------------------------------------------------------------------------


class Pattern(Base):
    """One pattern identity, from first detection to terminal state.

    **One row per pattern, not one per session.** The Phase 2 draft of this
    table keyed uniqueness on ``(instrument, type, start_date, detected_on)``,
    which minted a new row every day the same structure was re-detected -- the
    exact failure the Phase 4 brief forbids. Identity now keys on
    ``identity_key``, a content hash of the things that do not change as a
    pattern evolves.

    This row holds *current* state. Everything historical lives in
    ``pattern_observations``, which is append-only, so advancing a pattern never
    destroys what the system believed yesterday.

    ``detector_version`` and ``config_digest`` are not decoration. A scoring
    rule that changes in v1.1 produces different numbers from v1.0, and a
    backtest that silently recomputes history under the newest detector and
    presents the results as unchanged is the specific dishonesty they prevent.
    """

    __tablename__ = "patterns"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    #: Stable across the pattern's life. Content hash of instrument, type,
    #: timeframe and structural start.
    identity_key: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    pattern_type: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="1d")

    #: Provenance. Without these a stored pattern cannot be reproduced.
    detector_name: Mapped[str] = mapped_column(String(40), nullable=False)
    detector_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    data_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    #: Lifecycle. ``previous_state`` is kept alongside the current one so the
    #: most recent transition is answerable without touching the history table.
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(32))
    state_changed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    terminal_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)

    first_detected_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    last_observed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    #: When the *structure* began, which precedes when anyone noticed it.
    structural_start_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    structural_end_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    first_detected_session: Mapped[dt.date] = mapped_column(Date, nullable=False)
    last_observed_session: Mapped[dt.date] = mapped_column(Date, nullable=False)

    quality: Mapped[float] = mapped_column(Float, nullable=False)
    peak_quality: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    resistance_price: Mapped[Decimal | None] = mapped_column(PRICE)
    support_price: Mapped[Decimal | None] = mapped_column(PRICE)
    #: Structural invalidation, emphatically not a stop-loss. A stop belongs to
    #: a position and depends on portfolio risk and sizing, none of which exist
    #: at this stage.
    invalidation_price: Mapped[Decimal | None] = mapped_column(PRICE)

    #: Full geometry, sufficient to redraw the pattern without recomputing it.
    #: Recomputation against a longer series would draw a different pattern and
    #: call it the same one.
    geometry: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    session_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    run_manifest_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        UniqueConstraint("identity_key", "detector_version", name="uq_pattern_identity"),
        Index("ix_pattern_active", "state", "last_observed_session"),
        Index("ix_pattern_instrument", "instrument_id", "pattern_type", "state"),
        CheckConstraint("structural_end_date >= structural_start_date", name="ck_pattern_dates"),
        CheckConstraint("quality >= 0 AND quality <= 100", name="ck_pattern_quality"),
        CheckConstraint(
            "evidence_coverage >= 0 AND evidence_coverage <= 100",
            name="ck_pattern_coverage",
        ),
        CheckConstraint(
            "support_price IS NULL OR resistance_price IS NULL OR support_price < resistance_price",
            name="ck_pattern_support_below_resistance",
        ),
    )


class PatternObservation(Base):
    """Append-only record of what a pattern looked like on one session.

    The reason the pattern table can hold mutable current state without lying:
    every previous belief is here, unchanged. A pattern that was MATURE at
    quality 88 on 40% coverage on Wednesday was exactly that on Wednesday,
    whatever Thursday brought.

    Component scores are stored per observation rather than only currently,
    because "why did this decay?" is answerable from the component history and
    unanswerable from the composite alone.
    """

    __tablename__ = "pattern_observations"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    pattern_id: Mapped[int] = mapped_column(
        ForeignKey("patterns.id", ondelete="CASCADE"), nullable=False
    )
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    #: The clock boundary the detection was computed under.
    knowledge_time: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)

    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)

    quality: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: {name: {score, weight, unavailable, unavailable_reason, measurements}}
    component_scores: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    supporting_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    contradicting_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("pattern_id", "session_date", name="uq_pattern_observation"),
        Index("ix_pattern_observation_session", "session_date"),
        CheckConstraint("quality >= 0 AND quality <= 100", name="ck_observation_quality"),
    )


class PatternRelationship(Base):
    """How two patterns relate: nested, superseding, or merely coincident.

    Kept as edges rather than as columns on ``patterns`` because the
    relationships are many-to-many and directional, and because a weekly VCP
    containing three daily flags is a perfectly ordinary situation that a
    parent_id column would model badly.

    The ontology is deliberately small. ``NESTED_IN`` for a genuine containment
    (a daily flag inside a weekly base), ``SUPERSEDED_BY`` when one structure
    replaced another under a new identity, ``RELATED_TO`` for alternative
    readings of the same geometry -- a structure can be a TIGHT_CONSOLIDATION
    and a BULL_FLAG at once, and forcing exclusivity would discard what a later
    scoring stage is better placed to decide.
    """

    __tablename__ = "pattern_relationships"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    from_pattern_id: Mapped[int] = mapped_column(
        ForeignKey("patterns.id", ondelete="CASCADE"), nullable=False
    )
    to_pattern_id: Mapped[int] = mapped_column(
        ForeignKey("patterns.id", ondelete="CASCADE"), nullable=False
    )
    #: One of the six edges in :mod:`tradeit.patterns.relationships`:
    #: "contains" | "nested_in" | "overlaps" | "related_to" |
    #: "superseded_by" | "derived_from".
    relationship: Mapped[str] = mapped_column(String(24), nullable=False)
    established_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    #: The knowledge boundary the edge was derived under. An edge is a claim
    #: about what was visible at a moment; without this a stored edge cannot be
    #: distinguished from one somebody backdated.
    as_of_session: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "from_pattern_id", "to_pattern_id", "relationship", name="uq_pattern_relationship"
        ),
        Index("ix_pattern_relationship_to", "to_pattern_id", "relationship"),
        CheckConstraint("from_pattern_id <> to_pattern_id", name="ck_pattern_no_self_relation"),
    )


class PatternLabel(Base):
    """A human's opinion of a pattern, for eventual detector evaluation.

    Infrastructure only -- no model is trained on this in Phase 4, and none
    should be until humans have actually labelled real examples.

    Multiple reviewers per example is the point rather than an edge case. A
    single reviewer's rating is one opinion about a subjective judgement, and
    inter-reviewer agreement is what turns a set of opinions into a measurement.
    The detector's own prediction is stored alongside so agreement is computable
    without re-running a possibly-changed detector.
    """

    __tablename__ = "pattern_labels"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    as_of_session: Mapped[dt.date] = mapped_column(Date, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="1d")
    pattern_type: Mapped[str] = mapped_column(String(40), nullable=False)

    reviewer: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Append-only revisions. A reviewer changing their mind is itself a fact
    #: about how hard the example is, and the earlier opinion is what makes it
    #: visible, so a re-review is a new row rather than an update.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: "positive" | "negative" | "ambiguous" | "abstain" | "insufficient_evidence".
    #: See :class:`tradeit.patterns.labeling.HumanLabel` for why the last two
    #: are not merged.
    label: Mapped[str] = mapped_column(String(24), nullable=False, default="positive")
    #: 0-100, on the same scale as the detector so the two are comparable.
    human_quality: Mapped[float | None] = mapped_column(Float)
    #: How sure the reviewer is, 0-100. A confident 40 and an unsure 40 are
    #: different labels and should not be averaged as if they were the same.
    reviewer_confidence: Mapped[float | None] = mapped_column(Float)
    annotated_pivots: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    annotated_boundaries: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    comments: Mapped[str | None] = mapped_column(Text)

    #: What the detector said, pinned at labelling time.
    detector_name: Mapped[str | None] = mapped_column(String(40))
    detector_version: Mapped[int | None] = mapped_column(Integer)
    detector_quality: Mapped[float | None] = mapped_column(Float)
    detector_state: Mapped[str | None] = mapped_column(String(32))
    #: Coverage at labelling time. Agreement between a human and a detector that
    #: was scoring on 40% of its intended evidence is a different measurement
    #: from agreement with one that had everything.
    detector_coverage: Mapped[float | None] = mapped_column(Float)
    #: The configuration the detector ran under, so the prediction is
    #: reproducible rather than merely recorded.
    config_digest: Mapped[str | None] = mapped_column(String(64))
    pattern_id: Mapped[int | None] = mapped_column(ForeignKey("patterns.id", ondelete="SET NULL"))

    labelled_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "as_of_session",
            "timeframe",
            "pattern_type",
            "reviewer",
            "revision",
            name="uq_pattern_label",
        ),
        Index("ix_pattern_label_lookup", "pattern_type", "as_of_session"),
        CheckConstraint(
            "human_quality IS NULL OR (human_quality >= 0 AND human_quality <= 100)",
            name="ck_label_quality",
        ),
    )


class BreakoutEvent(Base):
    """One breakout attempt at one boundary, from first approach to resolution.

    **One row per attempt, not one per session and not one per status.** The
    Phase 2 draft keyed uniqueness on ``(instrument, pattern, session, status)``,
    which is a log rather than an identity: an attempt had no row of its own, so
    "how many attempts has this pattern made at this level?" was unanswerable
    and the attempt history item 26 requires could not exist. Identity now keys
    on ``event_key``, a content hash of the instrument, timeframe, pattern and
    attempt number.

    This row holds *current* state. Everything historical lives in
    ``breakout_observations``, which is append-only, so an event that failed on
    Thursday still records that it was CONFIRMED on Tuesday.

    The frozen breakout boundary is stored in full rather than referenced
    through the pattern, and that is deliberate: the pattern layer legitimately
    refines its levels as touches accumulate, so an event read back through a
    live pattern would be judged against a level that partly reflects the
    breakout it is judging.
    """

    __tablename__ = "breakout_events"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    #: Stable across the attempt's life. Content hash of instrument, timeframe,
    #: pattern identity and attempt number.
    event_key: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    pattern_id: Mapped[int | None] = mapped_column(ForeignKey("patterns.id", ondelete="SET NULL"))
    #: The pattern's own identity key, kept alongside the foreign key so an
    #: event survives the pattern row being pruned without losing what it was
    #: attached to.
    pattern_key: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="1d")
    #: 1 for the first go at this boundary. Never reused, never overwritten.
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    #: Provenance across both layers. Without all of these a stored event
    #: cannot be reproduced from the data that produced it.
    pattern_detector_name: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    pattern_detector_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pattern_config_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    breakout_config_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    scorer_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: Which confirmation policy the event was evaluated under. Two events under
    #: different profiles are not comparable, and a stored state that does not
    #: say which policy produced it cannot be interpreted.
    profile: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    state: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(32))
    terminal_reason: Mapped[str | None] = mapped_column(String(40))
    #: Which of momentum / retest / acceptance produced a confirmation.
    confirmed_path: Mapped[str] = mapped_column(String(16), nullable=False, default="none")

    opened_session: Mapped[dt.date] = mapped_column(Date, nullable=False)
    #: The three timestamps item 1 asks for, kept apart because they answer
    #: different questions and are frequently different days.
    first_approach_session: Mapped[dt.date | None] = mapped_column(Date)
    first_penetration_session: Mapped[dt.date | None] = mapped_column(Date)
    first_qualifying_close_session: Mapped[dt.date | None] = mapped_column(Date)
    confirmed_session: Mapped[dt.date | None] = mapped_column(Date)
    last_observed_session: Mapped[dt.date] = mapped_column(Date, nullable=False)

    #: The frozen boundary. ``tolerance_pct`` and ``atr_at_open`` are stored so
    #: the zone can be reconstructed exactly rather than recomputed against
    #: whatever volatility is current when the row is read.
    boundary_level: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    boundary_anchor_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    boundary_tolerance_pct: Mapped[float] = mapped_column(Float, nullable=False)
    boundary_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    boundary_method: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    boundary_touches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    boundary_slope: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    atr_at_open: Mapped[float | None] = mapped_column(Float)
    pattern_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    pattern_quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: "structural_pattern_boundary" | "manual_boundary" |
    #: "experimental_boundary" | "other". A crossing of a level somebody typed
    #: into a notebook and a breakout of a causally-derived structure are both
    #: real observations and are not the same object; downstream systems must be
    #: able to separate the populations without inferring it from a score.
    #: See ADR-0025.
    boundary_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="other", server_default="other"
    )

    #: Frozen at the breakout bar; see ADR-0021.
    breakout_quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Recomputed each session from post-breakout evidence only.
    confirmation_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    breakout_close: Mapped[Decimal | None] = mapped_column(PRICE)
    breakout_low: Mapped[Decimal | None] = mapped_column(PRICE)
    breakout_volume: Mapped[float | None] = mapped_column(Float)
    qualifying_closes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    gap_class: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    #: "no_earnings_nearby" | "earnings_event_nearby" | "post_earnings_gap" |
    #: "unknown_event_context". The last is not a synonym for the first: an
    #: event evaluated without an earnings calendar must not be readable as one
    #: where the answer was no.
    earnings_context: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown_event_context"
    )

    #: The retest episode, when one occurred. Kept after it resolves.
    retest: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    quality_components: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    confirmation_components: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)

    run_manifest_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="RESTRICT")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("event_key", name="uq_breakout_event_identity"),
        UniqueConstraint(
            "instrument_id",
            "timeframe",
            "pattern_key",
            "attempt_number",
            name="uq_breakout_attempt",
        ),
        Index("ix_breakout_active", "state", "last_observed_session"),
        Index("ix_breakout_instrument", "instrument_id", "timeframe", "state"),
        Index("ix_breakout_boundary_kind", "boundary_kind", "state"),
        Index("ix_breakout_pattern", "pattern_id", "opened_session"),
        CheckConstraint("attempt_number >= 1", name="ck_breakout_attempt"),
        CheckConstraint(
            "breakout_quality >= 0 AND breakout_quality <= 100", name="ck_breakout_quality"
        ),
        CheckConstraint(
            "confirmation_score >= 0 AND confirmation_score <= 100",
            name="ck_breakout_confirmation",
        ),
        CheckConstraint(
            "evidence_coverage >= 0 AND evidence_coverage <= 100", name="ck_breakout_coverage"
        ),
    )


class BreakoutObservation(Base):
    """Append-only record of what a breakout event looked like on one session.

    The reason the event row can hold mutable current state without lying.
    Item 15 of the Phase 5 brief requires that the progression CLOSED_ABOVE →
    CONFIRMATION_PENDING → CONFIRMED → FAILED_BREAKOUT survive the failure, and
    the only reliable way to guarantee that is to make the earlier rows
    unwritable: the unique constraint on ``(event_id, session_date)`` turns a
    second write for a session into a conflict rather than an overwrite.

    ``measurements`` carries everything the engine measured that session, which
    is what makes "why did the confirmation score fall on Thursday?" answerable.
    """

    __tablename__ = "breakout_observations"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("breakout_events.id", ondelete="CASCADE"), nullable=False
    )
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    #: The clock boundary the evaluation was computed under.
    knowledge_time: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)

    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    path: Mapped[str | None] = mapped_column(String(16))

    breakout_quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confirmation_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    distance_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    distance_atr: Mapped[float | None] = mapped_column(Float)

    measurements: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    supporting_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    contradicting_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("event_id", "session_date", name="uq_breakout_observation"),
        Index("ix_breakout_observation_session", "session_date"),
        CheckConstraint(
            "breakout_quality >= 0 AND breakout_quality <= 100",
            name="ck_breakout_observation_quality",
        ),
    )


class BreakoutRelationship(Base):
    """How two breakout events relate.

    A deliberately small ontology (item 40 warns against excessive ontology):
    the same structural region approached on two timeframes, a nested pattern's
    breakout inside a larger one, or a later event retesting an earlier one's
    level. Anything finer would be a taxonomy nobody maintains.
    """

    __tablename__ = "breakout_relationships"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    from_event_id: Mapped[int] = mapped_column(
        ForeignKey("breakout_events.id", ondelete="CASCADE"), nullable=False
    )
    to_event_id: Mapped[int] = mapped_column(
        ForeignKey("breakout_events.id", ondelete="CASCADE"), nullable=False
    )
    #: "same_region" | "cross_timeframe" | "nested_breakout" | "retest_of" |
    #: "later_attempt".
    relationship: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The knowledge boundary the edge was derived under, for the same reason
    #: pattern relationships carry one: an edge is a claim about what was
    #: visible at a moment.
    as_of_session: Mapped[dt.date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "from_event_id", "to_event_id", "relationship", name="uq_breakout_relationship"
        ),
        Index("ix_breakout_relationship_to", "to_event_id", "relationship"),
        CheckConstraint("from_event_id <> to_event_id", name="ck_breakout_no_self_relation"),
    )


class BreakoutLabel(Base):
    """A human's structural judgement of a breakout event.

    **Structural and confirmation labels only.** Nobody is asked "did the stock
    make money afterwards?" for this dataset, and there is no column in which
    that answer could be recorded. Future outcomes attach separately, later,
    with their own knowledge horizon — mixing them in here would produce a
    dataset whose labels silently encode returns and whose every downstream use
    would be circular.

    ``knowledge_horizon_session`` is the field that makes the labels honest. A
    FALSE_BREAKOUT label cannot be assigned from the breakout bar alone; it
    needs the window in which the failure became visible. Recording the last
    session the reviewer was shown makes the horizon explicit rather than
    leaving it to be guessed from the label's meaning.
    """

    __tablename__ = "breakout_labels"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("breakout_events.id", ondelete="SET NULL")
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    as_of_session: Mapped[dt.date] = mapped_column(Date, nullable=False)
    #: The last session the reviewer could see. Never later than the label's
    #: definition requires, and stored so a label's horizon is auditable.
    knowledge_horizon_session: Mapped[dt.date] = mapped_column(Date, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="1d")

    reviewer: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Append-only revisions, for the same reason pattern labels have them.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: "valid_breakout" | "weak_breakout" | "false_breakout" |
    #: "successful_retest" | "failed_retest" | "ambiguous" |
    #: "insufficient_evidence".
    label: Mapped[str] = mapped_column(String(24), nullable=False)
    reviewer_confidence: Mapped[float | None] = mapped_column(Float)
    comments: Mapped[str | None] = mapped_column(Text)

    #: What the engine said, pinned at labelling time.
    engine_state: Mapped[str | None] = mapped_column(String(32))
    engine_breakout_quality: Mapped[float | None] = mapped_column(Float)
    engine_confirmation_score: Mapped[float | None] = mapped_column(Float)
    engine_coverage: Mapped[float | None] = mapped_column(Float)
    engine_profile: Mapped[str | None] = mapped_column(String(32))
    breakout_config_digest: Mapped[str | None] = mapped_column(String(64))
    scorer_version: Mapped[int | None] = mapped_column(Integer)

    labelled_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "as_of_session",
            "timeframe",
            "reviewer",
            "revision",
            name="uq_breakout_label",
        ),
        Index("ix_breakout_label_lookup", "label", "as_of_session"),
        CheckConstraint(
            "knowledge_horizon_session >= as_of_session", name="ck_breakout_label_horizon"
        ),
    )


class OpportunityScore(Base):
    """A scored candidate with its factor breakdown.

    ``components`` holds the per-factor contributions as JSON. The brief
    requires every recommendation to be explainable, and an explanation
    reconstructed later from a total is a rationalisation -- so the breakdown is
    stored with the score, not derived on demand.

    ``total`` is a real column because it is what everything sorts by;
    the components are JSON because their names change with the scoring
    configuration and a column per factor would mean a migration per experiment.
    """

    __tablename__ = "opportunity_scores"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False, default="long")
    total: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict[str, object]] = mapped_column(JSONB_OR_JSON, nullable=False)
    features: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    pattern_id: Mapped[int | None] = mapped_column(ForeignKey("patterns.id", ondelete="SET NULL"))
    breakout_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("breakout_events.id", ondelete="SET NULL")
    )
    rank: Mapped[int | None] = mapped_column(Integer)
    run_manifest_id: Mapped[int] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "session_date", "run_manifest_id", name="uq_score_per_run"
        ),
        Index("ix_score_ranking", "session_date", "total"),
        Index("ix_score_instrument", "instrument_id", "session_date"),
        CheckConstraint("total >= 0 AND total <= 1", name="ck_score_range"),
    )


class Watchlist(Base):
    """A named, dated set of instruments under observation."""

    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="scan")
    run_manifest_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("name", "session_date", name="uq_watchlist_day"),
        Index("ix_watchlist_session", "session_date"),
    )


class WatchlistMember(Base):
    """An instrument on a watchlist, with why it is there."""

    __tablename__ = "watchlist_members"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    score_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunity_scores.id", ondelete="SET NULL")
    )
    rank: Mapped[int | None] = mapped_column(Integer)
    trigger_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("watchlist_id", "instrument_id", name="uq_watchlist_member"),
        Index("ix_watchlist_member_rank", "watchlist_id", "rank"),
    )


class ScreenRejection(Base):
    """Why an instrument did NOT make the cut.

    Recording only what passed makes a screen impossible to debug: "why isn't
    NVDA on the list?" requires re-running the whole chain otherwise. Rows are
    retained for a bounded window (see the maintenance job) because volume is
    high and their value decays quickly.
    """

    __tablename__ = "screen_rejections"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    run_manifest_id: Mapped[int] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    filter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    measured_value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_rejection_lookup", "session_date", "instrument_id"),
        Index("ix_rejection_filter", "run_manifest_id", "filter_name"),
    )


# ---------------------------------------------------------------------------
# Strategy and portfolio
# ---------------------------------------------------------------------------


class Strategy(Base):
    """A named strategy. Its parameters live in versioned configurations.

    The split matters: a strategy is a long-lived identity ("baseline breakout")
    while its configuration changes over time. Separating them means the
    performance history of a strategy survives a parameter change, and the
    change itself is visible as a configuration transition rather than as a
    discontinuity nobody can explain.
    """

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StrategyConfiguration(Base):
    """A versioned parameter set, active over a time interval.

    ``activated_at`` / ``deactivated_at`` make "which configuration was live on
    14 March?" a query. Without the interval, a live result cannot be matched to
    the rules that produced it, and every performance comparison across a
    parameter change is meaningless.
    """

    __tablename__ = "strategy_configurations"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    digest: Mapped[str] = mapped_column(
        ForeignKey("artifact_versions.digest", ondelete="RESTRICT"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("strategy_id", "digest", name="uq_strategy_config"),
        Index("ix_strategy_config_active", "strategy_id", "activated_at", "deactivated_at"),
        CheckConstraint(
            "deactivated_at IS NULL OR activated_at IS NULL OR deactivated_at > activated_at",
            name="ck_strategy_config_interval",
        ),
    )


class Portfolio(Base):
    """An account the system manages.

    ``mode`` is on the portfolio, not global: a paper portfolio and a live one
    can coexist, and every position, order and snapshot inherits its mode from
    the portfolio it belongs to. That is what stops a backtest's fills from ever
    being confused with real ones.
    """

    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL")
    )
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    initial_capital: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    inception_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    broker_account_ref: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("initial_capital > 0", name="ck_portfolio_capital"),
        CheckConstraint("mode IN ('backtest', 'paper', 'live')", name="ck_portfolio_mode"),
    )


class Position(Base):
    """An open or closed holding.

    ``stop_price`` is nullable in the schema but required by the domain while a
    position is open -- a position without a stop has undefined risk and cannot
    be sized or aggregated into portfolio heat. The CHECK enforces it.

    Quantities and prices are mutable here because a position is an aggregate
    over its fills. The immutable record is ``executions``; this table is the
    running total, and it must always be derivable from them.
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(8), nullable=False, default="long")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal(0))
    average_entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE)
    initial_stop_price: Mapped[Decimal | None] = mapped_column(PRICE)
    target_price: Mapped[Decimal | None] = mapped_column(PRICE)
    initial_risk_amount: Mapped[Decimal | None] = mapped_column(VALUE)
    opened_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    closed_on: Mapped[dt.date | None] = mapped_column(Date)
    realised_pnl: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    pyramid_entries: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entry_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunity_scores.id", ondelete="SET NULL")
    )
    entry_run_manifest_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_position_open", "portfolio_id", "status"),
        Index("ix_position_instrument", "portfolio_id", "instrument_id", "opened_on"),
        CheckConstraint("quantity >= 0", name="ck_position_quantity"),
        CheckConstraint(
            "status <> 'open' OR stop_price IS NOT NULL", name="ck_position_open_has_stop"
        ),
        CheckConstraint("closed_on IS NULL OR closed_on >= opened_on", name="ck_position_dates"),
    )


class Order(Base):
    """An order the system created. Immutable except for its status lifecycle.

    ``client_order_id`` is unique and generated before submission, which is what
    makes retrying a timed-out submission safe: the same id is a no-op at the
    broker rather than a second position.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id", ondelete="SET NULL"))
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), nullable=False
    )
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    time_in_force: Mapped[str] = mapped_column(String(8), nullable=False, default="day")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending_new")
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal(0))
    limit_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE)
    intent: Mapped[str | None] = mapped_column(String(128))
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    run_manifest_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_order_portfolio", "portfolio_id", "created_at"),
        Index("ix_order_status", "status", "created_at"),
        Index("ix_order_broker", "broker_order_id"),
        CheckConstraint("quantity > 0", name="ck_order_quantity"),
        CheckConstraint(
            "filled_quantity >= 0 AND filled_quantity <= quantity", name="ck_order_filled"
        ),
    )


class Execution(Base):
    """A fill. Append-only and immutable -- the ground truth for everything else.

    Partial fills are separate rows. A position's average entry price is
    derivable from its executions, and if the two ever disagree the executions
    are right. Commission and slippage are separate columns because conflating
    them makes an expensive strategy indistinguishable from a badly modelled
    one.
    """

    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    commission: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    slippage: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    filled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    venue: Mapped[str | None] = mapped_column(String(32))
    broker_execution_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("broker_execution_id", name="uq_execution_broker"),
        Index("ix_execution_order", "order_id"),
        Index("ix_execution_portfolio", "portfolio_id", "session_date"),
        CheckConstraint("quantity > 0 AND price > 0", name="ck_execution_positive"),
    )


class Trade(Base):
    """A completed round trip, denormalised for analysis.

    Derivable from positions and executions, and stored anyway: every
    post-trade query joins four tables otherwise, and the analysis that
    actually gets done is the analysis that is cheap to run.

    ``r_multiple`` -- profit measured in units of initial risk -- is the
    headline number rather than dollars, because the brief requires
    risk-adjusted percentage returns to take precedence over raw profit.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    entry_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    exit_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    costs: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    net_pnl: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    r_multiple: Mapped[float | None] = mapped_column(Float)
    holding_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    mae: Mapped[Decimal | None] = mapped_column(VALUE)
    mfe: Mapped[Decimal | None] = mapped_column(VALUE)
    score_at_entry: Mapped[float | None] = mapped_column(Float)
    regime_at_entry: Mapped[str | None] = mapped_column(String(24))
    sector_at_entry: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("position_id", name="uq_trade_position"),
        Index("ix_trade_portfolio", "portfolio_id", "exit_date"),
        Index("ix_trade_analysis", "portfolio_id", "exit_reason", "exit_date"),
        CheckConstraint("exit_date >= entry_date", name="ck_trade_dates"),
    )


class PortfolioSnapshot(Base):
    """End-of-session portfolio state. The equity curve, one row per session.

    Written whether or not anything traded. Reconstructing an equity curve from
    trades alone is impossible: it omits open-position marks, which is most of
    the volatility.
    """

    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cash: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    equity: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    long_market_value: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    short_market_value: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    position_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realised_pnl_to_date: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    unrealised_pnl: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    peak_equity: Mapped[Decimal | None] = mapped_column(VALUE)
    drawdown_pct: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("portfolio_id", "session_date", name="uq_portfolio_snapshot"),
        Index("ix_snapshot_curve", "portfolio_id", "session_date"),
    )


class RiskSnapshot(Base):
    """Daily risk metrics, recorded whether or not anything traded.

    The value is in the quiet periods: this is what answers "was that drawdown
    a risk-control failure or an ordinary run of losses?" months later, when
    nobody remembers.
    """

    __tablename__ = "risk_snapshots"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    open_risk: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    heat_pct: Mapped[float] = mapped_column(Float, nullable=False)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    largest_position_pct: Mapped[float | None] = mapped_column(Float)
    gross_exposure_pct: Mapped[float | None] = mapped_column(Float)
    max_pairwise_correlation: Mapped[float | None] = mapped_column(Float)
    drawdown_from_peak_pct: Mapped[float | None] = mapped_column(Float)
    sector_exposures: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    limit_breaches: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    strategy_config_digest: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("portfolio_id", "session_date", name="uq_risk_snapshot"),
        Index("ix_risk_snapshot_breach", "portfolio_id", "session_date"),
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class BacktestRun(Base):
    """One backtest, its specification, and its headline metrics.

    ``data_caveats`` is not decoration. It carries forward the provider
    limitations recorded at ingestion -- survivorship-unsafe universe, estimated
    filing dates, adjusted-only prices -- so a result cannot be quoted as clean
    when the data underneath it was not. Eight months later nobody remembers
    which vendor was in use.
    """

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    run_manifest_id: Mapped[int] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    start_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    end_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    universe: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_capital: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    cost_model: Mapped[str] = mapped_column(String(48), nullable=False)
    fill_model: Mapped[str] = mapped_column(String(48), nullable=False)
    walk_forward_window: Mapped[int | None] = mapped_column(Integer)
    is_out_of_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    total_return_pct: Mapped[float | None] = mapped_column(Float)
    cagr: Mapped[float | None] = mapped_column(Float)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float)
    sharpe: Mapped[float | None] = mapped_column(Float)
    sortino: Mapped[float | None] = mapped_column(Float)
    calmar: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    expectancy_r: Mapped[float | None] = mapped_column(Float)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exposure_pct: Mapped[float | None] = mapped_column(Float)
    benchmark_return_pct: Mapped[float | None] = mapped_column(Float)

    metrics: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    equity_curve: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    data_caveats: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    warnings: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_backtest_strategy", "strategy_id", "created_at"),
        Index("ix_backtest_status", "status", "created_at"),
        CheckConstraint("end_date > start_date", name="ck_backtest_dates"),
    )


class BacktestTrade(Base):
    """Trades produced by a backtest.

    Structurally near-identical to ``trades`` but kept separate on purpose:
    simulated fills must never be queryable alongside real ones. One accidental
    UNION between them and a live performance report becomes fiction.
    """

    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    entry_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    exit_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    costs: Mapped[Decimal] = mapped_column(VALUE, nullable=False, default=Decimal(0))
    net_pnl: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    r_multiple: Mapped[float | None] = mapped_column(Float)
    holding_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    mae: Mapped[Decimal | None] = mapped_column(VALUE)
    mfe: Mapped[Decimal | None] = mapped_column(VALUE)
    score_at_entry: Mapped[float | None] = mapped_column(Float)
    regime_at_entry: Mapped[str | None] = mapped_column(String(24))
    sector_at_entry: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_backtest_trade_run", "backtest_run_id", "exit_date"),
        Index("ix_backtest_trade_attr", "backtest_run_id", "exit_reason"),
        CheckConstraint("exit_date >= entry_date", name="ck_backtest_trade_dates"),
    )


class MonteCarloRun(Base):
    """A distributional study over a backtest result.

    Percentiles are stored rather than a point estimate. The 5th percentile of
    maximum drawdown is the number that should size a live position; the
    drawdown that happened to occur in one historical ordering is an anecdote.
    """

    __tablename__ = "monte_carlo_runs"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parameters: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    return_percentiles: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    drawdown_percentiles: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    ruin_probability: Mapped[float | None] = mapped_column(Float)
    iterations_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_montecarlo_backtest", "backtest_run_id", "method"),
        CheckConstraint("iterations >= 100", name="ck_montecarlo_iterations"),
    )


class ModelMetadata(Base):
    """Registry for learned models, if and when any exist.

    No phase currently trains a model. The table exists because a model
    introduced later without training-window metadata is a model whose
    look-ahead status cannot be audited, and retrofitting that is much harder
    than recording it from the start.

    ``training_end`` is the field that matters: a model trained on data through
    2023 must not be used to score 2022, and that check is only possible if the
    boundary was recorded.
    """

    __tablename__ = "model_metadata"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    digest: Mapped[str] = mapped_column(
        ForeignKey("artifact_versions.digest", ondelete="RESTRICT"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    model_type: Mapped[str] = mapped_column(String(48), nullable=False)
    framework: Mapped[str | None] = mapped_column(String(48))
    training_start: Mapped[dt.date | None] = mapped_column(Date)
    training_end: Mapped[dt.date | None] = mapped_column(Date)
    validation_start: Mapped[dt.date | None] = mapped_column(Date)
    validation_end: Mapped[dt.date | None] = mapped_column(Date)
    feature_set_digest: Mapped[str | None] = mapped_column(String(64))
    hyperparameters: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    metrics: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    artifact_uri: Mapped[str | None] = mapped_column(String(512))
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_model_name", "name", "created_at"),
        CheckConstraint(
            "training_end IS NULL OR training_start IS NULL OR training_end > training_start",
            name="ck_model_training_window",
        ),
        CheckConstraint(
            "validation_start IS NULL OR training_end IS NULL OR validation_start >= training_end",
            name="ck_model_validation_after_training",
        ),
    )


# ---------------------------------------------------------------------------
# Journal and operations
# ---------------------------------------------------------------------------


class JournalEntry(Base):
    """The trade journal. Append-only, and the system's memory.

    ``context`` holds the full decision environment at the moment of the entry:
    the score breakdown, the portfolio state, the risk verdict, and the
    candidates that were rejected in favour of this one. That last part is what
    makes the journal worth keeping -- a record of what was bought explains
    nothing without a record of what was passed over.
    """

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int | None] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE")
    )
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id", ondelete="SET NULL"))
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"))
    instrument_id: Mapped[int | None] = mapped_column(BigInteger)
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[str] = mapped_column(String(512), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    run_manifest_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_manifests.id", ondelete="SET NULL")
    )
    author: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_journal_portfolio", "portfolio_id", "session_date"),
        Index("ix_journal_position", "position_id", "occurred_at"),
        Index("ix_journal_type", "entry_type", "session_date"),
    )


class JobRun(Base):
    """Execution record for every scheduled job.

    The trading gate reads this table: before generating a plan, the system
    checks that every ``blocks_trading`` job completed for the session. Without
    it, a failed ingestion followed by a successful scan produces
    recommendations built on yesterday's prices.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    session_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    rows_processed: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)

    __table_args__ = (
        UniqueConstraint("job_name", "session_date", "attempt", name="uq_job_attempt"),
        Index("ix_job_recent", "job_name", "started_at"),
        Index("ix_job_session_status", "session_date", "status"),
    )


class DataQualityIssue(Base):
    """Findings from the validation job.

    Separate from ``quarantined_rows``: that table holds rows that failed to
    parse, this one holds problems detected *across* rows -- a missing session,
    a 40% price move with no corresponding corporate action, a series that has
    not updated in a week. Those are invisible row by row.
    """

    __tablename__ = "data_quality_issues"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    detected_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(BigInteger)
    session_date: Mapped[dt.date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    blocks_trading: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "check_name", "dataset", "instrument_id", "session_date", name="uq_quality_issue"
        ),
        Index("ix_quality_open", "detected_on", "severity", "resolved_at"),
        Index("ix_quality_blocking", "blocks_trading", "resolved_at"),
    )


class SystemLog(Base):
    """Structured application events worth keeping in the database.

    Not a replacement for stdout logging -- that goes to the log aggregator.
    This table holds the subset that must be queryable alongside trading data:
    risk overrides, safety-interlock trips, reconciliation breaks, config
    activations. Partitioned monthly and dropped on a retention schedule.
    """

    __tablename__ = "system_logs"

    # (logged_at, event_id) rather than a serial id: the partition column must
    # be part of the primary key, and a client-generated UUID avoids a
    # sequence, which matters for a table written from several workers at once.
    logged_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    level: Mapped[str] = mapped_column(String(12), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    event: Mapped[str] = mapped_column(String(96), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    portfolio_id: Mapped[int | None] = mapped_column(BigInteger)
    instrument_id: Mapped[int | None] = mapped_column(BigInteger)
    run_manifest_id: Mapped[int | None] = mapped_column(BigInteger)
    context: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    actor: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_syslog_time", "logged_at", "level"),
        Index("ix_syslog_category", "category", "logged_at"),
        {"postgresql_partition_by": "RANGE (logged_at)"},
    )


# ===========================================================================
# Phase 3 schema: analytics outputs.
#
# All derived, all recomputable, all carrying the feature-set digest that
# produced them. The recurring column across these tables is
# ``universe_digest`` / ``universe_size``: every cross-sectional or market-level
# measure records the roster it was computed over, because "68% above their
# 200DMA" is meaningless without "of what?" -- and a breadth series whose
# universe silently changed size is not a series.
# ===========================================================================


class RelativeStrengthValue(Base):
    """Benchmark-relative and cross-sectional strength, per instrument and date.

    Dimensioned by (instrument, session, benchmark, lookback) because the whole
    point of the multi-benchmark design is that a name beating SPY while lagging
    QQQ is a distinguishable state. Collapsing to one benchmark would discard
    exactly what the configuration was written to capture.

    Partitioned monthly like ``indicator_values``: the row count is
    universe x benchmarks x lookbacks x sessions, which is roughly 12x the
    indicator table for the default 3 benchmarks and 4 lookbacks.
    """

    __tablename__ = "relative_strength_values"

    instrument_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    benchmark_symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    lookback: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_set_digest: Mapped[str] = mapped_column(String(64), primary_key=True)

    security_return: Mapped[float | None] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    relative_performance: Mapped[float | None] = mapped_column(Float)
    excess_return: Mapped[float | None] = mapped_column(Float)
    relative_trend: Mapped[float | None] = mapped_column(Float)
    relative_momentum: Mapped[float | None] = mapped_column(Float)
    universe_percentile: Mapped[float | None] = mapped_column(Float)
    sector_percentile: Mapped[float | None] = mapped_column(Float)
    industry_percentile: Mapped[float | None] = mapped_column(Float)
    rs_score: Mapped[float | None] = mapped_column(Float)
    #: How many instruments the percentile was computed against. Recorded so a
    #: historical rank can be audited: "8th of what, exactly?"
    ranking_universe_size: Mapped[int | None] = mapped_column(Integer)
    ranking_universe_digest: Mapped[str | None] = mapped_column(String(64))
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_rs_lookup", "instrument_id", "benchmark_symbol", "session_date"),
        Index("ix_rs_ranking", "session_date", "benchmark_symbol", "rs_score"),
        CheckConstraint(
            "universe_percentile IS NULL OR (universe_percentile >= 0 "
            "AND universe_percentile <= 1)",
            name="ck_rs_percentile_range",
        ),
        CheckConstraint(
            "rs_score IS NULL OR (rs_score >= 0 AND rs_score <= 100)",
            name="ck_rs_score_range",
        ),
        CheckConstraint("lookback > 0", name="ck_rs_lookback"),
        {"postgresql_partition_by": "RANGE (session_date)"},
    )


class MarketBreadthSnapshot(Base):
    """Daily breadth over an explicitly recorded eligible universe.

    ``universe_digest`` is part of the measurement, not metadata. Comparing a
    2008 reading computed over 500 survivors with a 2024 reading computed over
    4,000 names is comparing two different statistics, and the digest is what
    makes that detectable rather than invisible.
    """

    __tablename__ = "market_breadth_snapshots"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    universe_name: Mapped[str] = mapped_column(String(64), nullable=False)
    universe_size: Mapped[int] = mapped_column(Integer, nullable=False)
    universe_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    advances: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    declines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    advance_volume: Mapped[Decimal | None] = mapped_column(VALUE)
    decline_volume: Mapped[Decimal | None] = mapped_column(VALUE)
    pct_above_ma: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    new_highs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_lows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ad_line: Mapped[int | None] = mapped_column(BigInteger)
    thrust_ratio: Mapped[float | None] = mapped_column(Float)
    #: Set when the roster was too small for the percentages to mean much.
    #: Downstream must be able to distinguish "40% above their 200DMA out of
    #: 4,000" from the same figure out of 12.
    low_confidence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "session_date", "universe_name", "feature_set_digest", name="uq_breadth_snapshot"
        ),
        Index("ix_breadth_session", "session_date"),
        CheckConstraint("evaluated <= universe_size", name="ck_breadth_evaluated"),
        CheckConstraint("universe_size >= 0", name="ck_breadth_universe_size"),
    )


class VolatilityRegimeState(Base):
    """Daily volatility regime with its evidence.

    Separate from ``market_regime_states`` because the two genuinely differ: a
    market can trend strongly with elevated volatility, and one row carrying
    both would force a single confidence number for two independent judgements.
    """

    __tablename__ = "volatility_regime_states"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="market")
    instrument_id: Mapped[int | None] = mapped_column(BigInteger)
    regime: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realized_volatility: Mapped[float | None] = mapped_column(Float)
    volatility_percentile: Mapped[float | None] = mapped_column(Float)
    atr_percent: Mapped[float | None] = mapped_column(Float)
    gap_frequency: Mapped[float | None] = mapped_column(Float)
    cross_sectional_volatility: Mapped[float | None] = mapped_column(Float)
    expansion: Mapped[float | None] = mapped_column(Float)
    supporting_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    contradicting_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    strategy_config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "session_date",
            "scope",
            "instrument_id",
            "strategy_config_digest",
            name="uq_volatility_regime",
        ),
        Index("ix_volatility_regime_session", "session_date", "scope"),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_volatility_confidence"),
    )


class FeatureDefinition(Base):
    """Persisted feature registry.

    The in-process registry is the source of truth during a run; this table is
    its durable record, so a feature-set digest referenced by a two-year-old
    backtest can still be explained after the code that defined it has changed.

    Keyed by (digest) rather than name: two definitions of ``rs_score`` with
    different lookbacks are different features, and giving them one row would
    lose exactly the distinction the registry exists to preserve.
    """

    __tablename__ = "feature_definitions"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    calculation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    output_type: Mapped[str] = mapped_column(String(24), nullable=False)
    null_behaviour: Mapped[str] = mapped_column(String(24), nullable=False)
    input_datasets: Mapped[dict[str, object]] = mapped_column(JSONB_OR_JSON, nullable=False)
    warmup_periods: Mapped[int] = mapped_column(Integer, nullable=False)
    lookback_sessions: Mapped[int | None] = mapped_column(Integer)
    parameters: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    availability: Mapped[str | None] = mapped_column(Text)
    depends_on: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    deprecated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_feature_name", "name", "version", "calculation_version"),
        Index("ix_feature_kind", "kind", "deprecated"),
        CheckConstraint("warmup_periods >= 0", name="ck_feature_warmup"),
    )


class FeatureSetMember(Base):
    """Which feature definitions belong to which feature-set digest.

    The join that makes a two-year-old ``feature_set_digest`` explainable: given
    the digest, list exactly the definitions that were active.
    """

    __tablename__ = "feature_set_members"

    feature_set_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    feature_digest: Mapped[str] = mapped_column(
        ForeignKey("feature_definitions.digest", ondelete="RESTRICT"), primary_key=True
    )
    feature_name: Mapped[str] = mapped_column(String(96), nullable=False)

    __table_args__ = (Index("ix_feature_set_lookup", "feature_set_digest", "feature_name"),)


# ===========================================================================
# Offline data packages (empirical validation gate).
#
# These tables answer one question: which bytes produced this result? A
# validation run cites a snapshot id; the snapshot id resolves to a package
# row; the package row lists every file with the SHA-256 that was verified
# before a single line was read. Nothing here stores market data — the facts
# land in the Phase 1 tables above. What lands here is provenance.
# ===========================================================================


class DataPackage(Base):
    """One import of one offline package, with what the package claimed.

    ``adjustment_policy`` is stored rather than referenced because a package's
    claim about its own prices is part of the evidence: a result computed from
    split-adjusted prices is a different result from one computed from raw
    prints, and six months later the only record of which it was is this row.

    ``snapshot_id`` is the identity a validation run cites. It is derived from
    the manifest digest and the observed row counts, so an import that aborted
    halfway cannot share an id with one that finished over the same files.
    """

    __tablename__ = "data_packages"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    format_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    export_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    coverage_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    #: What the data actually spans, which is not always what it claimed.
    observed_start: Mapped[dt.date | None] = mapped_column(Date)
    observed_end: Mapped[dt.date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    adjustment_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text)
    licence_note: Mapped[str | None] = mapped_column(Text)
    vendor_dataset: Mapped[str | None] = mapped_column(String(128))
    known_limitations: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    #: The full import report payload: per-dataset counts, quarantine reasons,
    #: flags, problems and notes. Stored whole so a stale summary elsewhere can
    #: always be checked against it.
    report: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    rows_read: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rows_quarantined: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Rows whose knowledge_time came from a lag rule rather than a filing
    #: timestamp. Counted at the package level so that "how much of this result
    #: rests on an assumption?" is one column rather than an audit.
    rows_estimated_knowledge_time: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    digests_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: True when a row limit was in force or the run aborted. A partial package
    #: is a smoke test and must never be cited as evidence.
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    aborted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    code_version: Mapped[str | None] = mapped_column(String(64))
    imported_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_data_package_snapshot"),
        Index("ix_data_package_name", "name", "imported_at"),
        CheckConstraint("coverage_end >= coverage_start", name="ck_package_coverage"),
        CheckConstraint(
            "adjustment_policy IN ('raw_unadjusted', 'split_adjusted', "
            "'total_return_adjusted', 'unknown')",
            name="ck_package_adjustment",
        ),
    )


class DataPackageFile(Base):
    """One file inside an imported package, with the digest that was verified.

    The column mapping is stored because it is the interpretation: the same
    bytes read with ``close`` mapped to an adjusted-close column produce a
    different history, and without the mapping nobody can tell afterwards which
    reading happened.
    """

    __tablename__ = "data_package_files"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("data_packages.id", ondelete="CASCADE"), nullable=False
    )
    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_rows: Mapped[int | None] = mapped_column(BigInteger)
    observed_rows: Mapped[int | None] = mapped_column(BigInteger)
    timeframe: Mapped[str | None] = mapped_column(String(8))
    column_map: Mapped[dict[str, object] | None] = mapped_column(JSONB_OR_JSON)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("package_id", "path", name="uq_package_file"),
        Index("ix_package_file_dataset", "package_id", "dataset"),
        Index("ix_package_file_digest", "sha256"),
    )


class ImportCorrection(Base):
    """Every deviation from the source text, with the rule that made it.

    Normalization is allowed to change things — strip a currency symbol,
    localise a naive timestamp — and each change is a small decision that could
    be wrong. This table is what makes "the importer changed my data" a query
    rather than an accusation: field, raw text, substituted value, rule name and
    reason, addressable back to the file and line it came from.

    High-volume by construction: a vendor that pads every number produces one
    row per cell. That is acceptable — the alternative is a pipeline whose
    changes are invisible — but the importer's report also carries per-dataset
    counts so the common case does not require reading this table at all.
    """

    __tablename__ = "import_corrections"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    package_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_packages.id", ondelete="CASCADE")
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE")
    )
    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    source_file: Mapped[str] = mapped_column(String(512), nullable=False)
    line_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_correction_package", "package_id", "dataset"),
        Index("ix_correction_rule", "rule"),
        Index("ix_correction_source", "source_file", "line_number"),
    )
