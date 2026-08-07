"""Controlled vocabularies shared across the whole platform.

These enums are part of the persisted schema. Renaming a member is a migration,
not a refactor -- values are written to the database and to trade journals.
"""

from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    """How far a generated order is allowed to travel.

    ``LIVE`` exists so that code paths can be written and tested, but the
    safety interlock in :mod:`tradeit.config` refuses to construct a live
    settings object unless an explicit authorization file is present. See
    ADR-0004.
    """

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Exchange(StrEnum):
    XNYS = "XNYS"  # New York Stock Exchange
    XNAS = "XNAS"  # Nasdaq
    ARCX = "ARCX"  # NYSE Arca
    BATS = "BATS"  # Cboe BZX
    XASE = "XASE"  # NYSE American
    OTHER = "OTHER"


class AssetClass(StrEnum):
    COMMON_STOCK = "common_stock"
    ADR = "adr"
    ETF = "etf"
    REIT = "reit"
    CLOSED_END_FUND = "closed_end_fund"
    UNIT = "unit"
    WARRANT = "warrant"
    PREFERRED = "preferred"
    OTHER = "other"


class ListingStatus(StrEnum):
    ACTIVE = "active"
    DELISTED = "delisted"
    ACQUIRED = "acquired"
    MERGED = "merged"
    BANKRUPT = "bankrupt"
    SUSPENDED = "suspended"


class Bartimeframe(StrEnum):
    """Bar aggregation periods the platform understands."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1mo"


class CorporateActionType(StrEnum):
    SPLIT = "split"
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SPINOFF = "spinoff"
    RIGHTS_ISSUE = "rights_issue"
    SYMBOL_CHANGE = "symbol_change"


class AdjustmentPolicy(StrEnum):
    """How raw prices are transformed on read. See ADR-0005.

    ``NONE`` returns exactly what the exchange printed. ``SPLIT_ONLY`` is the
    default for technical analysis: it removes the discontinuities that are
    pure share-count arithmetic while leaving dividend gaps intact, because a
    dividend gap is a real price the market traded through. ``TOTAL_RETURN``
    also back-adjusts dividends and is intended for performance attribution,
    not for pattern detection.
    """

    NONE = "none"
    SPLIT_ONLY = "split_only"
    TOTAL_RETURN = "total_return"


class KnowledgeTimeSource(StrEnum):
    """Provenance of a fact's ``knowledge_time``.

    Backtests are only as honest as this field. ``REPORTED`` means the vendor
    gave us an actual publication timestamp. ``ESTIMATED`` means we derived it
    from a rule (e.g. "assume filed 45 days after period end") and results that
    depend on it must be treated as optimistic. See ADR-0002.
    """

    REPORTED = "reported"
    VENDOR_INGEST = "vendor_ingest"
    ESTIMATED = "estimated"
    SYNTHETIC = "synthetic"


class FiscalPeriod(StrEnum):
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    FY = "FY"
    TTM = "TTM"


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class DataQualityFlag(StrEnum):
    """Non-fatal problems detected during ingestion.

    Rows carrying flags are stored, not dropped -- silently discarding bad bars
    creates gaps that look like holidays. Downstream consumers decide.
    """

    OK = "ok"
    SUSPECT_ZERO_VOLUME = "suspect_zero_volume"
    SUSPECT_PRICE_SPIKE = "suspect_price_spike"
    INCONSISTENT_OHLC = "inconsistent_ohlc"
    STALE_REPEAT = "stale_repeat"
    BACKFILLED = "backfilled"
    VENDOR_REVISION = "vendor_revision"
