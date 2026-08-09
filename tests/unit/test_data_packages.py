"""The import pipeline, exercised on packages built to break it.

The tests that matter here are not the happy path. They are the ones that pin
down behaviour someone would otherwise be tempted to "simplify": that an
ambiguous date is refused rather than guessed, that a fundamental fact is never
knowable on its period-end date, that a row is never discarded, and that the
raw file is never modified.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradeit.core.enums import DataQualityFlag, KnowledgeTimeSource
from tradeit.data.packages.importer import (
    ABORT_MINIMUM_ROWS,
    ImportOptions,
    PackageImporter,
    import_package,
    inspect_package,
)
from tradeit.data.packages.manifest import (
    Coverage,
    DatasetFile,
    PackageManifest,
    build_manifest_template,
    file_digest,
    load_manifest,
    verify_files,
)
from tradeit.data.packages.normalize import (
    CoercionError,
    NormalizationPolicy,
    coerce_date,
    coerce_decimal,
    coerce_int,
    is_null,
)
from tradeit.data.packages.pointintime import (
    MINIMUM_FUNDAMENTAL_LAG_DAYS,
    KnowledgeTimePolicy,
    PointInTimeError,
    assign_knowledge_time,
)
from tradeit.data.packages.readers import extract_archive, read_rows, resolve_package
from tradeit.data.packages.sinks import CollectingSink, CountingSink, JsonlSink
from tradeit.data.packages.spec import (
    DATASET_SPECS,
    AdjustmentPolicyDeclaration,
    DatasetKind,
    capabilities_for,
    describe_dataset,
)
from tradeit.data.packages.stages import (
    Correction,
    NormalizedRecord,
    QuarantineEntry,
    RawRecord,
    Stage,
    ValidatedRecord,
    row_digest,
)
from tradeit.data.packages.validate import (
    SeriesCheckConfig,
    SeriesState,
    ValidationFailure,
    validate_row,
)
from tradeit.errors import ConfigError, DataError

BAR_COLUMNS = {
    "session_date": "Date",
    "instrument_id": "Sym",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Vol",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def write_bars(path: Path, rows: list[str]) -> None:
    header = "Date,Sym,Open,High,Low,Close,Vol"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def clean_bar_rows(count: int = 10, start: dt.date = dt.date(2020, 1, 2)) -> list[str]:
    return [
        f"{start + dt.timedelta(days=i)},1,10.00,11.00,9.50,10.50,1000000" for i in range(count)
    ]


def instruments_file(path: Path) -> None:
    path.write_text(
        "instrument_id,name,primary_exchange,asset_class\n1,Test Co,XNYS,common_stock\n",
        encoding="utf-8",
    )


def build_manifest(
    root: Path,
    files: list[DatasetFile],
    *,
    coverage: Coverage | None = None,
    export_date: dt.date = dt.date(2020, 3, 1),
    **kwargs: object,
) -> PackageManifest:
    return PackageManifest(
        name="test",
        provider="fixture",
        export_date=export_date,
        coverage=coverage or Coverage(start=dt.date(2020, 1, 2), end=dt.date(2020, 1, 11)),
        timezone="America/New_York",
        adjustment_policy=AdjustmentPolicyDeclaration.RAW_UNADJUSTED,
        files=tuple(files),
        **kwargs,
    )


def bar_file(root: Path, name: str = "bars.csv") -> DatasetFile:
    return DatasetFile(
        path=name,
        dataset=DatasetKind.DAILY_BARS,
        sha256=file_digest(root / name),
        columns=BAR_COLUMNS,
    )


@pytest.fixture
def package(tmp_path: Path) -> Path:
    """A package directory holding only what its manifest will declare.

    Deliberately no stray instruments.csv: verify_files reports an undeclared
    file as a problem, which aborts the import. That is correct behaviour — a
    file nobody declared is a file whose provenance nobody can state — so the
    fixture does not create one, and the test that wants an instrument master
    calls add_instruments explicitly.
    """
    write_bars(tmp_path / "bars.csv", clean_bar_rows())
    return tmp_path


def add_instruments(root: Path) -> DatasetFile:
    instruments_file(root / "instruments.csv")
    return DatasetFile(
        path="instruments.csv",
        dataset=DatasetKind.INSTRUMENTS,
        sha256=file_digest(root / "instruments.csv"),
    )


def run(root: Path, manifest: PackageManifest, **kwargs: object) -> tuple[object, CollectingSink]:
    sink = CollectingSink()
    options = kwargs.pop("options", None) or ImportOptions()
    report = PackageImporter(manifest, root, options=options, sink=sink).run()  # type: ignore[arg-type]
    return report, sink


# ---------------------------------------------------------------------------
# Dataset specification
# ---------------------------------------------------------------------------


class TestSpec:
    def test_every_dataset_kind_has_a_spec(self) -> None:
        assert set(DATASET_SPECS) == set(DatasetKind)

    def test_every_spec_names_at_least_one_required_column(self) -> None:
        for kind, spec in DATASET_SPECS.items():
            assert spec.required_columns, f"{kind} requires nothing, so nothing identifies a row"

    def test_every_spec_says_why_it_matters(self) -> None:
        # The document generated from these is the one a package owner reads
        # before spending money on a vendor. A dataset that cannot say why it
        # matters should not be on the list.
        for kind, spec in DATASET_SPECS.items():
            assert spec.why, f"{kind} does not say why it matters"

    def test_dependencies_point_at_real_datasets(self) -> None:
        for spec in DATASET_SPECS.values():
            for dependency in spec.requires:
                assert dependency in DATASET_SPECS

    def test_capabilities_reports_both_halves(self) -> None:
        capability = capabilities_for([DatasetKind.INSTRUMENTS, DatasetKind.DAILY_BARS])
        assert "pattern detection" in capability.available
        blocked = {name for name, _ in capability.blocked}
        assert "market breadth" in blocked
        assert "Phase 6 fundamental scoring" in blocked

    def test_a_dataset_whose_dependency_is_missing_is_blocked_not_available(self) -> None:
        capability = capabilities_for([DatasetKind.SYMBOL_MAPPINGS])
        assert "symbol-change handling" not in capability.available
        needs = dict(capability.blocked)["symbol-change handling"]
        assert "instruments" in needs

    def test_describe_dataset_renders_the_column_contract(self) -> None:
        text = describe_dataset(DatasetKind.DAILY_BARS)
        assert "session_date" in text
        assert "| Column |" in text

    def test_unknown_adjustment_blocks_pattern_use(self) -> None:
        assert not AdjustmentPolicyDeclaration.UNKNOWN.is_usable_for_patterns
        assert AdjustmentPolicyDeclaration.SPLIT_ADJUSTED.is_usable_for_patterns


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class TestManifest:
    def test_adjustment_policy_has_no_default(self, package: Path) -> None:
        with pytest.raises(ValidationError):
            PackageManifest(
                name="x",
                provider="y",
                export_date=dt.date(2020, 1, 1),
                coverage=Coverage(start=dt.date(2020, 1, 1), end=dt.date(2020, 1, 2)),
                timezone="UTC",
                files=(bar_file(package),),
            )  # type: ignore[call-arg]

    def test_file_paths_may_not_escape_the_package(self, package: Path) -> None:
        with pytest.raises(ConfigError, match="must not escape"):
            DatasetFile(
                path="../secrets.csv",
                dataset=DatasetKind.DAILY_BARS,
                sha256="0" * 64,
            )

    def test_a_mapping_to_an_unknown_field_is_refused(self, package: Path) -> None:
        with pytest.raises(ConfigError, match="not fields of"):
            DatasetFile(
                path="bars.csv",
                dataset=DatasetKind.DAILY_BARS,
                sha256="0" * 64,
                columns={"adjusted_close": "AdjClose"},
            )

    def test_digest_covers_the_file_hashes(self, package: Path) -> None:
        one = build_manifest(package, [bar_file(package)])
        write_bars(package / "bars.csv", clean_bar_rows(11))
        two = build_manifest(package, [bar_file(package)])
        assert one.digest() != two.digest()

    def test_snapshot_id_is_stable_for_identical_bytes(self, package: Path) -> None:
        one = build_manifest(package, [bar_file(package)])
        two = build_manifest(package, [bar_file(package)])
        assert one.snapshot_id() == two.snapshot_id()

    def test_verify_files_reports_a_changed_file(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        write_bars(package / "bars.csv", clean_bar_rows(12))
        problems = verify_files(manifest, package)
        assert any("sha256" in p or "digest" in p for p in problems)

    def test_verify_files_notices_an_undeclared_file(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        (package / "extra.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        problems = verify_files(manifest, package)
        assert any("extra.csv" in p for p in problems)

    def test_missing_dependencies_are_reported_not_refused(self, package: Path) -> None:
        # A package of bars with no instrument master is legitimately useful for
        # a smoke test. It just cannot resolve instrument references, and the
        # owner should learn that at import rather than from a silent skip.
        (package / "symbols.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        symbols = DatasetFile(
            path="symbols.csv",
            dataset=DatasetKind.SYMBOL_MAPPINGS,
            sha256=file_digest(package / "symbols.csv"),
        )
        manifest = build_manifest(package, [bar_file(package), symbols])
        missing = manifest.missing_dependencies()
        assert missing[DatasetKind.SYMBOL_MAPPINGS] == (DatasetKind.INSTRUMENTS,)
        assert DatasetKind.DAILY_BARS in missing  # bars need instruments too

    def test_the_same_file_may_not_be_declared_twice(self, package: Path) -> None:
        # Two rows for one file would double every count computed from it.
        with pytest.raises(ConfigError, match="same file twice"):
            build_manifest(package, [bar_file(package), bar_file(package)])

    def test_template_fails_validation_until_it_is_filled_in(self, tmp_path: Path) -> None:
        # The template is a prompt, not a working manifest. If it validated as
        # written, its placeholder adjustment policy would become a claim.
        text = build_manifest_template(tmp_path, name="x", provider="y")
        assert "PLEASE_SET" in text
        path = tmp_path / "manifest.toml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises((ValidationError, ConfigError)):
            load_manifest(path)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


class TestReaders:
    def test_reads_gzip(self, tmp_path: Path) -> None:
        path = tmp_path / "bars.csv.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write("a,b\n1,2\n")
        assert list(read_rows(path)) == [{"a": "1", "b": "2"}]

    def test_strips_a_byte_order_mark(self, tmp_path: Path) -> None:
        path = tmp_path / "bars.csv"
        path.write_bytes("﻿date,close\n2020-01-02,10\n".encode())
        rows = list(read_rows(path))
        assert "date" in rows[0]

    def test_empty_cells_become_none_not_empty_string(self, tmp_path: Path) -> None:
        path = tmp_path / "x.csv"
        path.write_text("a,b\n1,\n", encoding="utf-8")
        assert list(read_rows(path)) == [{"a": "1", "b": None}]

    def test_archive_with_a_traversal_entry_is_refused_whole(self, tmp_path: Path) -> None:
        archive = tmp_path / "p.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("manifest.toml", "x")
            bundle.writestr("../escape.csv", "a,b\n")
        with pytest.raises(DataError, match="outside the extraction"):
            extract_archive(archive, tmp_path / "out")
        # Nothing was extracted: refusing the archive means refusing all of it.
        assert not (tmp_path / "out" / "manifest.toml").exists()

    def test_archive_with_one_top_level_folder_resolves(self, tmp_path: Path) -> None:
        archive = tmp_path / "p.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("pkg/manifest.toml", "x")
        root = extract_archive(archive, tmp_path / "out")
        assert root.name == "pkg"

    def test_two_manifests_is_ambiguous_and_refused(self, tmp_path: Path) -> None:
        archive = tmp_path / "p.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("a/manifest.toml", "x")
            bundle.writestr("b/manifest.toml", "x")
        with pytest.raises(DataError, match="exactly one"):
            extract_archive(archive, tmp_path / "out")

    def test_resolve_package_refuses_a_plain_file(self, tmp_path: Path) -> None:
        path = tmp_path / "notapackage.txt"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(DataError, match="neither a directory nor"):
            resolve_package(path)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    policy = NormalizationPolicy(timezone="America/New_York")

    def test_iso_dates_parse(self) -> None:
        assert coerce_date("2020-01-02", "d", self.policy) == dt.date(2020, 1, 2)

    def test_compact_dates_parse(self) -> None:
        assert coerce_date("20200102", "d", self.policy) == dt.date(2020, 1, 2)

    @pytest.mark.parametrize("text", ["03/04/2021", "3/4/2021", "03-04-2021"])
    def test_ambiguous_dates_are_refused_rather_than_guessed(self, text: str) -> None:
        # This is the test that stops someone "helpfully" adding dateutil.
        with pytest.raises(CoercionError, match="will not guess"):
            coerce_date(text, "session_date", self.policy)

    def test_a_declared_format_makes_an_ambiguous_date_readable(self) -> None:
        policy = NormalizationPolicy(date_formats=("%m/%d/%Y",))
        assert coerce_date("03/04/2021", "d", policy) == dt.date(2021, 3, 4)

    def test_decimal_never_goes_through_float(self) -> None:
        value, _ = coerce_decimal("0.1", "close", self.policy)
        assert value == Decimal("0.1")
        assert value != Decimal(0.1)  # noqa: RUF032 - the float literal is the point

    def test_currency_symbols_are_stripped_with_a_recorded_reason(self) -> None:
        value, corrections = coerce_decimal("$1,234.50", "close", self.policy)
        assert value == Decimal("1234.50")
        rules = {c.rule for c in corrections}
        assert rules == {"strip_number_noise", "strip_thousands"}
        assert all(c.reason for c in corrections)

    def test_percent_signs_are_refused_because_the_unit_is_ambiguous(self) -> None:
        with pytest.raises(CoercionError, match="percent sign"):
            coerce_decimal("12.5%", "margin", self.policy)

    def test_accounting_negatives_are_read_as_negative(self) -> None:
        value, corrections = coerce_decimal("(1500)", "net_income", self.policy)
        assert value == Decimal(-1500)
        assert any(c.rule == "accounting_negative" for c in corrections)

    def test_infinity_is_not_a_quantity(self) -> None:
        with pytest.raises(CoercionError, match="not a finite"):
            coerce_decimal("Infinity", "value", self.policy)

    def test_integers_written_as_floats_are_accepted(self) -> None:
        value, _ = coerce_int("4211.0", "instrument_id", self.policy)
        assert value == 4211

    def test_a_fractional_identity_is_refused(self) -> None:
        with pytest.raises(CoercionError, match="fractional"):
            coerce_int("4211.5", "instrument_id", self.policy)

    def test_zero_is_not_a_null_token(self) -> None:
        # A volume of zero is a fact about a session. Treating it as missing is
        # how a halted stock becomes a gap.
        assert not is_null("0", self.policy)
        assert is_null("N/A", self.policy)
        assert is_null("", self.policy)

    def test_comma_cannot_be_both_separator_and_decimal_mark(self) -> None:
        with pytest.raises(DataError, match="cannot both be on"):
            NormalizationPolicy(decimal_comma=True, thousands_comma=True)

    def test_a_naive_timestamp_without_a_declared_zone_is_refused(self) -> None:
        from tradeit.data.packages.normalize import coerce_datetime

        with pytest.raises(CoercionError, match="no timezone"):
            coerce_datetime("2020-01-02 16:00:00", "filed_at", NormalizationPolicy())

    def test_a_naive_timestamp_records_the_zone_it_was_given(self) -> None:
        from tradeit.data.packages.normalize import coerce_datetime

        value, corrections = coerce_datetime("2020-01-02 16:00:00", "filed_at", self.policy)
        assert value.tzinfo is dt.UTC
        assert value.hour == 21  # 16:00 New York in January
        assert corrections[0].rule == "localise_naive"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def make_normalized(dataset: DatasetKind, values: dict[str, object]) -> NormalizedRecord:
    raw = RawRecord(dataset=dataset, source_file="f.csv", line_number=2, values={})
    return NormalizedRecord(raw=raw, values=values)


class TestValidation:
    def test_a_bar_whose_high_is_below_its_low_is_quarantined(self) -> None:
        record = make_normalized(
            DatasetKind.DAILY_BARS,
            {
                "open": Decimal(10),
                "high": Decimal(8),
                "low": Decimal(9),
                "close": Decimal(10),
                "volume": Decimal(1),
            },
        )
        with pytest.raises(ValidationFailure, match="contradicts itself"):
            validate_row(record)

    def test_a_zero_volume_bar_is_flagged_not_dropped(self) -> None:
        record = make_normalized(
            DatasetKind.DAILY_BARS,
            {
                "open": Decimal(10),
                "high": Decimal(11),
                "low": Decimal(9),
                "close": Decimal(10),
                "volume": Decimal(0),
            },
        )
        assert DataQualityFlag.SUSPECT_ZERO_VOLUME in validate_row(record)

    def test_a_negative_price_is_quarantined(self) -> None:
        record = make_normalized(
            DatasetKind.DAILY_BARS,
            {
                "open": Decimal(-1),
                "high": Decimal(11),
                "low": Decimal(-2),
                "close": Decimal(10),
                "volume": Decimal(1),
            },
        )
        with pytest.raises(ValidationFailure, match="not positive"):
            validate_row(record)

    def test_an_inverted_interval_is_quarantined(self) -> None:
        record = make_normalized(
            DatasetKind.SYMBOL_MAPPINGS,
            {"valid_from": dt.date(2020, 5, 1), "valid_to": dt.date(2020, 1, 1)},
        )
        with pytest.raises(ValidationFailure, match="not after"):
            validate_row(record)

    def test_a_dividend_paid_before_its_ex_date_is_quarantined(self) -> None:
        record = make_normalized(
            DatasetKind.DIVIDENDS,
            {
                "cash_amount": Decimal("0.5"),
                "ex_date": dt.date(2020, 5, 1),
                "pay_date": dt.date(2020, 4, 1),
            },
        )
        with pytest.raises(ValidationFailure, match="cannot be paid before"):
            validate_row(record)

    def test_an_earnings_date_announced_after_the_event_is_quarantined(self) -> None:
        record = make_normalized(
            DatasetKind.EARNINGS,
            {
                "scheduled_date": dt.date(2020, 5, 1),
                "announced_time": dt.datetime(2020, 6, 1, tzinfo=dt.UTC),
            },
        )
        with pytest.raises(ValidationFailure, match="cannot be announced after"):
            validate_row(record)

    def test_duplicate_rows_are_quarantined(self) -> None:
        state = SeriesState(config=SeriesCheckConfig())
        values = {"instrument_id": 1, "session_date": dt.date(2020, 1, 2), "close": Decimal(10)}
        state.check(make_normalized(DatasetKind.DAILY_BARS, values))
        with pytest.raises(ValidationFailure, match="duplicate"):
            state.check(make_normalized(DatasetKind.DAILY_BARS, dict(values)))

    def test_a_session_after_the_export_date_is_quarantined(self) -> None:
        state = SeriesState(config=SeriesCheckConfig(), export_date=dt.date(2020, 1, 1))
        record = make_normalized(
            DatasetKind.DAILY_BARS, {"instrument_id": 1, "session_date": dt.date(2021, 1, 1)}
        )
        with pytest.raises(ValidationFailure, match="after the package export date"):
            state.check(record)

    def test_a_price_spike_is_flagged_not_dropped(self) -> None:
        # Real markets do this. A rule that discarded them would delete the
        # most informative sessions in the sample.
        state = SeriesState(config=SeriesCheckConfig())
        first = make_normalized(
            DatasetKind.DAILY_BARS,
            {"instrument_id": 1, "session_date": dt.date(2020, 1, 2), "close": Decimal(10)},
        )
        second = make_normalized(
            DatasetKind.DAILY_BARS,
            {"instrument_id": 1, "session_date": dt.date(2020, 1, 3), "close": Decimal(100)},
        )
        state.check(first)
        assert DataQualityFlag.SUSPECT_PRICE_SPIKE in state.check(second)


# ---------------------------------------------------------------------------
# Point in time — the non-negotiable part
# ---------------------------------------------------------------------------


def dated(dataset: DatasetKind, values: dict[str, object]) -> ValidatedRecord:
    return ValidatedRecord(normalized=make_normalized(dataset, values))


class TestPointInTime:
    def test_a_daily_bar_is_knowable_at_its_own_close(self) -> None:
        record = dated(DatasetKind.DAILY_BARS, {"session_date": dt.date(2020, 1, 2)})
        placed = assign_knowledge_time(record, KnowledgeTimePolicy())
        assert placed.knowledge_time.date() == dt.date(2020, 1, 2)
        assert placed.knowledge_source is KnowledgeTimeSource.VENDOR_INGEST

    def test_a_fundamental_is_never_knowable_on_its_period_end(self) -> None:
        # The requirement the whole module exists for. A quarter ending
        # 31 March does not become available on 31 March.
        record = dated(
            DatasetKind.FUNDAMENTALS,
            {"period_end": dt.date(2020, 3, 31), "fiscal_period": "Q1"},
        )
        placed = assign_knowledge_time(
            record, KnowledgeTimePolicy(require_reported_fundamentals=False)
        )
        gap = (placed.knowledge_time.date() - dt.date(2020, 3, 31)).days
        assert gap >= MINIMUM_FUNDAMENTAL_LAG_DAYS
        assert gap == 45  # the 10-Q deadline for a non-accelerated filer
        assert placed.knowledge_source is KnowledgeTimeSource.ESTIMATED

    def test_a_filing_timestamp_on_the_period_end_date_is_refused(self) -> None:
        # No configuration relaxes this. A report cannot be filed before the
        # period it reports has closed and been compiled.
        record = dated(
            DatasetKind.FUNDAMENTALS,
            {
                "period_end": dt.date(2020, 3, 31),
                "fiscal_period": "Q1",
                "filing_timestamp": dt.datetime(2020, 3, 31, 12, tzinfo=dt.UTC),
            },
        )
        for strict in (True, False):
            with pytest.raises(PointInTimeError, match="within"):
                assign_knowledge_time(
                    record, KnowledgeTimePolicy(require_reported_fundamentals=strict)
                )

    def test_a_real_filing_timestamp_is_used_verbatim(self) -> None:
        filed = dt.datetime(2020, 5, 8, 20, 15, tzinfo=dt.UTC)
        record = dated(
            DatasetKind.FUNDAMENTALS,
            {
                "period_end": dt.date(2020, 3, 31),
                "fiscal_period": "Q1",
                "filing_timestamp": filed,
            },
        )
        placed = assign_knowledge_time(record, KnowledgeTimePolicy())
        assert placed.knowledge_time == filed
        assert placed.knowledge_source is KnowledgeTimeSource.REPORTED
        assert not placed.is_estimated

    def test_annual_reports_get_the_longer_deadline(self) -> None:
        record = dated(
            DatasetKind.FUNDAMENTALS,
            {"period_end": dt.date(2019, 12, 31), "fiscal_period": "FY"},
        )
        placed = assign_knowledge_time(
            record, KnowledgeTimePolicy(require_reported_fundamentals=False)
        )
        assert (placed.knowledge_time.date() - dt.date(2019, 12, 31)).days == 90

    def test_strict_mode_is_the_default(self) -> None:
        assert KnowledgeTimePolicy().require_reported_fundamentals is True
        record = dated(
            DatasetKind.FUNDAMENTALS,
            {"period_end": dt.date(2020, 3, 31), "fiscal_period": "Q1"},
        )
        with pytest.raises(PointInTimeError, match="requires one"):
            assign_knowledge_time(record, KnowledgeTimePolicy())

    def test_a_lag_below_the_floor_is_refused_at_construction(self) -> None:
        with pytest.raises(DataError, match="floor"):
            KnowledgeTimePolicy(quarterly_lag_days=0)

    def test_annual_lag_may_not_be_shorter_than_quarterly(self) -> None:
        with pytest.raises(DataError, match="never sooner"):
            KnowledgeTimePolicy(quarterly_lag_days=45, annual_lag_days=30)

    def test_an_earnings_date_without_an_announcement_is_knowable_only_that_day(self) -> None:
        # Conservative on purpose: a proximity filter sees fewer upcoming
        # events than a live system would, never more.
        record = dated(
            DatasetKind.EARNINGS,
            {"scheduled_date": dt.date(2020, 5, 1), "fiscal_period": "Q1"},
        )
        placed = assign_knowledge_time(
            record, KnowledgeTimePolicy(require_reported_fundamentals=False)
        )
        assert placed.knowledge_time.date() == dt.date(2020, 5, 1)
        assert placed.knowledge_source is KnowledgeTimeSource.ESTIMATED

    def test_a_reference_row_needs_an_export_date_rather_than_today(self) -> None:
        record = dated(DatasetKind.INSTRUMENTS, {"instrument_id": 1})
        with pytest.raises(PointInTimeError, match="caller error"):
            assign_knowledge_time(record, KnowledgeTimePolicy())

    def test_a_reference_row_is_placed_at_the_export_date(self) -> None:
        record = dated(DatasetKind.INSTRUMENTS, {"instrument_id": 1})
        placed = assign_knowledge_time(
            record, KnowledgeTimePolicy(), export_date=dt.date(2020, 3, 1)
        )
        assert placed.knowledge_time.date() == dt.date(2020, 3, 1)
        assert "snapshot" in placed.knowledge_note


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


class TestStages:
    def test_a_normalized_record_still_holds_its_raw_row(self) -> None:
        raw = RawRecord(
            dataset=DatasetKind.DAILY_BARS,
            source_file="bars.csv",
            line_number=7,
            values={"Close": "$10.50"},
        )
        record = NormalizedRecord(raw=raw, values={"close": Decimal("10.50")})
        assert record.raw.values["Close"] == "$10.50"
        assert record.raw.identity == "daily_bars:bars.csv:7"

    def test_a_record_with_no_corrections_provably_changed_nothing(self) -> None:
        raw = RawRecord(DatasetKind.DAILY_BARS, "bars.csv", 2, {"Close": "10.50"})
        assert not NormalizedRecord(raw=raw, values={}).was_modified

    def test_row_digest_ignores_column_order(self) -> None:
        assert row_digest({"a": 1, "b": 2}) == row_digest({"b": 2, "a": 1})

    def test_row_digest_distinguishes_decimal_from_float(self) -> None:
        assert row_digest({"a": Decimal("0.1")}) != row_digest({"a": 0.1})

    def test_quarantine_keeps_the_raw_payload_not_the_interpretation(self) -> None:
        raw = RawRecord(DatasetKind.DAILY_BARS, "bars.csv", 9, {"Close": " 10.50 "})
        entry = QuarantineEntry.of(raw, stage=Stage.VALIDATED, reason="test")
        assert json.loads(entry.payload)["Close"] == " 10.50 "

    def test_every_stage_describes_itself(self) -> None:
        for stage in Stage:
            assert stage.describe

    def test_a_correction_names_both_the_rule_and_the_reason(self) -> None:
        correction = Correction("close", "$10", "10", "strip", "vendor prefixes currency")
        payload = correction.to_payload()
        assert payload["rule"] and payload["reason"]


# ---------------------------------------------------------------------------
# The importer end to end
# ---------------------------------------------------------------------------


class TestImporter:
    def test_a_clean_package_imports_every_row(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        report, sink = run(package, manifest)
        assert report.rows_read == 10  # type: ignore[attr-defined]
        assert report.rows_written == 10  # type: ignore[attr-defined]
        assert not sink.quarantined

    def test_a_changed_file_aborts_before_a_row_is_read(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        write_bars(package / "bars.csv", clean_bar_rows(20))
        report, sink = run(package, manifest)
        assert report.aborted  # type: ignore[attr-defined]
        assert report.rows_read == 0  # type: ignore[attr-defined]
        assert sink.written == 0

    def test_no_row_is_ever_discarded(self, package: Path) -> None:
        rows = clean_bar_rows(8)
        rows.append("2020-01-11,1,10.00,8.00,9.50,10.50,1000000")  # high < low
        rows.append("2020-01-12,1,10.00,11.00,9.50,10.50,notanumber")  # bad cell
        rows.append("13/01/2020,1,10.00,11.00,9.50,10.50,900")  # ambiguous date
        write_bars(package / "bars.csv", rows)
        manifest = build_manifest(package, [bar_file(package)])
        report, sink = run(package, manifest)
        # Every line is accounted for: imported or quarantined, never neither.
        assert report.rows_read == len(rows)  # type: ignore[attr-defined]
        assert sink.written + len(sink.quarantined) == report.rows_read  # type: ignore[attr-defined]
        assert len(sink.quarantined) == 3

    def test_quarantine_records_the_stage_that_rejected_the_row(self, package: Path) -> None:
        rows = clean_bar_rows(3)
        rows.append("2020-01-11,1,10.00,8.00,9.50,10.50,1000000")
        rows.append("13/01/2020,1,10.00,11.00,9.50,10.50,900")
        write_bars(package / "bars.csv", rows)
        manifest = build_manifest(package, [bar_file(package)])
        _, sink = run(package, manifest)
        stages = {entry.stage for entry in sink.quarantined}
        assert stages == {Stage.VALIDATED, Stage.NORMALIZED}

    def test_the_source_file_is_never_modified(self, package: Path) -> None:
        before = file_digest(package / "bars.csv")
        manifest = build_manifest(package, [bar_file(package)])
        run(package, manifest)
        assert file_digest(package / "bars.csv") == before

    def test_corrections_are_reported_per_row(self, package: Path) -> None:
        write_bars(
            package / "bars.csv",
            ['2020-01-02,1,$10.00,11.00,9.50,10.50,"1,000,000"'],
        )
        manifest = build_manifest(
            package,
            [bar_file(package)],
            coverage=Coverage(start=dt.date(2020, 1, 2), end=dt.date(2020, 1, 2)),
        )
        report, sink = run(package, manifest)
        rules = {c.rule for _, c in sink.corrections}
        assert rules == {"strip_number_noise", "strip_thousands"}
        assert report.outcomes[DatasetKind.DAILY_BARS].rows_corrected == 1  # type: ignore[attr-defined]

    def test_an_unknown_adjustment_policy_is_a_reported_problem(self, package: Path) -> None:
        manifest = PackageManifest(
            name="test",
            provider="fixture",
            export_date=dt.date(2020, 3, 1),
            coverage=Coverage(start=dt.date(2020, 1, 2), end=dt.date(2020, 1, 11)),
            timezone="America/New_York",
            adjustment_policy=AdjustmentPolicyDeclaration.UNKNOWN,
            files=(bar_file(package),),
        )
        report, _ = run(package, manifest)
        assert any("adjustment_policy is UNKNOWN" in p for p in report.problems)  # type: ignore[attr-defined]

    def test_coverage_shortfall_is_reported(self, package: Path) -> None:
        manifest = build_manifest(
            package,
            [bar_file(package)],
            coverage=Coverage(start=dt.date(2004, 1, 1), end=dt.date(2020, 1, 11)),
        )
        report, _ = run(package, manifest)
        assert any("declared coverage" in p for p in report.problems)  # type: ignore[attr-defined]

    def test_a_mapping_mistake_aborts_rather_than_grinding(self, package: Path) -> None:
        rows = ["2020-01-02,1,x,y,z,w,v" for _ in range(ABORT_MINIMUM_ROWS + 10)]
        write_bars(package / "bars.csv", rows)
        manifest = build_manifest(package, [bar_file(package)])
        report, _ = run(package, manifest)
        assert report.aborted  # type: ignore[attr-defined]
        assert "mapping" in report.abort_reason  # type: ignore[attr-defined]
        assert report.rows_read < len(rows)  # type: ignore[attr-defined]

    def test_a_partial_import_cannot_share_a_snapshot_id_with_a_full_one(
        self, package: Path
    ) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        full, _ = run(package, manifest)
        partial, _ = run(package, manifest, options=ImportOptions(limit_rows=3))
        assert full.snapshot_id != partial.snapshot_id  # type: ignore[attr-defined]
        assert partial.snapshot_id.endswith("-partial")  # type: ignore[attr-defined]

    def test_identical_bytes_produce_an_identical_snapshot_id(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        one, _ = run(package, manifest)
        two, _ = run(package, manifest)
        assert one.snapshot_id == two.snapshot_id  # type: ignore[attr-defined]

    def test_a_dry_run_makes_every_decision_and_writes_nothing(self, package: Path) -> None:
        rows = clean_bar_rows(5)
        rows.append("2020-01-11,1,10.00,8.00,9.50,10.50,1000000")
        write_bars(package / "bars.csv", rows)
        manifest = build_manifest(package, [bar_file(package)])
        report, sink = run(package, manifest, options=ImportOptions(dry_run=True))
        assert sink.written == 0
        assert report.rows_read == 6  # type: ignore[attr-defined]
        assert len(sink.quarantined) == 1  # the decision was still made

    def test_reference_datasets_carry_their_snapshot_caveat(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package), add_instruments(package)])
        report, _ = run(package, manifest)
        assert any("export-date snapshot" in note for note in report.notes)  # type: ignore[attr-defined]

    def test_reference_data_imports_before_the_facts_that_point_at_it(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package), add_instruments(package)])
        _, sink = run(package, manifest)
        datasets = [str(r.dataset) for r in sink.records]
        assert datasets.index("instruments") < datasets.index("daily_bars")

    def test_the_report_renders_without_a_database(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        report, _ = run(package, manifest)
        text = report.render()  # type: ignore[attr-defined]
        assert "snapshot id" in text
        assert "Validation capability" in text
        assert "No row is discarded" in text

    def test_the_report_payload_round_trips_as_json(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        report, _ = run(package, manifest)
        assert json.loads(json.dumps(report.to_payload()))["rows_read"] == 10  # type: ignore[attr-defined]

    def test_import_package_reads_a_manifest_from_disk(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        (package / "manifest.toml").write_text(_manifest_toml(manifest), encoding="utf-8")
        report = import_package(package, sink=CountingSink())
        assert report.rows_written == 10

    def test_inspect_describes_a_package_without_importing_it(self, package: Path) -> None:
        manifest = build_manifest(package, [bar_file(package)])
        (package / "manifest.toml").write_text(_manifest_toml(manifest), encoding="utf-8")
        text = inspect_package(package)
        assert "bars.csv" in text
        assert "Validation capability" in text or "available" in text

    def test_jsonl_sink_writes_one_file_per_dataset(self, package: Path, tmp_path: Path) -> None:
        rows = clean_bar_rows(4)
        rows.append("2020-01-11,1,10.00,8.00,9.50,10.50,1000000")
        write_bars(package / "bars.csv", rows)
        manifest = build_manifest(package, [bar_file(package)])
        out = tmp_path / "out"
        sink = JsonlSink(out)
        PackageImporter(manifest, package, sink=sink).run()
        sink.close()
        assert (out / "daily_bars.jsonl").exists()
        assert (out / "quarantine.jsonl").exists()
        first = json.loads((out / "daily_bars.jsonl").read_text().splitlines()[0])
        assert first["knowledge_time"]
        assert first["values"]["close"] == "10.50"


def _manifest_toml(manifest: PackageManifest) -> str:
    """Render a manifest back to TOML for the round-trip tests."""
    lines = [
        f'name = "{manifest.name}"',
        f'provider = "{manifest.provider}"',
        f"export_date = {manifest.export_date.isoformat()}",
        f'timezone = "{manifest.timezone}"',
        f'adjustment_policy = "{manifest.adjustment_policy}"',
        "",
        "[coverage]",
        f"start = {manifest.coverage.start.isoformat()}",
        f"end = {manifest.coverage.end.isoformat()}",
        "",
    ]
    for item in manifest.files:
        lines += [
            "[[files]]",
            f'path = "{item.path}"',
            f'dataset = "{item.dataset}"',
            f'sha256 = "{item.sha256}"',
        ]
        if item.columns:
            mapping = ", ".join(f'{k} = "{v}"' for k, v in item.columns.items())
            lines.append(f"columns = {{ {mapping} }}")
        lines.append("")
    return "\n".join(lines)
