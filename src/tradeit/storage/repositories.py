"""Point-in-time read API.

Every method takes an :class:`~tradeit.core.clock.AsOfClock` as its first
argument. That is not decoration -- it is the enforcement mechanism. There is no
overload that reads "the latest" data, because the latest data is exactly what a
backtest must not see.

The recurring query shape is *latest visible revision*: among all rows for a
key whose ``knowledge_time <= as_of``, take the one with the greatest
``knowledge_time``. It is expressed with ``ROW_NUMBER()`` rather than
PostgreSQL's ``DISTINCT ON`` so the same code runs against SQLite in unit tests.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from tradeit.core import models
from tradeit.core.clock import AsOfClock
from tradeit.core.enums import (
    AdjustmentPolicy,
    Bartimeframe,
    CorporateActionType,
    DataQualityFlag,
    KnowledgeTimeSource,
)
from tradeit.errors import UniverseError
from tradeit.storage import tables


class InstrumentRepository:
    """Instrument identity and universe membership, resolved as of a date."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve_ticker(self, clock: AsOfClock, ticker: str) -> int:
        """Map a ticker to the instrument it referred to on the clock's date.

        Raises rather than returning ``None`` because a screen that quietly
        skips an unresolvable symbol produces results nobody can reconcile.
        """
        on = clock.date
        row = self.session.execute(
            select(tables.SymbolMapping.instrument_id).where(
                tables.SymbolMapping.ticker == ticker.upper(),
                tables.SymbolMapping.valid_from <= on,
                or_(
                    tables.SymbolMapping.valid_to.is_(None),
                    tables.SymbolMapping.valid_to > on,
                ),
            )
        ).first()
        if row is None:
            raise UniverseError(f"ticker {ticker!r} does not resolve to an instrument on {on}")
        return int(row[0])

    def ticker_for(self, clock: AsOfClock, instrument_id: int) -> str | None:
        """The ticker an instrument traded under on the clock's date."""
        on = clock.date
        row = self.session.execute(
            select(tables.SymbolMapping.ticker).where(
                tables.SymbolMapping.instrument_id == instrument_id,
                tables.SymbolMapping.valid_from <= on,
                or_(
                    tables.SymbolMapping.valid_to.is_(None),
                    tables.SymbolMapping.valid_to > on,
                ),
            )
        ).first()
        return str(row[0]) if row else None

    def universe(self, clock: AsOfClock, universe: str) -> list[int]:
        """Instruments in a universe on the clock's date, survivors and casualties alike."""
        on = clock.date
        rows = self.session.execute(
            select(tables.UniverseMembership.instrument_id)
            .where(
                tables.UniverseMembership.universe == universe,
                tables.UniverseMembership.valid_from <= on,
                or_(
                    tables.UniverseMembership.valid_to.is_(None),
                    tables.UniverseMembership.valid_to > on,
                ),
            )
            .order_by(tables.UniverseMembership.instrument_id)
        ).all()
        return [int(r[0]) for r in rows]

    def get(self, instrument_id: int) -> tables.Instrument | None:
        """Fetch static instrument metadata.

        Takes no clock: exchange, asset class and CIK are treated as immutable
        reference data. Anything that genuinely changes over time (ticker,
        listing status at a date) is served by an interval table instead.
        """
        return self.session.get(tables.Instrument, instrument_id)


