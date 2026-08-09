"""Writing an imported package into the platform schema.

This is the only module that knows both halves — the pipeline's stage objects
and the physical tables — and it is deliberately the last thing in the chain, so
that everything upstream can be exercised with no database at all.

Three things it is careful about.

**It writes the package row first.** A run that dies halfway still leaves a
``data_packages`` row saying which package was being imported and that it did
not finish, which is more useful than an empty table and a memory of an error
message. The counts are updated at the end by :meth:`DatabaseSink.finalise`.

**One ``ingestion_runs`` row per dataset.** The existing audit table is keyed by
dataset and already carries rows-written and rows-rejected, so a package import
of five datasets appears as five ingestion runs sharing a package. This makes a
package import queryable by the same tools as any other ingestion rather than
being a special case nobody's dashboard knows about.

**Unmapped datasets do not silently vanish.** A package containing a dataset
this sink has no table for — ``FILINGS`` today — is counted and reported rather
than dropped. The row still went through every pipeline stage and still landed
in the report; what it did not do is acquire a home in the schema, and saying so
is the difference between "not implemented" and "lost".
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from tradeit.core.enums import Bartimeframe, CorporateActionType, DataQualityFlag
from tradeit.data.packages.importer import ImportReport
from tradeit.data.packages.manifest import PackageManifest
from tradeit.data.packages.spec import DatasetKind
from tradeit.data.packages.stages import Correction, PointInTimeRecord, QuarantineEntry, RawRecord
from tradeit.storage import tables as t

#: Rows flushed to the session at a time. Large enough that a million-row import
#: is not a million round trips, small enough that a failure does not lose an
#: afternoon of work.
DEFAULT_BATCH = 2_000


@dataclass(slots=True)
class DatabaseSink:
    """Persists dated records into the platform schema.

    Not a context manager on purpose: :meth:`finalise` takes the import report,
    which the caller only holds after the run completes, so the closing step is
    an explicit call rather than something ``__exit__`` could do.
    """

    session: Session
    manifest: PackageManifest
    source_path: Path | None = None
    code_version: str | None = None
    batch_size: int = DEFAULT_BATCH

    package: t.DataPackage = field(init=False)
    _runs: dict[DatasetKind, t.IngestionRun] = field(default_factory=dict, init=False)
    _pending: int = field(default=0, init=False)
    unmapped: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.package = t.DataPackage(
            snapshot_id=f"pending-{self.manifest.digest()[:16]}",
            name=self.manifest.name,
            provider=self.manifest.provider,
            format_version=self.manifest.format_version,
            manifest_digest=self.manifest.digest(),
            export_date=self.manifest.export_date,
            coverage_start=self.manifest.coverage.start,
            coverage_end=self.manifest.coverage.end,
            timezone=self.manifest.timezone,
            adjustment_policy=str(self.manifest.adjustment_policy),
            source_path=str(self.source_path) if self.source_path else None,
            licence_note=self.manifest.licence_note or None,
            vendor_dataset=self.manifest.vendor_dataset or None,
            known_limitations={"items": list(self.manifest.known_limitations)},
            code_version=self.code_version,
        )
        self.session.add(self.package)
        self.session.flush()

        for item in self.manifest.files:
            self.session.add(
                t.DataPackageFile(
                    package_id=self.package.id,
                    dataset=str(item.dataset),
                    path=item.path,
                    sha256=item.sha256,
                    declared_rows=item.rows,
                    timeframe=str(item.timeframe) if item.timeframe else None,
                    column_map=dict(item.columns),
                    note=item.note or None,
                )
            )
        self.session.flush()

    # -- audit rows ----------------------------------------------------------

    def _run_for(self, dataset: DatasetKind) -> t.IngestionRun:
        run = self._runs.get(dataset)
        if run is None:
            run = t.IngestionRun(
                provider=self.manifest.provider,
                dataset=str(dataset),
                requested_from=self.manifest.coverage.start,
                requested_to=self.manifest.coverage.end,
                started_at=dt.datetime.now(dt.UTC),
                status="running",
                code_version=self.code_version,
            )
            self.session.add(run)
            self.session.flush()
            self._runs[dataset] = run
        return run

    # -- RecordSink ----------------------------------------------------------

    def write(self, record: PointInTimeRecord) -> None:
        run = self._run_for(record.dataset)
        builder = _BUILDERS.get(record.dataset)
        if builder is None:
            key = str(record.dataset)
            self.unmapped[key] = self.unmapped.get(key, 0) + 1
            return
        row = builder(record, self.manifest.provider)
        if row is None:
            key = str(record.dataset)
            self.unmapped[key] = self.unmapped.get(key, 0) + 1
            return
        self.session.add(row)
        run.rows_written += 1
        self._tick()

    def quarantine(self, entry: QuarantineEntry) -> None:
        run = self._run_for(entry.dataset)
        self.session.add(
            t.QuarantinedRow(
                ingestion_run_id=run.id,
                package_id=self.package.id,
                dataset=str(entry.dataset),
                identifier=entry.identifier,
                stage=str(entry.stage),
                source_file=entry.source_file,
                line_number=entry.line_number,
                reason=entry.reason,
                payload=entry.payload,
            )
        )
        run.rows_rejected += 1
        self._tick()

    def correction(self, record: RawRecord, correction: Correction) -> None:
        run = self._run_for(record.dataset)
        self.session.add(
            t.ImportCorrection(
                package_id=self.package.id,
                ingestion_run_id=run.id,
                dataset=str(record.dataset),
                source_file=record.source_file,
                line_number=record.line_number,
                field=correction.field,
                raw_value=correction.raw,
                corrected_value=correction.corrected,
                rule=correction.rule,
                reason=correction.reason,
            )
        )
        self._tick()

    def flush(self) -> None:
        self.session.flush()
        self._pending = 0

    def _tick(self) -> None:
        self._pending += 1
        if self._pending >= self.batch_size:
            self.flush()

    # -- completion ----------------------------------------------------------

    def finalise(self, report: ImportReport) -> t.DataPackage:
        """Stamp the package row with the outcome and close the ingestion runs.

        Returns the package row so the caller can cite ``snapshot_id``. The
        snapshot id is only assigned here, because it is derived in part from
        the observed row counts and those are not known until the run ends.
        """
        self.flush()
        self.package.snapshot_id = report.snapshot_id
        self.package.observed_start = report.observed_start
        self.package.observed_end = report.observed_end
        self.package.rows_read = report.rows_read
        self.package.rows_imported = report.rows_written
        self.package.rows_quarantined = report.rows_quarantined
        self.package.rows_estimated_knowledge_time = report.rows_estimated
        self.package.digests_verified = report.digests_verified
        self.package.partial = report.partial
        self.package.aborted = report.aborted
        payload = report.to_payload()
        if self.unmapped:
            payload["unmapped_datasets"] = dict(sorted(self.unmapped.items()))
            report.notes.append(
                "datasets with no table in this schema were validated and dated but "
                f"not persisted: {dict(sorted(self.unmapped.items()))}"
            )
        self.package.report = payload

        finished = dt.datetime.now(dt.UTC)
        for dataset, run in self._runs.items():
            run.finished_at = finished
            outcome = report.outcomes.get(dataset)
            run.status = "failed" if report.aborted else "succeeded"
            if report.aborted:
                run.error = report.abort_reason
            if outcome is not None:
                run.rows_written = outcome.rows_point_in_time
                run.rows_rejected = outcome.rows_quarantined
        self.session.flush()
        return self.package


# ---------------------------------------------------------------------------
# Dataset -> ORM row.
#
# Each builder is deliberately dumb: the values it reads have already been
# coerced, validated and dated, so its only job is to put them in the right
# columns. Anything that needs a decision was decided upstream, where the
# decision could be quarantined instead of raised.
# ---------------------------------------------------------------------------


def _bar(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    timeframe = values.get("timeframe") or Bartimeframe.D1
    flags = [f for f in record.validated.flags if f is not DataQualityFlag.OK]
    return t.OhlcvBar(
        instrument_id=values["instrument_id"],
        timeframe=str(timeframe),
        session_date=values["session_date"],
        event_time=record.knowledge_time,
        knowledge_time=record.knowledge_time,
        knowledge_source=str(record.knowledge_source),
        open=values["open"],
        high=values["high"],
        low=values["low"],
        close=values["close"],
        volume=values.get("volume") or Decimal(0),
        trade_count=values.get("trade_count"),
        vwap=values.get("vwap"),
        quality=str(flags[0]) if flags else str(DataQualityFlag.OK),
        source=source,
    )


def _instrument(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    status = (values.get("listing_status") or "active").lower()
    delisted = values.get("delisted_date")
    # The schema's lifecycle constraint says an inactive instrument has a
    # delisting date and an active one does not. A vendor that supplies a
    # delisted_date but leaves listing_status as "active" would violate it, and
    # the date is the stronger evidence of the two.
    if delisted is not None and status == "active":
        status = "delisted"
    return t.Instrument(
        instrument_id=values["instrument_id"],
        primary_exchange=str(values["primary_exchange"])[:8],
        asset_class=values["asset_class"],
        name=values["name"],
        country=values.get("country") or "US",
        currency=values.get("currency") or "USD",
        first_trade_date=values.get("first_trade_date"),
        listing_status=status,
        delisted_date=delisted,
        figi=values.get("figi"),
        cik=values.get("cik"),
        source=source,
    )


def _symbol_mapping(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    return t.SymbolMapping(
        instrument_id=values["instrument_id"],
        ticker=values["ticker"],
        valid_from=values["valid_from"],
        valid_to=values.get("valid_to"),
        source=source,
    )


def _universe(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    return t.UniverseMembership(
        universe=values["universe"],
        instrument_id=values["instrument_id"],
        valid_from=values["valid_from"],
        valid_to=values.get("valid_to"),
        exit_reason=values.get("exit_reason"),
        source=source,
    )


def _sector(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    return t.Sector(
        instrument_id=values["instrument_id"],
        scheme=values.get("scheme") or "vendor",
        sector=values["sector"],
        industry=values.get("industry"),
        valid_from=values["valid_from"],
        valid_to=values.get("valid_to"),
        source=source,
    )


def _split(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    return t.CorporateAction(
        instrument_id=values["instrument_id"],
        action_type=str(CorporateActionType.SPLIT),
        ex_date=values["ex_date"],
        event_time=record.knowledge_time,
        knowledge_time=record.knowledge_time,
        knowledge_source=str(record.knowledge_source),
        ratio=values["ratio"],
        cash_amount=Decimal(0),
        source=source,
    )


def _dividend(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    # The DIVIDENDS dataset carries a cash amount per share, so it maps to
    # CASH_DIVIDEND. A stock dividend changes the share count and belongs in
    # SPLITS or CORPORATE_ACTIONS, where a ratio can express it.
    return t.CorporateAction(
        instrument_id=values["instrument_id"],
        action_type=str(CorporateActionType.CASH_DIVIDEND),
        ex_date=values["ex_date"],
        event_time=record.knowledge_time,
        knowledge_time=record.knowledge_time,
        knowledge_source=str(record.knowledge_source),
        ratio=Decimal(1),
        cash_amount=values["amount"],
        source=source,
    )


def _corporate_action(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    return t.CorporateAction(
        instrument_id=values["instrument_id"],
        action_type=values["action_type"],
        ex_date=values["ex_date"],
        event_time=record.knowledge_time,
        knowledge_time=record.knowledge_time,
        knowledge_source=str(record.knowledge_source),
        ratio=values.get("ratio") or Decimal(1),
        cash_amount=values.get("cash_amount") or Decimal(0),
        new_ticker=values.get("new_ticker"),
        source=source,
    )


def _fundamental(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    return t.FundamentalFact(
        instrument_id=values["instrument_id"],
        metric=values["metric"],
        fiscal_period=str(values["fiscal_period"])[:4],
        fiscal_year=values["fiscal_year"],
        period_end=values["period_end"],
        # event_time is when the period closed; knowledge_time is when anyone
        # could act on it. Keeping them distinct is the entire point of the
        # bitemporal schema, and the check constraint enforces the ordering.
        event_time=_end_of(values["period_end"], record.knowledge_time),
        knowledge_time=record.knowledge_time,
        knowledge_source=str(record.knowledge_source),
        value=values.get("value"),
        unit=values.get("unit") or "USD",
        source=source,
    )


def _earnings(record: PointInTimeRecord, source: str) -> Any:
    values = record.values
    return t.EarningsEvent(
        instrument_id=values["instrument_id"],
        scheduled_date=values["scheduled_date"],
        session_hint=values.get("session_hint"),
        fiscal_period=str(values["fiscal_period"])[:4],
        fiscal_year=values["fiscal_year"],
        event_time=record.knowledge_time,
        knowledge_time=record.knowledge_time,
        knowledge_source=str(record.knowledge_source),
        is_confirmed=bool(values.get("is_confirmed")),
        eps_actual=values.get("eps_actual"),
        eps_estimate=values.get("eps_estimate"),
        source=source,
    )


def _end_of(day: dt.date, ceiling: dt.datetime) -> dt.datetime:
    """Midnight UTC at the end of ``day``, never later than ``ceiling``.

    The schema requires ``knowledge_time >= event_time``. A fiscal period that
    ended at 23:59 UTC and a filing timestamp earlier that same day would
    violate it — rare, but real for a period end that falls on a filing date —
    so the event time is clamped rather than allowed to break the constraint.
    """
    candidate = dt.datetime.combine(day, dt.time(23, 59), tzinfo=dt.UTC)
    return min(candidate, ceiling)


_BUILDERS = {
    DatasetKind.DAILY_BARS: _bar,
    DatasetKind.INTRADAY_BARS: _bar,
    DatasetKind.INSTRUMENTS: _instrument,
    DatasetKind.SYMBOL_MAPPINGS: _symbol_mapping,
    DatasetKind.UNIVERSE_MEMBERSHIP: _universe,
    DatasetKind.SECTORS: _sector,
    DatasetKind.SPLITS: _split,
    DatasetKind.DIVIDENDS: _dividend,
    DatasetKind.CORPORATE_ACTIONS: _corporate_action,
    DatasetKind.FUNDAMENTALS: _fundamental,
    DatasetKind.EARNINGS: _earnings,
}

#: Datasets the pipeline handles end to end but this schema has no home for.
#: Named rather than silently absent, so "not implemented" cannot be mistaken
#: for "imported".
UNMAPPED_DATASETS: frozenset[DatasetKind] = frozenset(
    kind for kind in DatasetKind if kind not in _BUILDERS
)


__all__ = ["DEFAULT_BATCH", "UNMAPPED_DATASETS", "DatabaseSink"]
