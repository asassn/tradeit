"""Domain models for the data layer.

Phase 1 defines the *facts* the platform reasons over: what an instrument is,
what it traded at, what its financials said, and -- critically -- when each of
those things became knowable. Signals, opportunities, positions and portfolio
state are deliberately absent; they belong to the phases that implement them,
and inventing their shape now would only guarantee a rewrite.

Two invariants are enforced here rather than left to convention:

1. Every fact carries a ``knowledge_time``. There is no constructor path that
   produces a fact without one (see :class:`BitemporalFact`).
2. A fact cannot be knowable before it happened. ``knowledge_time >=
   event_time`` is validated, which catches the single most common ingestion
   bug: stamping a filing with its fiscal period end.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradeit.core.enums import (
    AssetClass,
    Bartimeframe,
    CorporateActionType,
    DataQualityFlag,
    Exchange,
    FiscalPeriod,
    KnowledgeTimeSource,
    ListingStatus,
)
from tradeit.core.money import quantize_price, quantize_qty
from tradeit.errors import DataError

UTC = dt.UTC

Ticker = Annotated[str, Field(min_length=1, max_length=16, pattern=r"^[A-Z0-9.\-]+$")]
NonNegDecimal = Annotated[Decimal, Field(ge=0)]
PosDecimal = Annotated[Decimal, Field(gt=0)]


class Frozen(BaseModel):
    """Base for immutable value objects.

    Facts are immutable by construction. A revised fundamental is a *new* row
    with a later ``knowledge_time``, never a mutation of the old one -- that is
    what makes "what did we believe last March?" answerable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)


def _require_utc(value: dt.datetime, field: str) -> dt.datetime:
    if value.tzinfo is None:
        raise DataError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class BitemporalFact(Frozen):
    """Anything the platform learns, stamped with when it happened and when we
    could have known it.

    ``event_time``
        When the fact refers to -- a bar's close, a filing's period end, a
        split's ex-date.
    ``knowledge_time``
        The earliest instant a decision-maker could have acted on it. For a
        daily bar that is the close plus the vendor's publication lag; for an
        annual report it is the filing timestamp, which may be months after the
        period end.
    ``knowledge_source``
        How ``knowledge_time`` was obtained. Anything other than
        :attr:`KnowledgeTimeSource.REPORTED` means a backtest built on this row
        is making an assumption, and reports must say so.
    """

    #: Set by the handful of fact types where an announcement legitimately
    #: precedes the event it describes (corporate actions, scheduled earnings).
    allow_prescient_knowledge: ClassVar[bool] = False

    event_time: dt.datetime
    knowledge_time: dt.datetime
    knowledge_source: KnowledgeTimeSource

    @model_validator(mode="after")
    def _validate_times(self) -> Self:
        event = _require_utc(self.event_time, "event_time")
        known = _require_utc(self.knowledge_time, "knowledge_time")
        if known < event and not self.allow_prescient_knowledge:
            raise DataError(
                f"knowledge_time {known.isoformat()} precedes event_time {event.isoformat()}: "
                "a fact cannot be known before it occurs"
            )
        return self

    def is_visible_at(self, as_of: dt.datetime) -> bool:
        return self.knowledge_time <= _require_utc(as_of, "as_of")


