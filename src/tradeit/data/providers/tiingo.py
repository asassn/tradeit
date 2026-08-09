"""Tiingo adapter — the recommended provider for point-in-time backtesting.

Why this vendor, specifically, out of the ones evaluated in
``docs/VENDOR_EVALUATION.md``:

**It returns raw and adjusted prices in the same row.** ``open`` alongside
``adjOpen``, ``volume`` alongside ``adjVolume``. This is rarer than it sounds
and it is the single requirement ADR-0005 cannot compromise on: an
adjusted-only feed encodes future splits into past prices, so a "price" in it
is not a number anyone could have seen, and every price-based threshold in a
backtest silently tests a counterfactual.

**Each row carries ``divCash`` and ``splitFactor``.** The corporate actions are
in the price file, dated to the ex-date, so the action series and the price
series cannot disagree with each other — they are the same download.

**Delisted tickers are included**, with an ``endDate`` that stops when the
company did. Survivorship control needs the companies that went to zero, and
most cheap feeds quietly drop them.

**Thirty-plus years of history**, which is enough to cover 2000, 2008 and 2020
— the three periods where a regime model earns or loses credibility.

What it does *not* give, stated here because the pipeline records it:

**No per-bar publication timestamp.** Nothing says when a given day's bar
became queryable. We derive ``knowledge_time`` from the session close plus the
configured publication lag and stamp it ``ESTIMATED``, so
``backtest_grade`` is ``False`` and the caveat travels with every result. That
is the honest reading: the *rule* is ours, not the vendor's.

**No historical index constituents.** Tiingo tells you a ticker existed and
when it stopped; it does not tell you it was in the S&P 500 in 2011.
Survivorship bias in a *universe* built from it is therefore reduced, not
solved, and :meth:`TiingoProvider.limitations` says so in as many words.

Requires an API key (``TRADEIT_TIINGO_TOKEN``). The free tier covers end-of-day
prices for a limited number of unique symbols per hour; the paid tier at the
time of writing lifts that. Nothing in this adapter depends on which tier is in
use beyond the rate limit.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from pathlib import Path

import structlog

from tradeit.config import get_settings
from tradeit.core.calendar import get_calendar
from tradeit.core.enums import (
    AssetClass,
    Bartimeframe,
    CorporateActionType,
    Exchange,
    KnowledgeTimeSource,
    ListingStatus,
)
from tradeit.core.models import CorporateAction, Instrument, OhlcvBar, SymbolMapping
from tradeit.data.provider import ProviderCapabilities
from tradeit.data.providers.http import HttpTransport, ProviderAuthError, ResponseCache
from tradeit.errors import DataError, ProviderError

log = structlog.get_logger(__name__)

BASE_URL = "https://api.tiingo.com/tiingo/daily"

#: Tiingo exchange codes seen on US equities, mapped to our MIC-based enum.
_EXCHANGES = {
    "NYSE": Exchange.XNYS,
    "NYSE ARCA": Exchange.ARCX,
    "NYSE MKT": Exchange.XASE,
    "AMEX": Exchange.XASE,
    "NASDAQ": Exchange.XNAS,
    "BATS": Exchange.BATS,
}


class TiingoProvider:
    """End-of-day US equity prices and the corporate actions embedded in them.

    Implements :class:`~tradeit.data.provider.MarketDataProvider` and
    :class:`~tradeit.data.provider.ReferenceDataProvider`.

    ``symbols`` maps our permanent ``instrument_id`` to the vendor's ticker. The
    indirection is deliberate: instrument identity is ours and survives a ticker
    change, and an adapter that keyed on tickers would quietly follow the ticker
    to whichever company holds it now.
    """

    name = "tiingo"

    capabilities = ProviderCapabilities(
        # Tiingo does not stamp a per-bar publication instant, so ours is a
        # rule. Declaring this True would be the exact shortcut the provider
        # contract warns about.
        supplies_reported_knowledge_time=False,
        supplies_delisted_instruments=True,
        supplies_unadjusted_prices=True,
        supplies_restatements=False,
        earliest_available=dt.date(1962, 1, 2),
        max_symbols_per_request=1,
        rate_limit_per_minute=50,
    )

    def __init__(
        self,
        symbols: Mapping[int, str],
        *,
        token: str | None = None,
        cache_dir: str | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self.symbols = dict(symbols)
        self._token = token or os.environ.get("TRADEIT_TIINGO_TOKEN", "")
        settings = get_settings()
        self._calendar = get_calendar(settings.data.exchange_calendar)
        self._lag = dt.timedelta(minutes=settings.data.daily_bar_publication_lag_minutes)

        if transport is not None:
            self.transport = transport
        else:
            root = cache_dir or str(settings.data_dir / "vendor_cache" / "tiingo")
            self.transport = HttpTransport(
                cache=ResponseCache(root=Path(root)),
                min_interval_s=60.0 / (self.capabilities.rate_limit_per_minute or 50),
            )

    # -- plumbing ------------------------------------------------------------

    def _ticker(self, instrument_id: int) -> str:
        try:
            return self.symbols[instrument_id]
        except KeyError:
            raise ProviderError(
                f"instrument {instrument_id} has no Tiingo ticker in this adapter's "
                "symbol map; identity resolution is the caller's job, not the vendor's"
            ) from None

    def _get(self, path: str, **params: str) -> Any:
        if not self._token:
            raise ProviderAuthError(
                "no Tiingo token: set TRADEIT_TIINGO_TOKEN or pass token=. "
                "Refusing to issue an unauthenticated request that would return a "
                "403 and look like a network problem."
            )
        query = "&".join(f"{k}={v}" for k, v in {**params, "format": "json"}.items())
        url = f"{BASE_URL}/{path}?{query}"
        # The token goes in a header rather than the query string so it stays
        # out of cache sidecars, logs and exception messages.
        body = self.transport.get(url, headers={"Authorization": f"Token {self._token}"})
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Tiingo returned non-JSON for {path}: {body[:200]!r}") from exc

    # -- reference data ------------------------------------------------------

    def list_instruments(self, as_of: dt.date) -> Iterable[tuple[Instrument, SymbolMapping]]:
        """Metadata for the configured symbols, as of a date.

        Filters on the vendor's ``startDate``/``endDate``, so a symbol that had
        not listed by ``as_of`` is absent and one that had already delisted is
        marked ``DELISTED`` rather than dropped — the delisted ones are the
        whole point of survivorship control.
        """
        for instrument_id, ticker in sorted(self.symbols.items(), key=lambda kv: kv[0]):
            meta = self._get(ticker)
            first = _date(meta.get("startDate"))
            last = _date(meta.get("endDate"))
            if first is None or first > as_of:
                continue

            delisted = last is not None and last < as_of
            yield (
                Instrument(
                    instrument_id=instrument_id,
                    primary_exchange=_EXCHANGES.get(
                        str(meta.get("exchangeCode", "")).upper(), Exchange.OTHER
                    ),
                    asset_class=AssetClass.COMMON_STOCK,
                    name=str(meta.get("name") or ticker),
                    first_trade_date=first,
                    listing_status=ListingStatus.DELISTED if delisted else ListingStatus.ACTIVE,
                    delisted_date=last if delisted else None,
                ),
                SymbolMapping(
                    instrument_id=instrument_id,
                    ticker=ticker.upper(),
                    valid_from=first,
                    valid_to=last if delisted else None,
                ),
            )

    # -- prices --------------------------------------------------------------

    def fetch_bars(
        self,
        instrument_id: int,
        start: dt.date,
        end: dt.date,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> Iterable[OhlcvBar]:
        if timeframe is not Bartimeframe.D1:
            raise ProviderError(
                f"the Tiingo EOD endpoint serves daily bars only, not {timeframe}; "
                "intraday requires their IEX endpoint and a different entitlement"
            )
        ticker = self._ticker(instrument_id)
        rows = self._get(
            f"{ticker}/prices", startDate=start.isoformat(), endDate=end.isoformat()
        )
        for row in rows:
            bar = self._bar(instrument_id, row)
            if bar is not None:
                yield bar

    def _bar(self, instrument_id: int, row: dict[str, Any]) -> OhlcvBar | None:
        session = _date(row.get("date"))
        if session is None:
            raise DataError(f"Tiingo row for instrument {instrument_id} has no date: {row!r}")

        if not self._calendar.is_session(session):
            # A bar on a date the exchange calendar says was not a session. The
            # disagreement is itself a finding -- it is either a vendor error or
            # a hole in our calendar -- so it is logged and surfaced to the
            # calendar validation rather than passed off as a normal bar.
            log.warning("tiingo.non_session_bar", instrument_id=instrument_id, session=session)
            return None
        close_time = self._calendar.close_instant(session)

        try:
            return OhlcvBar(
                instrument_id=instrument_id,
                timeframe=Bartimeframe.D1,
                session_date=session,
                event_time=close_time,
                knowledge_time=close_time + self._lag,
                # Ours, not the vendor's. See the module docstring.
                knowledge_source=KnowledgeTimeSource.ESTIMATED,
                open=_decimal(row["open"]),
                high=_decimal(row["high"]),
                low=_decimal(row["low"]),
                close=_decimal(row["close"]),
                volume=_decimal(row["volume"]),
            )
        except (KeyError, InvalidOperation, TypeError) as exc:
            raise DataError(
                f"Tiingo row for instrument {instrument_id} on {session} is unusable: {exc}"
            ) from exc

    # -- corporate actions ---------------------------------------------------

    def fetch_corporate_actions(
        self, instrument_id: int, start: dt.date, end: dt.date
    ) -> Iterable[CorporateAction]:
        """Splits and cash dividends, read off the price rows.

        Taken from the same download as the prices so the two cannot disagree.
        ``knowledge_time`` is the ex-date open rather than the close: a split is
        announced well in advance and is common knowledge before the session it
        applies to, so stamping it at the close would make the system act on a
        price series it could not yet interpret.
        """
        ticker = self._ticker(instrument_id)
        rows = self._get(
            f"{ticker}/prices", startDate=start.isoformat(), endDate=end.isoformat()
        )
        for row in rows:
            ex_date = _date(row.get("date"))
            if ex_date is None or not self._calendar.is_session(ex_date):
                continue
            open_time = self._calendar.session(ex_date).open_utc

            split = _decimal(row.get("splitFactor", 1))
            if split != 1:
                yield CorporateAction(
                    instrument_id=instrument_id,
                    action_type=CorporateActionType.SPLIT,
                    ex_date=ex_date,
                    ratio=split,
                    event_time=open_time,
                    knowledge_time=open_time,
                    knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
                )

            dividend = _decimal(row.get("divCash", 0))
            if dividend > 0:
                yield CorporateAction(
                    instrument_id=instrument_id,
                    action_type=CorporateActionType.CASH_DIVIDEND,
                    ex_date=ex_date,
                    cash_amount=dividend,
                    event_time=open_time,
                    knowledge_time=open_time,
                    knowledge_source=KnowledgeTimeSource.VENDOR_INGEST,
                )

    # -- honesty -------------------------------------------------------------

    def limitations(self) -> list[str]:
        """What this vendor cannot do, in the words a report should use."""
        return [
            *self.capabilities.caveats(),
            "no historical index constituents: a universe built from this feed "
            "includes delisted securities but cannot reconstruct which of them were "
            "in the S&P 500 on a given date. Survivorship bias is reduced, not solved.",
            "no per-bar publication timestamp: knowledge_time is the session close "
            f"plus {self._lag.total_seconds() / 60:.0f} minutes, applied as a rule and "
            "stamped ESTIMATED.",
            "corporate actions are derived from the price file's splitFactor and "
            "divCash columns, which covers splits and cash dividends but not "
            "spin-offs, rights issues or symbol changes.",
        ]


def _decimal(value: Any) -> Decimal:
    """Parse via ``str`` so a vendor float never becomes binary noise in a price."""
    if value is None:
        raise InvalidOperation("missing numeric value")
    return Decimal(str(value))


def _date(value: Any) -> dt.date | None:
    if not value:
        return None
    text = str(value)
    # Tiingo returns "2024-01-02T00:00:00.000Z" on prices and "2024-01-02" on
    # metadata. Both are ISO dates once the time is discarded.
    return dt.date.fromisoformat(text[:10])


def build_symbol_map(tickers: Sequence[str], *, start_id: int = 1) -> dict[int, str]:
    """Assign stable ``instrument_id`` values to a ticker list.

    Sorted so the mapping is reproducible from the ticker list alone. This is a
    convenience for validation runs; production identity comes from the
    ``instruments`` table, where an id outlives every ticker it ever wore.

    Case is normalised *before* de-duplication: ``["aapl", "AAPL"]`` is one
    security, and assigning it two ids would silently double it in every
    cross-sectional rank.
    """
    return {start_id + i: t for i, t in enumerate(sorted({t.upper() for t in tickers}))}