class BarRepository:
    """Point-in-time price history."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def history(
        self,
        clock: AsOfClock,
        instrument_id: int,
        *,
        timeframe: Bartimeframe = Bartimeframe.D1,
        start: dt.date | None = None,
        limit: int | None = None,
        adjustment: AdjustmentPolicy = AdjustmentPolicy.SPLIT_ONLY,
        include_suspect: bool = False,
    ) -> list[models.OhlcvBar]:
        """Bars up to the clock, newest revision of each session, oldest first.

        ``adjustment`` is applied on read from corporate actions that were
        themselves knowable at ``clock.as_of``. A split announced tomorrow does
        not retroactively adjust today's view. See ADR-0005.
        """
        t = tables.OhlcvBar
        columns = [
            t.session_date,
            t.event_time,
            t.knowledge_time,
            t.knowledge_source,
            t.open,
            t.high,
            t.low,
            t.close,
            t.volume,
            t.trade_count,
            t.vwap,
            t.quality,
        ]
        rank = (
            func.row_number()
            .over(partition_by=t.session_date, order_by=t.knowledge_time.desc())
            .label("revision_rank")
        )
        conditions = [
            t.instrument_id == instrument_id,
            t.timeframe == timeframe.value,
            t.knowledge_time <= clock.as_of,
        ]
        if start is not None:
            conditions.append(t.session_date >= start)
        if not include_suspect:
            conditions.append(t.quality.in_([DataQualityFlag.OK, DataQualityFlag.BACKFILLED]))

        inner = select(*columns, rank).where(and_(*conditions)).subquery()
        stmt = select(inner).where(inner.c.revision_rank == 1).order_by(inner.c.session_date.desc())
        if limit is not None:
            stmt = stmt.limit(limit)

        rows = list(self.session.execute(stmt).all())
        rows.reverse()  # chronological order for downstream indicator math

        bars = [
            models.OhlcvBar(
                instrument_id=instrument_id,
                timeframe=timeframe,
                session_date=r.session_date,
                event_time=r.event_time,
                knowledge_time=r.knowledge_time,
                knowledge_source=KnowledgeTimeSource(r.knowledge_source),
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
                trade_count=r.trade_count,
                vwap=r.vwap,
                quality=DataQualityFlag(r.quality),
            )
            for r in rows
        ]
        if adjustment is AdjustmentPolicy.NONE or not bars:
            return bars
        actions = CorporateActionRepository(self.session).for_instrument(clock, instrument_id)
        return apply_adjustments(bars, actions, adjustment)

    def latest(
        self,
        clock: AsOfClock,
        instrument_id: int,
        *,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> models.OhlcvBar | None:
        """The most recent bar the clock is allowed to see."""
        bars = self.history(
            clock, instrument_id, timeframe=timeframe, limit=1, adjustment=AdjustmentPolicy.NONE
        )
        return bars[-1] if bars else None


class CorporateActionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def for_instrument(self, clock: AsOfClock, instrument_id: int) -> list[models.CorporateAction]:
        t = tables.CorporateAction
        rank = (
            func.row_number()
            .over(
                partition_by=(t.action_type, t.ex_date),
                order_by=t.knowledge_time.desc(),
            )
            .label("revision_rank")
        )
        inner = (
            select(
                t.action_type,
                t.ex_date,
                t.event_time,
                t.knowledge_time,
                t.knowledge_source,
                t.ratio,
                t.cash_amount,
                t.new_ticker,
                rank,
            )
            .where(t.instrument_id == instrument_id, t.knowledge_time <= clock.as_of)
            .subquery()
        )
        rows = self.session.execute(
            select(inner).where(inner.c.revision_rank == 1).order_by(inner.c.ex_date)
        ).all()
        return [
            models.CorporateAction(
                instrument_id=instrument_id,
                action_type=CorporateActionType(r.action_type),
                ex_date=r.ex_date,
                event_time=r.event_time,
                knowledge_time=r.knowledge_time,
                knowledge_source=KnowledgeTimeSource(r.knowledge_source),
                ratio=r.ratio,
                cash_amount=r.cash_amount,
                new_ticker=r.new_ticker,
            )
            for r in rows
        ]


class FundamentalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest_metric(
        self, clock: AsOfClock, instrument_id: int, metric: str
    ) -> models.FundamentalFact | None:
        """Most recently *filed* value of a metric visible to the clock.

        Ordered by ``knowledge_time`` and not by ``period_end``: what matters is
        what had been published, and a restatement of an older quarter can
        legitimately arrive after a newer quarter's original filing.
        """
        t = tables.FundamentalFact
        row = self.session.execute(
            select(t)
            .where(
                t.instrument_id == instrument_id,
                t.metric == metric,
                t.knowledge_time <= clock.as_of,
            )
            .order_by(t.knowledge_time.desc(), t.period_end.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return _to_fundamental(row)

    def series(
        self, clock: AsOfClock, instrument_id: int, metric: str, *, periods: int = 8
    ) -> list[models.FundamentalFact]:
        """Latest visible revision of each of the last ``periods`` fiscal periods.

        This is the series a growth screen should use: as-filed history with
        restatements applied only where they were already public.
        """
        t = tables.FundamentalFact
        rank = (
            func.row_number()
            .over(
                partition_by=(t.fiscal_year, t.fiscal_period),
                order_by=t.knowledge_time.desc(),
            )
            .label("revision_rank")
        )
        inner = (
            select(
                t.id,
                t.metric,
                t.fiscal_period,
                t.fiscal_year,
                t.period_end,
                t.event_time,
                t.knowledge_time,
                t.knowledge_source,
                t.value,
                t.unit,
                t.restatement_of,
                rank,
            )
            .where(
                t.instrument_id == instrument_id,
                t.metric == metric,
                t.knowledge_time <= clock.as_of,
            )
            .subquery()
        )
        rows = self.session.execute(
            select(inner)
            .where(inner.c.revision_rank == 1)
            .order_by(inner.c.period_end.desc())
            .limit(periods)
        ).all()
        out = [
            models.FundamentalFact(
                instrument_id=instrument_id,
                metric=r.metric,
                fiscal_period=r.fiscal_period,
                fiscal_year=r.fiscal_year,
                period_end=r.period_end,
                event_time=r.event_time,
                knowledge_time=r.knowledge_time,
                knowledge_source=KnowledgeTimeSource(r.knowledge_source),
                value=r.value,
                unit=r.unit,
                restatement_of=r.restatement_of,
            )
            for r in rows
        ]
        out.reverse()
        return out


class EarningsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def next_scheduled(
        self, clock: AsOfClock, instrument_id: int, *, horizon_days: int = 45
    ) -> models.EarningsEvent | None:
        """The next earnings date known to the clock, within a horizon.

        Used as an entry veto. Note that ``is_confirmed`` on the returned event
        should drive how much the veto is trusted -- an unconfirmed vendor
        estimate can be off by a week.
        """
        t = tables.EarningsEvent
        horizon = clock.date + dt.timedelta(days=horizon_days)
        row = self.session.execute(
            select(t)
            .where(
                t.instrument_id == instrument_id,
                t.knowledge_time <= clock.as_of,
                t.scheduled_date >= clock.date,
                t.scheduled_date <= horizon,
            )
            .order_by(t.scheduled_date.asc(), t.knowledge_time.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return models.EarningsEvent(
            instrument_id=row.instrument_id,
            scheduled_date=row.scheduled_date,
            session_hint=row.session_hint,
            fiscal_period=row.fiscal_period,
            fiscal_year=row.fiscal_year,
            event_time=row.event_time,
            knowledge_time=row.knowledge_time,
            knowledge_source=KnowledgeTimeSource(row.knowledge_source),
            is_confirmed=row.is_confirmed,
            eps_actual=row.eps_actual,
            eps_estimate=row.eps_estimate,
        )


def _to_fundamental(row: tables.FundamentalFact) -> models.FundamentalFact:
    return models.FundamentalFact(
        instrument_id=row.instrument_id,
        metric=row.metric,
        fiscal_period=row.fiscal_period,
        fiscal_year=row.fiscal_year,
        period_end=row.period_end,
        event_time=row.event_time,
        knowledge_time=row.knowledge_time,
        knowledge_source=KnowledgeTimeSource(row.knowledge_source),
        value=row.value,
        unit=row.unit,
        restatement_of=row.restatement_of,
    )


def apply_adjustments(
    bars: list[models.OhlcvBar],
    actions: list[models.CorporateAction],
    policy: AdjustmentPolicy,
) -> list[models.OhlcvBar]:
    """Back-adjust a raw bar series for corporate actions.

    Walks backwards from the newest bar, accumulating a factor each time an
    ex-date is crossed. Bars on or after an ex-date are untouched; bars before
    it are scaled. Volume is scaled inversely to splits so that dollar volume
    is preserved -- a 2-for-1 split halves the price and doubles the shares, and
    a volume series that ignores it shows a phantom breakout in turnover.

    ``actions`` must already be filtered to what the clock could see; this
    function does no visibility checking of its own.
    """
    if policy is AdjustmentPolicy.NONE or not bars or not actions:
        return bars

    relevant = [
        a
        for a in actions
        if a.action_type is CorporateActionType.SPLIT
        or (
            policy is AdjustmentPolicy.TOTAL_RETURN
            and a.action_type is CorporateActionType.CASH_DIVIDEND
        )
    ]
    if not relevant:
        return bars

    by_ex_date = sorted(relevant, key=lambda a: a.ex_date, reverse=True)
    price_factor = Decimal(1)
    volume_factor = Decimal(1)
    cursor = 0
    adjusted: list[models.OhlcvBar] = []

    for bar in reversed(bars):
        while cursor < len(by_ex_date) and by_ex_date[cursor].ex_date > bar.session_date:
            action = by_ex_date[cursor]
            if action.action_type is CorporateActionType.SPLIT:
                price_factor /= action.ratio
                volume_factor *= action.ratio
            else:
                # Dividend adjustment needs a reference price; the close of the
                # bar preceding the ex-date is the standard choice.
                reference = bar.close * price_factor
                if reference > 0:
                    price_factor *= (reference - action.cash_amount) / reference
            cursor += 1

        if price_factor == Decimal(1) and volume_factor == Decimal(1):
            adjusted.append(bar)
            continue

        adjusted.append(
            bar.model_copy(
                update={
                    "open": bar.open * price_factor,
                    "high": bar.high * price_factor,
                    "low": bar.low * price_factor,
                    "close": bar.close * price_factor,
                    "vwap": bar.vwap * price_factor if bar.vwap is not None else None,
                    "volume": bar.volume * volume_factor,
                }
            )
        )

    adjusted.reverse()
    return adjusted
