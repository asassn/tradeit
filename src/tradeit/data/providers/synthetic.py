"""Deterministic synthetic market data.

This exists so the pipeline, the schema, and eventually the backtester can be
exercised end-to-end with no vendor account, no network, and no flaky fixtures.
It is a *test instrument*, not a simulator: the price process is a seeded
geometric random walk with an optional trend and a planted breakout, chosen so
that pattern-detection code written in later phases has something with a known
answer to run against.

Every series is a pure function of ``(seed, instrument_id)``, so a failing test
reproduces exactly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator, Sequence
from decimal import Decimal

import numpy as np

from tradeit.config import get_settings
from tradeit.core.calendar import get_calendar
from tradeit.core.enums import (
    AssetClass,
    Bartimeframe,
    CorporateActionType,
    Exchange,
    FiscalPeriod,
    KnowledgeTimeSource,
)
from tradeit.core.models import (
    CorporateAction,
    EarningsEvent,
    FundamentalFact,
    Instrument,
    OhlcvBar,
    SymbolMapping,
)
from tradeit.core.money import quantize_price
from tradeit.data.provider import ProviderCapabilities

UTC = dt.UTC

_TICKERS = ["AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "FFFF", "GGGG", "HHHH"]


class SyntheticProvider:
    """Offline provider implementing all three data protocols."""

    name = "synthetic"

    def __init__(self, seed: int = 20240101, universe_size: int = 8) -> None:
        self.seed = seed
        self.universe_size = min(universe_size, len(_TICKERS))
        self._calendar = get_calendar(get_settings().data.exchange_calendar)
        self._lag = dt.timedelta(minutes=get_settings().data.daily_bar_publication_lag_minutes)

    capabilities = ProviderCapabilities(
        supplies_reported_knowledge_time=True,
        supplies_delisted_instruments=True,
        earliest_available=dt.date(2000, 1, 3),
        max_symbols_per_request=1,
    )

    # -- reference data ------------------------------------------------------

    def list_instruments(self, as_of: dt.date) -> Iterable[tuple[Instrument, SymbolMapping]]:
        for i in range(self.universe_size):
            instrument_id = i + 1
            listed = dt.date(2000, 1, 3)
            yield (
                Instrument(
                    instrument_id=instrument_id,
                    primary_exchange=Exchange.XNAS if i % 2 else Exchange.XNYS,
                    asset_class=AssetClass.COMMON_STOCK,
                    name=f"Synthetic Corp {_TICKERS[i]}",
                    first_trade_date=listed,
                ),
                SymbolMapping(instrument_id=instrument_id, ticker=_TICKERS[i], valid_from=listed),
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
            raise NotImplementedError("synthetic provider only generates daily bars")
        sessions = self._calendar.sessions_between(start, end)
        if not sessions:
            return
        closes, volumes = self._series(instrument_id, len(sessions))
        rng = np.random.default_rng(self.seed + instrument_id * 977)

        prev_close = closes[0]
        for idx, session_date in enumerate(sessions):
            close = closes[idx]
            # Build a plausible bar around the close: gap from prior close, then
            # an intraday range scaled to the day's move.
            gap = rng.normal(0.0, 0.004)
            open_ = prev_close * (1.0 + gap)
            span = abs(close - open_) + close * abs(rng.normal(0.0, 0.006))
            high = max(open_, close) + span * rng.uniform(0.1, 0.6)
            low = min(open_, close) - span * rng.uniform(0.1, 0.6)
            low = max(low, 0.01)
            vwap = (high + low + close) / 3.0

            close_utc = self._calendar.close_instant(session_date)
            yield OhlcvBar(
                instrument_id=instrument_id,
                timeframe=Bartimeframe.D1,
                session_date=session_date,
                event_time=close_utc,
                knowledge_time=close_utc + self._lag,
                knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                open=quantize_price(open_),
                high=quantize_price(high),
                low=quantize_price(low),
                close=quantize_price(close),
                volume=Decimal(int(volumes[idx])),
                vwap=quantize_price(vwap),
            )
            prev_close = close

    def _series(self, instrument_id: int, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Seeded price and volume paths with a planted breakout.

        The breakout is placed at 70% of the window: a base of roughly 25
        sessions with compressed volatility, then an expansion on 3x volume.
        Later phases can assert that the detector finds it and, just as
        importantly, that it does not fire on the flat stretch before it.
        """
        rng = np.random.default_rng(self.seed + instrument_id)
        drift = rng.uniform(-0.0002, 0.0006)
        vol = rng.uniform(0.010, 0.028)

        shocks = rng.normal(drift, vol, size=n)
        base_volume = rng.uniform(4e5, 8e6)
        volumes = rng.lognormal(mean=np.log(base_volume), sigma=0.35, size=n)

        if n > 60:
            pivot = int(n * 0.7)
            base_start = max(0, pivot - 25)
            shocks[base_start:pivot] *= 0.35  # volatility contraction
            volumes[base_start:pivot] *= 0.7
            shocks[pivot] = abs(shocks[pivot]) + 0.06  # the expansion day
            volumes[pivot] *= 3.2
            shocks[pivot + 1 : pivot + 6] = np.abs(shocks[pivot + 1 : pivot + 6]) * 0.8

        start_price = rng.uniform(8.0, 240.0)
        closes = start_price * np.exp(np.cumsum(shocks))
        return closes, np.maximum(volumes.astype(np.int64), 100)

    def fetch_corporate_actions(
        self, instrument_id: int, start: dt.date, end: dt.date
    ) -> Iterable[CorporateAction]:
        """One deterministic 2-for-1 split per instrument, announced 21 days ahead."""
        rng = np.random.default_rng(self.seed + instrument_id * 31)
        if rng.uniform() > 0.4:
            return
        sessions = self._calendar.sessions_between(start, end)
        if len(sessions) < 40:
            return
        ex_date = sessions[int(len(sessions) * 0.45)]
        announced = ex_date - dt.timedelta(days=21)
        yield CorporateAction(
            instrument_id=instrument_id,
            action_type=CorporateActionType.SPLIT,
            ex_date=ex_date,
            event_time=dt.datetime.combine(ex_date, dt.time(13, 30), tzinfo=UTC),
            knowledge_time=dt.datetime.combine(announced, dt.time(21, 0), tzinfo=UTC),
            knowledge_source=KnowledgeTimeSource.SYNTHETIC,
            ratio=Decimal(2),
        )

    # -- fundamentals --------------------------------------------------------

    def fetch_fundamentals(
        self, instrument_id: int, metrics: Sequence[str], start: dt.date, end: dt.date
    ) -> Iterable[FundamentalFact]:
        rng = np.random.default_rng(self.seed + instrument_id * 7919)
        for metric in metrics:
            level = rng.uniform(5e7, 5e9)
            growth = rng.uniform(-0.02, 0.09)
            for idx, (year, period, period_end) in enumerate(_quarters(start, end)):
                # 40 days from period end to filing: late enough to be realistic,
                # and always stamped so the knowledge_time is genuine.
                filed = dt.datetime.combine(
                    period_end + dt.timedelta(days=40), dt.time(21, 5), tzinfo=UTC
                )
                value = level * ((1 + growth) ** idx) * (1 + rng.normal(0, 0.05))
                yield FundamentalFact(
                    instrument_id=instrument_id,
                    metric=metric,
                    fiscal_period=period,
                    fiscal_year=year,
                    period_end=period_end,
                    event_time=dt.datetime.combine(period_end, dt.time(21, 0), tzinfo=UTC),
                    knowledge_time=filed,
                    knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                    value=quantize_price(value),
                )

    def fetch_earnings(
        self, instrument_id: int, start: dt.date, end: dt.date
    ) -> Iterable[EarningsEvent]:
        for year, period, period_end in _quarters(start, end):
            scheduled = period_end + dt.timedelta(days=40)
            announced = scheduled - dt.timedelta(days=18)
            yield EarningsEvent(
                instrument_id=instrument_id,
                scheduled_date=scheduled,
                session_hint="after_close",
                fiscal_period=period,
                fiscal_year=year,
                event_time=dt.datetime.combine(scheduled, dt.time(21, 0), tzinfo=UTC),
                knowledge_time=dt.datetime.combine(announced, dt.time(13, 0), tzinfo=UTC),
                knowledge_source=KnowledgeTimeSource.SYNTHETIC,
                is_confirmed=True,
            )


def _quarters(start: dt.date, end: dt.date) -> Iterator[tuple[int, FiscalPeriod, dt.date]]:
    """Calendar-aligned fiscal quarters whose period end falls in the window."""
    ends = {
        1: (FiscalPeriod.Q1, dt.date(1, 3, 31)),
        2: (FiscalPeriod.Q2, dt.date(1, 6, 30)),
        3: (FiscalPeriod.Q3, dt.date(1, 9, 30)),
        4: (FiscalPeriod.Q4, dt.date(1, 12, 31)),
    }
    for year in range(start.year, end.year + 1):
        for quarter in (1, 2, 3, 4):
            period, template = ends[quarter]
            period_end = dt.date(year, template.month, template.day)
            if start <= period_end <= end:
                yield year, period, period_end
