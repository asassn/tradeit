"""Stooq adapter — free daily history, with limitations stated up front.

Stooq serves decades of daily OHLCV as a CSV with no account, no key and no
rate-limit paperwork, which makes it the fastest way to put *real* market data
in front of this system. It is included for exactly that reason.

It is **not backtest-grade**, and the adapter says so rather than leaving it to
be discovered:

**The prices are adjusted.** Stooq applies split and dividend adjustment to
history, so a price in a 2005 bar reflects corporate actions that had not
happened in 2005. Under ADR-0005 that disqualifies the feed for anything whose
result depends on a price *level* — a $5 minimum price, a round-number
resistance level, a dollar-volume floor. The adjusted series remains perfectly
good for anything defined on *returns*: momentum, relative strength, realised
volatility, correlation.

**There are no corporate actions.** ``fetch_corporate_actions`` returns nothing
and is honest about why. Because the prices arrive pre-adjusted, the system
cannot recover the actions from them, and cannot undo the adjustment either.

**There are no delisted securities.** A universe built from Stooq contains the
survivors, which is the textbook definition of survivorship bias.

Use it to exercise the pipeline on genuine price behaviour — real gaps, real
holidays, real volatility clustering, real halts. Do not use it to produce a
performance number anyone is meant to believe.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation

from pathlib import Path

import structlog

from tradeit.config import get_settings
from tradeit.core.calendar import get_calendar
from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.core.models import CorporateAction, OhlcvBar
from tradeit.data.provider import ProviderCapabilities
from tradeit.data.providers.http import HttpTransport, ResponseCache
from tradeit.errors import DataError, ProviderError

log = structlog.get_logger(__name__)

BASE_URL = "https://stooq.com/q/d/l/"


class StooqProvider:
    """Free adjusted daily bars. Implements ``MarketDataProvider``."""

    name = "stooq"

    capabilities = ProviderCapabilities(
        supplies_reported_knowledge_time=False,
        supplies_delisted_instruments=False,
        supplies_unadjusted_prices=False,
        supplies_restatements=False,
        earliest_available=dt.date(1990, 1, 2),
        max_symbols_per_request=1,
        rate_limit_per_minute=30,
    )

    def __init__(
        self,
        symbols: Mapping[int, str],
        *,
        cache_dir: str | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self.symbols = dict(symbols)
        settings = get_settings()
        self._calendar = get_calendar(settings.data.exchange_calendar)
        self._lag = dt.timedelta(minutes=settings.data.daily_bar_publication_lag_minutes)

        if transport is not None:
            self.transport = transport
        else:
            root = cache_dir or str(settings.data_dir / "vendor_cache" / "stooq")
            self.transport = HttpTransport(
                cache=ResponseCache(root=Path(root)),
                min_interval_s=60.0 / (self.capabilities.rate_limit_per_minute or 30),
            )

    def _ticker(self, instrument_id: int) -> str:
        try:
            ticker = self.symbols[instrument_id]
        except KeyError:
            raise ProviderError(
                f"instrument {instrument_id} has no Stooq symbol in this adapter's map"
            ) from None
        # Stooq namespaces US listings with a .us suffix; without it the symbol
        # resolves to a different exchange's instrument of the same name.
        return ticker.lower() if "." in ticker else f"{ticker.lower()}.us"

    def fetch_bars(
        self,
        instrument_id: int,
        start: dt.date,
        end: dt.date,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> Iterable[OhlcvBar]:
        if timeframe is not Bartimeframe.D1:
            raise ProviderError(f"Stooq serves daily bars only, not {timeframe}")

        url = (
            f"{BASE_URL}?s={self._ticker(instrument_id)}&i=d"
            f"&d1={start:%Y%m%d}&d2={end:%Y%m%d}"
        )
        body = self.transport.get(url).decode("utf-8", errors="replace")
        if body.strip().lower().startswith("no data"):
            raise ProviderError(
                f"Stooq has no data for {self._ticker(instrument_id)}; the symbol is "
                "unknown to them or the security is delisted, which this feed drops"
            )

        for row in csv.DictReader(io.StringIO(body)):
            bar = self._bar(instrument_id, row)
            if bar is not None:
                yield bar

    def _bar(self, instrument_id: int, row: dict[str, str]) -> OhlcvBar | None:
        raw_date = row.get("Date")
        if not raw_date:
            return None
        session = dt.date.fromisoformat(raw_date)
        if not self._calendar.is_session(session):
            log.warning("stooq.non_session_bar", instrument_id=instrument_id, session=session)
            return None

        close_time = self._calendar.close_instant(session)
        try:
            return OhlcvBar(
                instrument_id=instrument_id,
                timeframe=Bartimeframe.D1,
                session_date=session,
                event_time=close_time,
                knowledge_time=close_time + self._lag,
                knowledge_source=KnowledgeTimeSource.ESTIMATED,
                open=Decimal(row["Open"]),
                high=Decimal(row["High"]),
                low=Decimal(row["Low"]),
                close=Decimal(row["Close"]),
                # Stooq omits volume for some indices and older sessions. Zero
                # is a real value here (a halted or untraded session), so it is
                # stored rather than rejected; the quality diagnostics decide
                # whether a run of zeros is suspicious.
                volume=Decimal(row.get("Volume") or 0),
            )
        except (KeyError, InvalidOperation) as exc:
            raise DataError(
                f"Stooq row for instrument {instrument_id} on {session} is unusable: {exc}"
            ) from exc

    def fetch_corporate_actions(
        self, instrument_id: int, start: dt.date, end: dt.date
    ) -> Iterable[CorporateAction]:
        """Always empty, and deliberately not an exception.

        Returning nothing lets the ingestion pipeline run unchanged, which is
        the point of a vendor-neutral interface. The reason it is empty lives in
        :meth:`limitations` and in the capabilities recorded on every ingestion
        run, so it cannot be mistaken for "this security had no splits".
        """
        return ()

    def limitations(self) -> list[str]:
        return [
            *self.capabilities.caveats(),
            "prices are split- and dividend-adjusted at source and cannot be "
            "un-adjusted: any rule that depends on a price level (minimum price, "
            "round-number levels, dollar-volume floors) is measuring an adjusted "
            "number, not one that could have been observed.",
            "returns-based features (momentum, relative strength, realised "
            "volatility) are unaffected by the adjustment and are usable.",
            "no corporate action feed at all: fetch_corporate_actions returns "
            "nothing, which is a gap in coverage rather than an absence of events.",
        ]
