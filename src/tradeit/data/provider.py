"""Vendor-neutral data provider contracts.

Providers are ``Protocol`` classes rather than a base class so that an adapter
can satisfy one without inheriting from us -- and, more importantly, so that a
provider that only supplies prices is not forced to stub out fundamentals.

The contract every implementation must honour:

1. **Return facts, not frames.** Providers emit validated domain objects. Every
   ad-hoc DataFrame that escapes an adapter becomes a column-naming argument
   six months later.
2. **Populate ``knowledge_time`` honestly.** If the vendor supplies a
   publication timestamp, use it and mark ``REPORTED``. If you are applying a
   rule of thumb, mark ``ESTIMATED``. Never stamp a filing with its period end
   and call it reported -- that single shortcut is worth several points of
   fictitious annual return in a backtest.
3. **Never look at the clock.** Providers fetch what was asked for. Visibility
   filtering happens at the repository layer, after ingestion, so that the same
   stored row can serve both a 2019 backtest and today's screen.
4. **Declare limitations rather than hiding them.** ``ProviderCapabilities``
   is not documentation; it is recorded on every ingestion run and surfaced in
   backtest reports.

The one interface that breaks rule 3 is :class:`BrokerProvider`, which reports
live account state rather than historical facts. It is a different kind of thing
and is documented as such below.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradeit.core.enums import Bartimeframe, OrderStatus
from tradeit.core.models import (
    CorporateAction,
    EarningsEvent,
    FundamentalFact,
    Instrument,
    MacroObservation,
    NewsItem,
    OhlcvBar,
    SymbolMapping,
)


@runtime_checkable
class ReferenceDataProvider(Protocol):
    """Instrument identity: what exists, under which ticker, when.

    ``list_instruments`` takes a date because the answer changes: companies
    list, delist, and hand their tickers to other companies. A provider that
    ignores the date and returns today's actives is survivorship-unsafe and must
    say so in its capabilities.
    """

    name: str

    def list_instruments(self, as_of: dt.date) -> Iterable[tuple[Instrument, SymbolMapping]]: ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """Raw, unadjusted OHLCV bars and the corporate actions that explain them.

    The two belong together because a price series without its actions is
    uninterpretable: a 50% overnight drop is either a catastrophe or a 2-for-1
    split, and only the action series distinguishes them.
    """

    name: str

    def fetch_bars(
        self,
        instrument_id: int,
        start: dt.date,
        end: dt.date,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> Iterable[OhlcvBar]: ...

    def fetch_corporate_actions(
        self, instrument_id: int, start: dt.date, end: dt.date
    ) -> Iterable[CorporateAction]: ...


#: Phase 1 name, kept so existing adapters and tests keep working. New code
#: should use ``MarketDataProvider``.
PriceProvider = MarketDataProvider


@runtime_checkable
class FundamentalDataProvider(Protocol):
    """As-filed financial statement facts.

    ``metrics`` is an explicit list rather than "everything" because vendors
    charge per field and because a screen that silently starts depending on a
    new metric is a screen whose history cannot be reproduced.
    """

    name: str

    def fetch_fundamentals(
        self, instrument_id: int, metrics: Sequence[str], start: dt.date, end: dt.date
    ) -> Iterable[FundamentalFact]: ...


@runtime_checkable
class EarningsProvider(Protocol):
    """Scheduled and reported earnings dates.

    Split from :class:`FundamentalDataProvider` because the two are commonly
    sold separately, and because earnings *dates* are needed for risk management
    even when no fundamental data is licensed at all.
    """

    name: str

    def fetch_earnings(
        self, instrument_id: int, start: dt.date, end: dt.date
    ) -> Iterable[EarningsEvent]: ...


@runtime_checkable
class NewsProvider(Protocol):
    """Headlines and their publication timestamps.

    News is the most leak-prone dataset in the system. Vendors routinely
    backfill archives with the timestamp of *their* ingestion rather than the
    original publication, and revise sentiment scores after the fact using
    models trained on later data. A news adapter that cannot supply a genuine
    publication instant must report ``supplies_reported_knowledge_time=False``,
    and anything built on it is not backtest-grade.

    No phase currently consumes news. The interface is defined now so that the
    ingestion and storage design accommodates it rather than being retrofitted.
    """

    name: str

    def fetch_news(
        self, instrument_id: int, start: dt.datetime, end: dt.datetime
    ) -> Iterable[NewsItem]: ...


@runtime_checkable
class MacroDataProvider(Protocol):
    """Economic series: rates, inflation, employment, credit spreads.

    Macro data has the worst revision behaviour of any dataset here. An
    employment number is revised twice, a GDP print three times, and the
    original release is often unrecoverable from a vendor's current API. That is
    exactly why observations are bitemporal: ``event_time`` is the reference
    period, ``knowledge_time`` is the release. Backtesting a regime model on
    revised macro data produces a model that could not have existed.
    """

    name: str

    def fetch_series(
        self, series_id: str, start: dt.date, end: dt.date
    ) -> Iterable[MacroObservation]: ...

    def list_series(self) -> Iterable[str]: ...


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class BrokerAccount(Protocol):
    """Point-in-time account state as the broker reports it."""

    account_id: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    maintenance_margin: Decimal
    as_of: dt.datetime


class BrokerPosition(Protocol):
    """A position as the broker reports it, for reconciliation."""

    instrument_id: int | None
    symbol: str
    quantity: Decimal
    average_price: Decimal
    market_value: Decimal


class BrokerOrderStatus(Protocol):
    """Broker-side order state."""

    broker_order_id: str
    client_order_id: str
    status: OrderStatus
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    updated_at: dt.datetime


@runtime_checkable
class BrokerProvider(Protocol):
    """Order placement and account state.

    Unlike the data providers, a broker adapter deals in *current* state, not
    historical facts, so it does not participate in the bitemporal model. What
    it must do instead:

    * **Refuse to act in the wrong mode.** Every implementation checks
      ``settings.is_live`` itself rather than trusting its caller (ADR-0004).
      A paper adapter that can reach a live endpoint is a bug, not a feature.
    * **Accept a client-generated idempotency key.** ``client_order_id`` is
      generated by us and must make a retried submission a no-op rather than a
      second position. Network timeouts during order submission are normal, and
      "did that order go through?" must be answerable without guessing.
    * **Report state, not intent.** ``get_order`` returns what the broker
      believes, which is the only thing that reconciles against a statement.

    The paper broker (Phase 8) and any live adapter implement the same
    interface, so the execution layer above cannot tell them apart -- which is
    what makes paper trading evidence about live behaviour.
    """

    name: str
    supports_fractional_shares: bool
    supports_extended_hours: bool

    def get_account(self) -> BrokerAccount: ...

    def list_positions(self) -> Iterable[BrokerPosition]: ...

    def submit_order(self, order: object, client_order_id: str) -> BrokerOrderStatus: ...

    def cancel_order(self, broker_order_id: str) -> BrokerOrderStatus: ...

    def get_order(self, broker_order_id: str) -> BrokerOrderStatus: ...

    def is_market_open(self) -> bool: ...


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class ProviderCapabilities:
    """Declared limits of an adapter, used to fail loudly instead of quietly.

    A provider that cannot supply real filing timestamps should say so here. The
    ingestion pipeline records it on the run, and backtest reports surface it,
    so nobody has to remember which vendor was in use eight months ago.
    """

    def __init__(
        self,
        *,
        supplies_reported_knowledge_time: bool,
        supplies_delisted_instruments: bool,
        earliest_available: dt.date,
        max_symbols_per_request: int = 1,
        supplies_unadjusted_prices: bool = True,
        supplies_restatements: bool = False,
        rate_limit_per_minute: int | None = None,
    ) -> None:
        self.supplies_reported_knowledge_time = supplies_reported_knowledge_time
        self.supplies_delisted_instruments = supplies_delisted_instruments
        self.earliest_available = earliest_available
        self.max_symbols_per_request = max_symbols_per_request
        self.supplies_unadjusted_prices = supplies_unadjusted_prices
        self.supplies_restatements = supplies_restatements
        self.rate_limit_per_minute = rate_limit_per_minute

    def describe(self) -> dict[str, object]:
        return {
            "supplies_reported_knowledge_time": self.supplies_reported_knowledge_time,
            "supplies_delisted_instruments": self.supplies_delisted_instruments,
            "supplies_unadjusted_prices": self.supplies_unadjusted_prices,
            "supplies_restatements": self.supplies_restatements,
            "earliest_available": self.earliest_available.isoformat(),
            "max_symbols_per_request": self.max_symbols_per_request,
            "rate_limit_per_minute": self.rate_limit_per_minute,
        }

    @property
    def survivorship_safe(self) -> bool:
        return self.supplies_delisted_instruments

    @property
    def backtest_grade(self) -> bool:
        """Whether results from this provider can be trusted without caveats.

        Adjusted-only prices disqualify a provider (ADR-0005): the adjustment
        encodes future splits, so the series is not what anyone could have seen.
        """
        return (
            self.supplies_reported_knowledge_time
            and self.supplies_delisted_instruments
            and self.supplies_unadjusted_prices
        )

    def caveats(self) -> list[str]:
        """Human-readable reasons this provider's data is not backtest-grade.

        Rendered into backtest reports so the limitation travels with the
        result instead of living in someone's memory.
        """
        out: list[str] = []
        if not self.supplies_reported_knowledge_time:
            out.append(
                "no reported publication timestamps: knowledge_time is estimated, "
                "so results assume a filing lag that may be wrong in either direction"
            )
        if not self.supplies_delisted_instruments:
            out.append(
                "no delisted instruments: the universe is survivorship-biased and "
                "returns are overstated by an unknown amount"
            )
        if not self.supplies_unadjusted_prices:
            out.append(
                "adjusted prices only: the series encodes future corporate actions "
                "and is not what could have been observed at the time"
            )
        if not self.supplies_restatements:
            out.append(
                "no restatement history: fundamentals reflect the latest revision, "
                "not what had been filed at the decision date"
            )
        return out
