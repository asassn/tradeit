"""Importing a package into the schema, and what the schema then knows.

The pipeline tests prove the rows are read correctly. These prove the
provenance survives the write: that a validation result can be traced back to a
package, a package back to its files, and a file back to the SHA-256 that was
verified before anything was read.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradeit.core.enums import KnowledgeTimeSource
from tradeit.data.packages.database import UNMAPPED_DATASETS, DatabaseSink
from tradeit.data.packages.importer import ImportOptions, PackageImporter
from tradeit.data.packages.manifest import Coverage, DatasetFile, PackageManifest, file_digest
from tradeit.data.packages.pointintime import KnowledgeTimePolicy
from tradeit.data.packages.spec import AdjustmentPolicyDeclaration, DatasetKind
from tradeit.storage import tables as t


def payload(value: object) -> dict[str, Any]:
    """Narrow a JSON column to a mapping for assertions."""
    assert isinstance(value, dict)
    return value


BAR_COLUMNS = {
    "session_date": "Date",
    "instrument_id": "Sym",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Vol",
}


@pytest.fixture
def package(tmp_path: Path) -> Path:
    rows = [
        f"{dt.date(2020, 1, 2) + dt.timedelta(days=i)},1,10.00,11.00,9.50,10.50,1000000"
        for i in range(6)
    ]
    rows.append("2020-01-09,1,10.00,8.00,9.50,10.50,1000000")  # high below low
    rows.append('2020-01-10,1,$10.00,11.00,9.50,10.50,"1,000,000"')  # corrections
    (tmp_path / "bars.csv").write_text(
        "\n".join(["Date,Sym,Open,High,Low,Close,Vol", *rows]) + "\n", encoding="utf-8"
    )
    (tmp_path / "instruments.csv").write_text(
        "instrument_id,name,primary_exchange,asset_class\n1,Test Co,XNYS,common_stock\n",
        encoding="utf-8",
    )
    return tmp_path


def manifest_for(root: Path) -> PackageManifest:
    return PackageManifest(
        name="storage-test",
        provider="fixture",
        export_date=dt.date(2020, 3, 1),
        coverage=Coverage(start=dt.date(2020, 1, 2), end=dt.date(2020, 1, 10)),
        timezone="America/New_York",
        adjustment_policy=AdjustmentPolicyDeclaration.RAW_UNADJUSTED,
        known_limitations=("no delisted securities before 2010",),
        files=(
            DatasetFile(
                path="instruments.csv",
                dataset=DatasetKind.INSTRUMENTS,
                sha256=file_digest(root / "instruments.csv"),
            ),
            DatasetFile(
                path="bars.csv",
                dataset=DatasetKind.DAILY_BARS,
                sha256=file_digest(root / "bars.csv"),
                columns=BAR_COLUMNS,
            ),
        ),
    )


def import_into(session: Session, root: Path, **kwargs: object) -> tuple[t.DataPackage, object]:
    manifest = manifest_for(root)
    sink = DatabaseSink(session=session, manifest=manifest, source_path=root)
    options = kwargs.pop("options", None) or ImportOptions()
    report = PackageImporter(manifest, root, options=options, sink=sink).run()  # type: ignore[arg-type]
    package = sink.finalise(report)
    session.commit()
    return package, report


class TestDatabaseSink:
    def test_bars_land_in_the_bar_table(self, db_session: Session, package: Path) -> None:
        import_into(db_session, package)
        count = db_session.scalar(select(func.count()).select_from(t.OhlcvBar))
        assert count == 7  # eight data rows, one quarantined

    def test_every_stored_bar_carries_a_knowledge_time(
        self, db_session: Session, package: Path
    ) -> None:
        import_into(db_session, package)
        for bar in db_session.scalars(select(t.OhlcvBar)):
            assert bar.knowledge_time is not None
            assert bar.knowledge_time.tzinfo is not None
            assert bar.knowledge_source == str(KnowledgeTimeSource.VENDOR_INGEST)

    def test_the_package_row_records_the_manifest_digest(
        self, db_session: Session, package: Path
    ) -> None:
        stored, report = import_into(db_session, package)
        assert stored.manifest_digest == manifest_for(package).digest()
        assert stored.snapshot_id == report.snapshot_id  # type: ignore[attr-defined]
        assert stored.adjustment_policy == "raw_unadjusted"

    def test_every_declared_file_is_recorded_with_its_digest(
        self, db_session: Session, package: Path
    ) -> None:
        stored, _ = import_into(db_session, package)
        files = list(
            db_session.scalars(
                select(t.DataPackageFile).where(t.DataPackageFile.package_id == stored.id)
            )
        )
        assert {f.path for f in files} == {"bars.csv", "instruments.csv"}
        bars = next(f for f in files if f.path == "bars.csv")
        assert bars.sha256 == file_digest(package / "bars.csv")
        # The mapping is the interpretation: without it nobody can tell
        # afterwards which column was read as "close".
        assert payload(bars.column_map)["close"] == "Close"

    def test_the_quarantined_row_is_kept_with_its_stage_and_line(
        self, db_session: Session, package: Path
    ) -> None:
        stored, _ = import_into(db_session, package)
        rows = list(db_session.scalars(select(t.QuarantinedRow)))
        assert len(rows) == 1
        row = rows[0]
        assert row.stage == "validated"
        assert row.source_file == "bars.csv"
        assert row.line_number == 8
        assert row.package_id == stored.id
        assert "contradicts itself" in row.reason
        # Kept verbatim: the operator sees their own data, not our reading.
        assert "8.00" in row.payload

    def test_corrections_are_persisted_with_rule_and_reason(
        self, db_session: Session, package: Path
    ) -> None:
        stored, _ = import_into(db_session, package)
        corrections = list(
            db_session.scalars(
                select(t.ImportCorrection).where(t.ImportCorrection.package_id == stored.id)
            )
        )
        assert {c.rule for c in corrections} == {"strip_number_noise", "strip_thousands"}
        for correction in corrections:
            assert correction.reason
            assert correction.source_file == "bars.csv"
            assert correction.line_number == 9

    def test_an_ingestion_run_is_opened_per_dataset_and_closed(
        self, db_session: Session, package: Path
    ) -> None:
        import_into(db_session, package)
        runs = list(db_session.scalars(select(t.IngestionRun)))
        assert {r.dataset for r in runs} == {"daily_bars", "instruments"}
        for run in runs:
            assert run.status == "succeeded"
            assert run.finished_at is not None
        bars = next(r for r in runs if r.dataset == "daily_bars")
        assert bars.rows_written == 7
        assert bars.rows_rejected == 1

    def test_the_counts_on_the_package_match_the_report(
        self, db_session: Session, package: Path
    ) -> None:
        stored, report = import_into(db_session, package)
        assert stored.rows_read == report.rows_read  # type: ignore[attr-defined]
        assert stored.rows_imported == report.rows_written  # type: ignore[attr-defined]
        assert stored.rows_quarantined == report.rows_quarantined  # type: ignore[attr-defined]
        assert stored.rows_read == stored.rows_imported + stored.rows_quarantined

    def test_the_whole_report_is_stored_for_later_checking(
        self, db_session: Session, package: Path
    ) -> None:
        stored, _ = import_into(db_session, package)
        assert stored.report is not None
        datasets = payload(payload(stored.report)["datasets"])
        assert payload(datasets["daily_bars"])["rows_quarantined"] == 1
        assert payload(stored.known_limitations)["items"] == ["no delisted securities before 2010"]

    def test_declared_and_observed_coverage_are_both_kept(
        self, db_session: Session, package: Path
    ) -> None:
        # A manifest claiming 2004-2024 over a file that stops in 2019 is not a
        # rounding error, and storing only one of the two would hide it.
        stored, _ = import_into(db_session, package)
        assert stored.coverage_start == dt.date(2020, 1, 2)
        assert stored.observed_start == dt.date(2020, 1, 2)
        assert stored.observed_end == dt.date(2020, 1, 10)

    def test_a_partial_import_is_flagged_in_the_row(
        self, db_session: Session, package: Path
    ) -> None:
        stored, _ = import_into(db_session, package, options=ImportOptions(limit_rows=2))
        assert stored.partial is True
        assert stored.snapshot_id.endswith("-partial")

    def test_the_instrument_master_lands_in_the_instrument_table(
        self, db_session: Session, package: Path
    ) -> None:
        import_into(db_session, package)
        instrument = db_session.scalar(select(t.Instrument))
        assert instrument is not None
        assert instrument.name == "Test Co"
        assert instrument.listing_status == "active"

    def test_prices_are_stored_as_decimals_not_floats(
        self, db_session: Session, package: Path
    ) -> None:
        import_into(db_session, package)
        bar = db_session.scalar(select(t.OhlcvBar))
        assert bar is not None
        assert isinstance(bar.close, Decimal)
        assert bar.close == Decimal("10.50")

    def test_a_dataset_with_no_table_is_counted_rather_than_lost(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        # FILINGS goes through every pipeline stage but has no home in this
        # schema yet. "Not implemented" must not be mistakable for "imported".
        assert DatasetKind.FILINGS in UNMAPPED_DATASETS
        (tmp_path / "filings.csv").write_text(
            "instrument_id,accession,form_type,filed_at,period_end\n"
            "1,0001,10-Q,2020-05-08T20:15:00+00:00,2020-03-31\n",
            encoding="utf-8",
        )
        manifest = PackageManifest(
            name="filings-only",
            provider="fixture",
            export_date=dt.date(2020, 6, 1),
            coverage=Coverage(start=dt.date(2020, 3, 31), end=dt.date(2020, 5, 8)),
            timezone="America/New_York",
            adjustment_policy=AdjustmentPolicyDeclaration.RAW_UNADJUSTED,
            files=(
                DatasetFile(
                    path="filings.csv",
                    dataset=DatasetKind.FILINGS,
                    sha256=file_digest(tmp_path / "filings.csv"),
                ),
            ),
        )
        sink = DatabaseSink(session=db_session, manifest=manifest)
        report = PackageImporter(manifest, tmp_path, sink=sink).run()
        stored = sink.finalise(report)
        db_session.commit()
        assert sink.unmapped == {"filings": 1}
        assert payload(stored.report)["unmapped_datasets"] == {"filings": 1}
        assert any("not persisted" in note for note in report.notes)


class TestPointInTimeInStorage:
    """The point-in-time guarantee, checked where it finally has to hold."""

    def test_a_fundamental_fact_is_never_stored_at_its_period_end(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        (tmp_path / "fundamentals.csv").write_text(
            "instrument_id,metric,value,period_end,fiscal_period,fiscal_year\n"
            "1,revenue,1000,2020-03-31,Q1,2020\n",
            encoding="utf-8",
        )
        manifest = PackageManifest(
            name="pit",
            provider="fixture",
            export_date=dt.date(2020, 8, 1),
            coverage=Coverage(start=dt.date(2020, 3, 31), end=dt.date(2020, 3, 31)),
            timezone="America/New_York",
            adjustment_policy=AdjustmentPolicyDeclaration.RAW_UNADJUSTED,
            files=(
                DatasetFile(
                    path="fundamentals.csv",
                    dataset=DatasetKind.FUNDAMENTALS,
                    sha256=file_digest(tmp_path / "fundamentals.csv"),
                ),
            ),
        )
        sink = DatabaseSink(session=db_session, manifest=manifest)
        options = ImportOptions(knowledge=KnowledgeTimePolicy(require_reported_fundamentals=False))
        report = PackageImporter(manifest, tmp_path, options=options, sink=sink).run()
        stored = sink.finalise(report)
        db_session.commit()

        fact = db_session.scalar(select(t.FundamentalFact))
        assert fact is not None
        assert fact.period_end == dt.date(2020, 3, 31)
        assert fact.knowledge_time.date() > fact.period_end
        assert fact.knowledge_source == str(KnowledgeTimeSource.ESTIMATED)
        assert fact.knowledge_time >= fact.event_time  # the schema's constraint
        assert stored.rows_estimated_knowledge_time == 1

    def test_strict_mode_quarantines_rather_than_storing_a_guess(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        (tmp_path / "fundamentals.csv").write_text(
            "instrument_id,metric,value,period_end,fiscal_period,fiscal_year\n"
            "1,revenue,1000,2020-03-31,Q1,2020\n",
            encoding="utf-8",
        )
        manifest = PackageManifest(
            name="pit-strict",
            provider="fixture",
            export_date=dt.date(2020, 8, 1),
            coverage=Coverage(start=dt.date(2020, 3, 31), end=dt.date(2020, 3, 31)),
            timezone="America/New_York",
            adjustment_policy=AdjustmentPolicyDeclaration.RAW_UNADJUSTED,
            files=(
                DatasetFile(
                    path="fundamentals.csv",
                    dataset=DatasetKind.FUNDAMENTALS,
                    sha256=file_digest(tmp_path / "fundamentals.csv"),
                ),
            ),
        )
        sink = DatabaseSink(session=db_session, manifest=manifest)
        report = PackageImporter(manifest, tmp_path, sink=sink).run()
        sink.finalise(report)
        db_session.commit()

        assert db_session.scalar(select(func.count()).select_from(t.FundamentalFact)) == 0
        row = db_session.scalar(select(t.QuarantinedRow))
        assert row is not None
        assert row.stage == "point_in_time"
        assert "requires one" in row.reason