class Instrument(Frozen):
    """A tradable security, identified by a surrogate key rather than a ticker.

    Tickers are recycled. When a company is acquired its symbol is often
    reassigned within months, and a screen keyed on the string ``"XYZ"`` will
    happily splice two unrelated price histories together. ``instrument_id`` is
    permanent; the ticker is an attribute that changes over time and lives in
    :class:`SymbolMapping`.
    """

    instrument_id: int
    primary_exchange: Exchange
    asset_class: AssetClass
    name: str = Field(min_length=1, max_length=256)
    country: str = Field(default="US", min_length=2, max_length=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    first_trade_date: dt.date | None = None
    listing_status: ListingStatus = ListingStatus.ACTIVE
    delisted_date: dt.date | None = None
    figi: str | None = Field(default=None, max_length=12)
    cik: str | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> Self:
        if self.listing_status is ListingStatus.ACTIVE and self.delisted_date is not None:
            raise DataError("an active instrument cannot carry a delisted_date")
        if self.listing_status is not ListingStatus.ACTIVE and self.delisted_date is None:
            raise DataError(f"{self.listing_status} instrument requires a delisted_date")
        if (
            self.first_trade_date
            and self.delisted_date
            and self.delisted_date < self.first_trade_date
        ):
            raise DataError("delisted_date precedes first_trade_date")
        return self


class SymbolMapping(Frozen):
    """Ticker-to-instrument binding over a half-open date interval.

    ``valid_from`` inclusive, ``valid_to`` exclusive; ``None`` means "still
    current". Resolving a ticker always requires a date.
    """

    instrument_id: int
    ticker: Ticker
    valid_from: dt.date
    valid_to: dt.date | None = None

    @model_validator(mode="after")
    def _validate_interval(self) -> Self:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise DataError(f"empty validity interval for {self.ticker}")
        return self

    def covers(self, on: dt.date) -> bool:
        return self.valid_from <= on and (self.valid_to is None or on < self.valid_to)


class UniverseMembership(Frozen):
    """Membership of an instrument in a named universe over a date interval.

    This is the survivorship-bias control. A screen run as of 2015 must see the
    companies that were listed in 2015, including the ones that later went to
    zero. Storing membership intervals -- rather than filtering today's active
    list -- is what makes that possible.
    """

    universe: str = Field(min_length=1, max_length=64)
    instrument_id: int
    valid_from: dt.date
    valid_to: dt.date | None = None
    exit_reason: ListingStatus | None = None

    @model_validator(mode="after")
    def _validate_interval(self) -> Self:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise DataError(f"empty membership interval in universe {self.universe!r}")
        if self.valid_to is None and self.exit_reason is not None:
            raise DataError("open-ended membership cannot have an exit_reason")
        return self

    def covers(self, on: dt.date) -> bool:
        return self.valid_from <= on and (self.valid_to is None or on < self.valid_to)


class OhlcvBar(BitemporalFact):
    """A single price bar as the exchange printed it -- unadjusted.

    Adjusted prices are *not* stored. Today's adjusted history for a stock that
    split last week differs from the history you could have seen before the
    split, so persisting adjusted values bakes future information into the
    past. Raw prices plus a point-in-time :class:`CorporateAction` series
    reconstruct any adjustment as of any date. See ADR-0005.

    ``event_time`` is the bar's close instant. ``session_date`` is the exchange-
    local trading date, which is the key humans and calendars use and which does
    not always equal the UTC date of the close.
    """

    instrument_id: int
    timeframe: Bartimeframe
    session_date: dt.date
    open: PosDecimal
    high: PosDecimal
    low: PosDecimal
    close: PosDecimal
    volume: NonNegDecimal
    trade_count: int | None = Field(default=None, ge=0)
    vwap: PosDecimal | None = None
    quality: DataQualityFlag = DataQualityFlag.OK

    @model_validator(mode="after")
    def _validate_ohlc(self) -> Self:
        if self.high < max(self.open, self.close, self.low):
            raise DataError(
                f"high {self.high} below open/close/low on {self.session_date} "
                f"(instrument {self.instrument_id})"
            )
        if self.low > min(self.open, self.close, self.high):
            raise DataError(
                f"low {self.low} above open/close/high on {self.session_date} "
                f"(instrument {self.instrument_id})"
            )
        if self.vwap is not None and not (self.low <= self.vwap <= self.high):
            raise DataError(f"vwap {self.vwap} outside [{self.low}, {self.high}]")
        return self

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    @property
    def dollar_volume(self) -> Decimal:
        """Turnover proxy used by liquidity filters.

        Uses VWAP when the vendor supplies it, otherwise the typical price.
        Close-times-volume systematically misstates turnover on trend days.
        """
        reference = self.vwap if self.vwap is not None else (self.high + self.low + self.close) / 3
        return quantize_price(reference) * quantize_qty(self.volume)


class CorporateAction(BitemporalFact):
    """A split, dividend, or other event that changes the price series' meaning.

    ``ratio`` is the multiplier applied to the *share count*: a 2-for-1 split is
    ``2``, a 1-for-10 reverse split is ``0.1``. Prices before the ex-date are
    divided by it. ``cash_amount`` is per pre-action share.

    ``event_time`` is the ex-date open -- the first instant the price series
    reflects the action. ``knowledge_time`` is the announcement, which is
    normally *earlier* than the ex-date. That ordering is legitimate, so this
    is one of the fact types that opts out of the knowledge-after-event rule.
    """

    allow_prescient_knowledge: ClassVar[bool] = True

    instrument_id: int
    action_type: CorporateActionType
    ex_date: dt.date
    ratio: PosDecimal = Decimal(1)
    cash_amount: NonNegDecimal = Decimal(0)
    new_ticker: Ticker | None = None

    @model_validator(mode="after")
    def _validate_action(self) -> Self:
        if self.action_type is CorporateActionType.SPLIT and self.ratio == Decimal(1):
            raise DataError("a split with ratio 1 is not a split")
        if self.action_type is CorporateActionType.CASH_DIVIDEND and self.cash_amount == Decimal(0):
            raise DataError("a cash dividend requires a non-zero cash_amount")
        if self.action_type is CorporateActionType.SYMBOL_CHANGE and self.new_ticker is None:
            raise DataError("a symbol change requires new_ticker")
        return self


class FundamentalFact(BitemporalFact):
    """One reported financial metric for one fiscal period, as filed.

    Modelled as narrow rows (one metric per row) rather than a wide financial
    statement because vendors disagree on which line items exist, restatements
    arrive per-line, and a sparse wide table with two hundred mostly-null
    columns is worse to migrate than it is to query.

    ``restatement_of`` links a revision to the row it supersedes, so the audit
    trail survives.
    """

    instrument_id: int
    metric: str = Field(min_length=1, max_length=64)
    fiscal_period: FiscalPeriod
    fiscal_year: int = Field(ge=1900, le=2200)
    period_end: dt.date
    value: Decimal | None
    unit: str = Field(default="USD", max_length=16)
    restatement_of: int | None = None


class EarningsEvent(BitemporalFact):
    """A scheduled or reported earnings date.

    Both a landmine (do not enter the day before a print) and a catalyst, so it
    is a first-class fact. ``is_confirmed`` distinguishes a company-confirmed
    date from a vendor estimate, because acting on an estimated date as if it
    were confirmed is how positions get held through surprises.

    A scheduled date is announced before it arrives, so like corporate actions
    this fact type is allowed to be known ahead of its ``event_time``. The
    announcement instant is still the gate: a screen run in January cannot see
    an April date that was only published in March.
    """

    allow_prescient_knowledge: ClassVar[bool] = True

    instrument_id: int
    scheduled_date: dt.date
    session_hint: str | None = Field(default=None, max_length=16)
    fiscal_period: FiscalPeriod
    fiscal_year: int = Field(ge=1900, le=2200)
    is_confirmed: bool = False
    eps_actual: Decimal | None = None
    eps_estimate: Decimal | None = None

    @property
    def has_reported(self) -> bool:
        return self.eps_actual is not None


class NewsItem(BitemporalFact):
    """A headline, stamped with when it was actually published.

    ``event_time`` and ``knowledge_time`` are usually identical for news -- the
    event *is* the publication. They stay separate fields because syndicated and
    re-published items legitimately differ, and because a vendor backfilling an
    archive will set ``knowledge_time`` to its own ingestion instant, which we
    need to be able to see rather than have silently merged into one column.

    ``sentiment`` is nullable and carries ``sentiment_model_version``: a
    sentiment score is a model output, not an observation, and scores recomputed
    with a newer model are a different fact. Without the version, backtests
    silently use tomorrow's model on yesterday's news.
    """

    instrument_id: int | None
    headline: str = Field(min_length=1, max_length=512)
    url: str | None = Field(default=None, max_length=1024)
    publisher: str | None = Field(default=None, max_length=128)
    sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)
    sentiment_model_version: str | None = Field(default=None, max_length=64)
    external_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _validate_sentiment_provenance(self) -> Self:
        if self.sentiment is not None and self.sentiment_model_version is None:
            raise DataError(
                "a sentiment score without a model version cannot be reproduced; "
                "record which model produced it"
            )
        return self


class MacroObservation(BitemporalFact):
    """One value of an economic series, as originally released.

    Macro data is revised more aggressively than anything else the platform
    consumes: employment figures are revised twice, GDP three times, and vendor
    APIs typically serve only the current vintage. ``event_time`` is the end of
    the reference period; ``knowledge_time`` is the release instant.
    ``vintage`` distinguishes the first print from subsequent revisions of the
    same period, so a regime model can be backtested on what was actually known.
    """

    series_id: str = Field(min_length=1, max_length=64)
    period_end: dt.date
    value: Decimal | None
    unit: str = Field(default="index", max_length=24)
    vintage: int = Field(default=1, ge=1)
