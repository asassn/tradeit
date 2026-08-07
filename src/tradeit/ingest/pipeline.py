"""Ingestion: provider facts in, audited rows out.

Two behaviours here are load-bearing and easy to get wrong later:

* **Nothing is dropped silently.** A row that fails validation goes to
  ``quarantined_rows`` with its raw payload and the reason. Gaps in a price
  series must always be explainable.
* **Nothing is updated in place.** Re-running an ingest for a window that was
  already loaded inserts revisions, and the repository layer resolves which
  revision a given clock sees. Idempotency comes from the uniqueness constraint
  on ``(key..., knowledge_time)``, so replaying identical data is a no-op while
  genuinely revised data is preserved as history.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Insert

from tradeit.core.clock import utcnow
from tradeit.core.enums import Bartimeframe
from tradeit.core.models import (
    CorporateAction,
    EarningsEvent,
    FundamentalFact,
    OhlcvBar,
)
from tradeit.errors import DataError
from tradeit.storage import tables

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class IngestResult:
    dataset: str
    provider: str
    rows_written: int = 0
    rows_rejected: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rows_rejected += 1
        key = reason.split(":")[0][:80]
        self.reasons[key] = self.reasons.get(key, 0) + 1

    @property
    def ok(self) -> bool:
        return self.rows_rejected == 0


class Ingestor:
    """Writes validated facts, quarantining what it cannot validate."""

    def __init__(self, session: Session, provider_name: str, code_version: str = "0.1.0") -> None:
        self.session = session
        self.provider_name = provider_name
        self.code_version = code_version

    def _open_run(
        self, dataset: str, start: dt.date | None, end: dt.date | None
    ) -> tables.IngestionRun:
        run = tables.IngestionRun(
            provider=self.provider_name,
            dataset=dataset,
            requested_from=start,
            requested_to=end,
            started_at=utcnow(),
            status="running",
            code_version=self.code_version,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def _close_run(self, run: tables.IngestionRun, result: IngestResult) -> None:
        run.finished_at = utcnow()
        run.rows_written = result.rows_written
        run.rows_rejected = result.rows_rejected
        run.status = "completed" if result.ok else "completed_with_rejections"
        self.session.flush()

    def _quarantine(
        self,
        run: tables.IngestionRun,
        dataset: str,
        identifier: str | None,
        reason: str,
        payload: Any,
    ) -> None:
        self.session.add(
            tables.QuarantinedRow(
                ingestion_run_id=run.id,
                dataset=dataset,
                identifier=identifier,
                reason=reason[:2000],
                payload=json.dumps(payload, default=str)[:8000],
            )
        )

    # -- datasets ------------------------------------------------------------

    def ingest_bars(
        self,
        bars: Iterable[OhlcvBar],
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        timeframe: Bartimeframe = Bartimeframe.D1,
    ) -> IngestResult:
        result = IngestResult(dataset="ohlcv_bars", provider=self.provider_name)
        run = self._open_run("ohlcv_bars", start, end)
        rows: list[dict[str, Any]] = []

        for bar in bars:
            try:
                rows.append(self._bar_row(bar, timeframe))
            except (DataError, ValueError) as exc:
                result.reject(str(exc))
                self._quarantine(
                    run, "ohlcv_bars", str(getattr(bar, "instrument_id", None)), str(exc), bar
                )

        result.rows_written = self._upsert(tables.OhlcvBar, rows)
        self._close_run(run, result)
        log.info(
            "ingest.bars",
            provider=self.provider_name,
            written=result.rows_written,
            rejected=result.rows_rejected,
        )
        return result

    def _bar_row(self, bar: OhlcvBar, timeframe: Bartimeframe) -> dict[str, Any]:
        if bar.timeframe is not timeframe:
            raise DataError(f"bar timeframe {bar.timeframe} does not match requested {timeframe}")
        return {
            "instrument_id": bar.instrument_id,
            "timeframe": bar.timeframe.value,
            "session_date": bar.session_date,
            "event_time": bar.event_time,
            "knowledge_time": bar.knowledge_time,
            "knowledge_source": bar.knowledge_source.value,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "trade_count": bar.trade_count,
            "vwap": bar.vwap,
            "quality": bar.quality.value,
            "source": self.provider_name,
        }

    def ingest_corporate_actions(self, actions: Iterable[CorporateAction]) -> IngestResult:
        result = IngestResult(dataset="corporate_actions", provider=self.provider_name)
        run = self._open_run("corporate_actions", None, None)
        rows = [
            {
                "instrument_id": a.instrument_id,
                "action_type": a.action_type.value,
                "ex_date": a.ex_date,
                "event_time": a.event_time,
                "knowledge_time": a.knowledge_time,
                "knowledge_source": a.knowledge_source.value,
                "ratio": a.ratio,
                "cash_amount": a.cash_amount,
                "new_ticker": a.new_ticker,
                "source": self.provider_name,
            }
            for a in actions
        ]
        result.rows_written = self._upsert(tables.CorporateAction, rows)
        self._close_run(run, result)
        return result

    def ingest_fundamentals(self, facts: Iterable[FundamentalFact]) -> IngestResult:
        result = IngestResult(dataset="fundamental_facts", provider=self.provider_name)
        run = self._open_run("fundamental_facts", None, None)
        rows: list[dict[str, Any]] = []
        for fact in facts:
            if fact.knowledge_time <= fact.event_time:
                # Zero filing lag means the vendor almost certainly gave us the
                # period end rather than a publication timestamp. Accepting it
                # would let a screen trade on results before they were filed.
                reason = "knowledge_time equals event_time: suspected period-end-as-filing-date"
                result.reject(reason)
                self._quarantine(
                    run, "fundamental_facts", str(fact.instrument_id), reason, fact.model_dump()
                )
                continue
            rows.append(
                {
                    "instrument_id": fact.instrument_id,
                    "metric": fact.metric,
                    "fiscal_period": fact.fiscal_period.value,
                    "fiscal_year": fact.fiscal_year,
                    "period_end": fact.period_end,
                    "event_time": fact.event_time,
                    "knowledge_time": fact.knowledge_time,
                    "knowledge_source": fact.knowledge_source.value,
                    "value": fact.value,
                    "unit": fact.unit,
                    "restatement_of": fact.restatement_of,
                    "source": self.provider_name,
                }
            )
        result.rows_written = self._upsert(tables.FundamentalFact, rows)
        self._close_run(run, result)
        return result

    def ingest_earnings(self, events: Iterable[EarningsEvent]) -> IngestResult:
        result = IngestResult(dataset="earnings_events", provider=self.provider_name)
        run = self._open_run("earnings_events", None, None)
        rows = [
            {
                "instrument_id": e.instrument_id,
                "scheduled_date": e.scheduled_date,
                "session_hint": e.session_hint,
                "fiscal_period": e.fiscal_period.value,
                "fiscal_year": e.fiscal_year,
                "event_time": e.event_time,
                "knowledge_time": e.knowledge_time,
                "knowledge_source": e.knowledge_source.value,
                "is_confirmed": e.is_confirmed,
                "eps_actual": e.eps_actual,
                "eps_estimate": e.eps_estimate,
                "source": self.provider_name,
            }
            for e in events
        ]
        result.rows_written = self._upsert(tables.EarningsEvent, rows)
        self._close_run(run, result)
        return result

    # -- write path ----------------------------------------------------------

    def _upsert(self, table: type[tables.Base], rows: list[dict[str, Any]]) -> int:
        """Insert facts, ignoring exact replays. Returns rows actually written.

        ``ON CONFLICT DO NOTHING`` against the ``(key..., knowledge_time)``
        uniqueness constraint makes re-running a window idempotent without
        overwriting anything -- a revision has a different knowledge_time and so
        does not conflict at all.

        The count comes from ``RETURNING`` rather than ``rowcount`` because
        psycopg reports ``-1`` for multi-row inserts, and an ingestion audit
        trail that under-reports what it wrote is worse than no audit trail.
        """
        if not rows:
            return 0

        dialect = self.session.get_bind().dialect.name
        stmt: Insert
        if dialect == "postgresql":
            stmt = pg_insert(table).values(rows).on_conflict_do_nothing()
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(table).values(rows).on_conflict_do_nothing()
        else:
            raise DataError(f"ingestion does not support the {dialect!r} dialect")

        pk = next(iter(table.__table__.primary_key))
        return len(self.session.execute(stmt.returning(pk)).scalars().all())
