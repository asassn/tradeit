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
    H4 = "4h"
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


# ---------------------------------------------------------------------------
# Phase 2 vocabularies.
#
# These name the concepts the architecture introduces. They are persisted, so
# adding a member is cheap and renaming one is a migration. Members that no
# phase implements yet are still declared here, because a vocabulary that grows
# ad hoc across five modules ends up with three spellings of "stop loss".
# ---------------------------------------------------------------------------


class MarketRegime(StrEnum):
    """Coarse classification of the market environment.

    Deliberately coarse. A regime model with fifteen states cannot be validated
    on the number of regime changes a few decades of history contains.
    """

    BULL_TRENDING = "bull_trending"
    BULL_CHOPPY = "bull_choppy"
    NEUTRAL = "neutral"
    BEAR_CHOPPY = "bear_choppy"
    BEAR_TRENDING = "bear_trending"
    HIGH_VOLATILITY = "high_volatility"
    UNKNOWN = "unknown"


class PatternType(StrEnum):
    """Chart formations the pattern engine may recognise."""

    FLAT_BASE = "flat_base"
    CUP_WITH_HANDLE = "cup_with_handle"
    DOUBLE_BOTTOM = "double_bottom"
    ASCENDING_TRIANGLE = "ascending_triangle"
    DESCENDING_TRIANGLE = "descending_triangle"
    SYMMETRICAL_TRIANGLE = "symmetrical_triangle"
    BULL_FLAG = "bull_flag"
    VOLATILITY_CONTRACTION = "volatility_contraction"
    HIGH_TIGHT_FLAG = "high_tight_flag"
    CONSOLIDATION = "consolidation"


class PatternStatus(StrEnum):
    """Lifecycle of a detected pattern.

    A pattern is not a signal. It becomes actionable only on ``TRIGGERED`` and
    stays provisional until ``CONFIRMED`` -- the distinction that separates
    buying a breakout from buying a failed breakout.
    """

    FORMING = "forming"
    COMPLETE = "complete"
    TRIGGERED = "triggered"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class BreakoutStatus(StrEnum):
    """State of a breakout attempt through a pivot level."""

    APPROACHING = "approaching"
    TRIGGERED = "triggered"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNDERCUT = "undercut"
    EXPIRED = "expired"


class SignalDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"
    SELL_SHORT = "sell_short"
    BUY_TO_COVER = "buy_to_cover"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    MARKET_ON_CLOSE = "market_on_close"
    MARKET_ON_OPEN = "market_on_open"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    OPG = "opg"
    CLS = "cls"


class OrderStatus(StrEnum):
    PENDING_NEW = "pending_new"
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    PENDING_ENTRY = "pending_entry"
    PENDING_EXIT = "pending_exit"


class ExitReason(StrEnum):
    """Why a position was closed. Drives post-trade analysis.

    Recorded per exit rather than per position, because a scaled-out position
    can leave for several reasons and averaging them away loses the lesson.
    """

    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TARGET_REACHED = "target_reached"
    TIME_STOP = "time_stop"
    EARNINGS_AVOIDANCE = "earnings_avoidance"
    SIGNAL_REVERSAL = "signal_reversal"
    REGIME_CHANGE = "regime_change"
    RISK_LIMIT = "risk_limit"
    REBALANCE = "rebalance"
    PARTIAL_PROFIT = "partial_profit"
    DISCRETIONARY = "discretionary"
    BACKTEST_END = "backtest_end"


class RiskLimitType(StrEnum):
    """Limits the risk engine may enforce."""

    PORTFOLIO_HEAT = "portfolio_heat"
    POSITION_RISK = "position_risk"
    POSITION_SIZE = "position_size"
    SECTOR_EXPOSURE = "sector_exposure"
    CORRELATION_CLUSTER = "correlation_cluster"
    MAX_POSITIONS = "max_positions"
    MAX_DRAWDOWN = "max_drawdown"
    DAILY_LOSS = "daily_loss"
    GROSS_EXPOSURE = "gross_exposure"
    LIQUIDITY_PARTICIPATION = "liquidity_participation"


class RiskDecision(StrEnum):
    """Outcome of evaluating a proposed trade against the risk rules."""

    ALLOW = "allow"
    ALLOW_REDUCED = "allow_reduced"
    REJECT = "reject"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class BacktestStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JournalEntryType(StrEnum):
    """What a trade-journal entry records."""

    ENTRY_DECISION = "entry_decision"
    ENTRY_REJECTED = "entry_rejected"
    SCALE_IN = "scale_in"
    SCALE_OUT = "scale_out"
    STOP_MOVED = "stop_moved"
    EXIT_DECISION = "exit_decision"
    POST_TRADE_REVIEW = "post_trade_review"
    RISK_OVERRIDE = "risk_override"
    NOTE = "note"


class ArtifactKind(StrEnum):
    """What a versioned artifact identifies, for reproducibility.

    Every stored decision references one of each, so replaying it means pinning
    the same four hashes rather than hoping nothing changed.
    """

    STRATEGY_CONFIG = "strategy_config"
    DATA_SNAPSHOT = "data_snapshot"
    FEATURE_SET = "feature_set"
    MODEL = "model"
