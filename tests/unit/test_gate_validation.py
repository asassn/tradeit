"""The validation harness, including the two things it must refuse to do.

Most of these are ordinary tests of ordinary checks. Three are not, and they are
the reason the file exists:

* a blocked check is never reported as a pass;
* a run with any blocked check is not evidence, however many passed;
* no check may report a performance statistic.

Those three are the gate's whole claim about its own honesty, and a claim that
is only made in prose is a claim that decays.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tradeit.core.enums import Bartimeframe, KnowledgeTimeSource
from tradeit.data.packages.database import DatabaseSink
from tradeit.data.packages.importer import ImportOptions, PackageImporter
from tradeit.data.packages.manifest import Coverage, DatasetFile, PackageManifest, file_digest
from tradeit.data.packages.pointintime import KnowledgeTimePolicy
from tradeit.data.packages.spec import AdjustmentPolicyDeclaration, DatasetKind
from tradeit.data.validation_universe import ValidationInstrument, ValidationUniverse
from tradeit.errors import ConfigError
from tradeit.storage import tables as t
from tradeit.validation.checks import (
    FORBIDDEN_MEASURES,
    CheckClock,
    CheckResult,
    CheckStatus,
    Phase,
    assert_no_performance_claims,
    blocked,
)
from tradeit.validation.context import ValidationContext, load_context
from tradeit.validation.data_checks import (
    AdjustmentDeclared,
    KnowledgeTimeOrdering,
    PointInTimeFundamentals,
    QuarantineRate,
    RowsPresent,
    SurvivorshipCoverage,
)
from tradeit.validation.phase_checks import (
    BreakoutBoundaryProvenance,
    IndicatorDeterminism,
    IndicatorPrefixConsistency,
    IndicatorWarmup,
)
from tradeit.validation.runner import NOT_MEASURED, all_checks, run_validation

BAR_COLUMNS = {
    "session_date": "date",
    "instrument_id": "symbol",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}

EMPTY_UNIVERSE = ValidationUniverse(name="empty", description="no delisted names", instruments=())


# ---------------------------------------------------------------------------
# Fixtures: a small but real package, imported into SQLite.
# ---------------------------------------------------------------------------


def write_package(root: Path, *, sessions: int = 320, fundamentals: bool = True) -> None:
    rows = ["date,symbol,open,high,low,close,volume"]
    for instrument_id in (1, 2):
        price = 50.0 + instrument_id * 10
        day = dt.date(2019, 1, 2)
        written = 0
        while written < sessions:
            if day.weekday() < 5:
                price *= 1.0 + (0.004 if written % 7 else -0.011)
                rows.append(
                    f"{day},{instrument_id},{price * 0.99:.2f},{price * 1.02:.2f},"
                    f"{price * 0.97:.2f},{price:.2f},1000000"
                )
                written += 1
            day += dt.timedelta(days=1)
    (root / "bars.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (root / "instruments.csv").write_text(
        "instrument_id,name,primary_exchange,asset_class\n"
        "1,Alpha Corp,XNYS,common_stock\n"
        "2,Beta Inc,XNYS,common_stock\n",
        encoding="utf-8",
    )
    if fundamentals:
        (root / "fundamentals.csv").write_text(
            "instrument_id,metric,value,period_end,fiscal_period,fiscal_year,filed_at\n"
            "1,revenue,1000,2019-03-31,Q1,2019,2019-05-08T20:15:00+00:00\n"
            "1,revenue,1100,2019-06-30,Q2,2019,2019-08-07T20:20:00+00:00\n",
            encoding="utf-8",
        )


def build_manifest(
    root: Path,
    *,
    adjustment: AdjustmentPolicyDeclaration = AdjustmentPolicyDeclaration.RAW_UNADJUSTED,
    fundamentals: bool = True,
) -> PackageManifest:
    files = [
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
    ]
    if fundamentals:
        files.append(
            DatasetFile(
                path="fundamentals.csv",
                dataset=DatasetKind.FUNDAMENTALS,
                sha256=file_digest(root / "fundamentals.csv"),
                columns={"filing_timestamp": "filed_at"},
            )
        )
    return PackageManifest(
        name="harness",
        provider="fixture",
        export_date=dt.date(2021, 1, 15),
        coverage=Coverage(start=dt.date(2019, 1, 2), end=dt.date(2020, 4, 30)),
        timezone="America/New_York",
        adjustment_policy=adjustment,
        files=tuple(files),
    )


def import_package(
    session: Session, root: Path, manifest: PackageManifest, **kwargs: object
) -> t.DataPackage:
    sink = DatabaseSink(session=session, manifest=manifest, source_path=root)
    options = kwargs.pop("options", None) or ImportOptions()
    report = PackageImporter(manifest, root, options=options, sink=sink).run()  # type: ignore[arg-type]
    package = sink.finalise(report)
    session.commit()
    return package


@pytest.fixture
def context(db_session: Session, tmp_path: Path) -> ValidationContext:
    write_package(tmp_path)
    package = import_package(db_session, tmp_path, build_manifest(tmp_path))
    return load_context(
        db_session, package.snapshot_id, universe=EMPTY_UNIVERSE, code_version="test"
    )


# ---------------------------------------------------------------------------
# The honesty guarantees
# ---------------------------------------------------------------------------


class TestHonesty:
    def test_blocked_is_not_a_pass(self, context: ValidationContext) -> None:
        check = PointInTimeFundamentals()
        result = blocked(check, (DatasetKind.FUNDAMENTALS,))
        assert result.status is CheckStatus.BLOCKED
        assert not result.status.is_conclusive
        assert result.status not in (CheckStatus.PASS, CheckStatus.SKIPPED)

    def test_a_blocked_check_must_say_what_it_needs(self) -> None:
        with pytest.raises(ConfigError, match="does not say what it needs"):
            CheckResult(
                check_id="x",
                title="x",
                phase=Phase.DATA,
                status=CheckStatus.BLOCKED,
                summary="not run",
            )

    def test_a_run_with_a_blocked_check_is_not_evidence(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        # No fundamentals in the package, so the point-in-time check cannot run.
        # Everything else may pass; the run is still not a validation.
        write_package(tmp_path, fundamentals=False)
        package = import_package(db_session, tmp_path, build_manifest(tmp_path, fundamentals=False))
        context = load_context(db_session, package.snapshot_id, universe=EMPTY_UNIVERSE)
        run = run_validation(context)
        assert run.blocked_checks
        usable, reason = run.is_evidence
        assert not usable
        assert "could not run" in reason
        assert "not a validation" in run.render().lower()

    def test_no_check_may_report_a_performance_measure(self) -> None:
        offender = CheckResult(
            check_id="phase5.win_rate",
            title="Breakout win rate",
            phase=Phase.PHASE_5,
            status=CheckStatus.PASS,
            summary="confirmed breakouts won 62% of the time",
        )
        with pytest.raises(ConfigError, match="must not compute"):
            assert_no_performance_claims(offender)

    def test_the_guard_matches_whole_words_not_substrings(self) -> None:
        # The first version of this guard matched "edge" inside
        # "knowledge_time_ordering" and refused the most important check in the
        # harness. A guard that cries wolf gets deleted.
        innocent = CheckResult(
            check_id="data.knowledge_time_ordering",
            title="knowledge_time never precedes event_time",
            phase=Phase.DATA,
            status=CheckStatus.PASS,
            summary="every stored fact becomes knowable at or after it happened",
            evidence={"knowledge_time_rows": 10},
        )
        assert_no_performance_claims(innocent)  # must not raise

    def test_the_guard_still_catches_suffixed_forms(self) -> None:
        offender = CheckResult(
            check_id="phase5.summary",
            title="Breakout outcomes",
            phase=Phase.PHASE_5,
            status=CheckStatus.PASS,
            summary="most confirmed breakouts were profitable",
        )
        with pytest.raises(ConfigError, match="must not compute"):
            assert_no_performance_claims(offender)

    def test_every_real_check_passes_the_guard(self, context: ValidationContext) -> None:
        # The guard runs inside run_validation, so this is really a test that
        # the shipped checks are all nameable without a forbidden term.
        run = run_validation(context)
        assert run.results

    def test_the_report_states_what_it_did_not_measure(self, context: ValidationContext) -> None:
        text = run_validation(context).render()
        for item in NOT_MEASURED:
            assert item in text

    def test_forbidden_measures_are_lowercase_identifiers(self) -> None:
        for term in FORBIDDEN_MEASURES:
            assert term == term.lower()
            assert " " not in term


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


class TestRunner:
    def test_check_ids_are_unique(self) -> None:
        ids = [c.check_id for c in all_checks()]
        assert len(ids) == len(set(ids))

    def test_a_check_that_raises_is_an_error_not_a_crash(self, context: ValidationContext) -> None:
        class Exploding:
            check_id = "test.explodes"
            title = "Always raises"
            phase = Phase.DATA
            requires: tuple[DatasetKind, ...] = ()

            def run(self, context: object) -> CheckResult:
                raise RuntimeError("boom")

        run = run_validation(context, [Exploding()])
        assert run.results[0].status is CheckStatus.ERROR
        assert "boom" in run.results[0].summary
        assert run.failures

    def test_a_partial_snapshot_disqualifies_the_run(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        write_package(tmp_path)
        package = import_package(
            db_session,
            tmp_path,
            build_manifest(tmp_path),
            options=ImportOptions(limit_rows=20),
        )
        context = load_context(db_session, package.snapshot_id, universe=EMPTY_UNIVERSE)
        run = run_validation(context)
        usable, reason = run.is_evidence
        assert not usable
        assert "partial" in reason

    def test_an_unknown_snapshot_names_the_ones_that_exist(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        write_package(tmp_path)
        package = import_package(db_session, tmp_path, build_manifest(tmp_path))
        with pytest.raises(ConfigError, match=package.snapshot_id):
            load_context(db_session, "no-such-snapshot")

    def test_as_of_defaults_to_the_export_date_not_today(self, context: ValidationContext) -> None:
        # Defaulting to now would let the same snapshot answer differently on
        # different days, which makes a result impossible to compare with itself.
        assert context.as_of.date() == context.package.export_date

    def test_provenance_records_everything_a_reader_needs(self, context: ValidationContext) -> None:
        provenance = context.provenance()
        for key in (
            "snapshot_id",
            "manifest_digest",
            "adjustment_policy",
            "declared_coverage",
            "observed_coverage",
            "universe_digest",
            "code_version",
            "rows_estimated_knowledge_time",
        ):
            assert key in provenance

    def test_the_universe_digest_changes_with_the_roster(self, context: ValidationContext) -> None:
        # A reading over 85 names and one over 4,000 are different statistics,
        # and the digest is what makes that detectable rather than invisible.
        from tradeit.data.validation_universe import UniverseCategory

        before = context.universe_digest()
        context.universe = ValidationUniverse(
            name="empty",
            description="no delisted names",
            instruments=(ValidationInstrument("SPY", UniverseCategory.BENCHMARK, "none"),),
        )
        assert context.universe_digest() != before

    def test_the_payload_is_json_serialisable(self, context: ValidationContext) -> None:
        import json

        payload = run_validation(context).to_payload()
        assert json.loads(json.dumps(payload))["provenance"]["snapshot_id"]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


class TestDataChecks:
    def test_rows_present_passes_on_an_imported_package(self, context: ValidationContext) -> None:
        result = RowsPresent().run(context)
        assert result.status is CheckStatus.PASS
        assert result.evidence["instruments"] == 2

    def test_quarantine_rate_passes_on_a_clean_import(self, context: ValidationContext) -> None:
        assert QuarantineRate().run(context).status is CheckStatus.PASS

    def test_an_unknown_adjustment_policy_fails(self, db_session: Session, tmp_path: Path) -> None:
        write_package(tmp_path)
        package = import_package(
            db_session,
            tmp_path,
            build_manifest(tmp_path, adjustment=AdjustmentPolicyDeclaration.UNKNOWN),
        )
        context = load_context(db_session, package.snapshot_id, universe=EMPTY_UNIVERSE)
        result = AdjustmentDeclared().run(context)
        assert result.status is CheckStatus.FAIL
        assert "not interpretable" in result.summary

    def test_an_adjusted_series_warns_rather_than_failing(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        write_package(tmp_path)
        package = import_package(
            db_session,
            tmp_path,
            build_manifest(tmp_path, adjustment=AdjustmentPolicyDeclaration.SPLIT_ADJUSTED),
        )
        context = load_context(db_session, package.snapshot_id, universe=EMPTY_UNIVERSE)
        assert AdjustmentDeclared().run(context).status is CheckStatus.WARN

    def test_knowledge_time_ordering_passes_and_the_schema_also_refuses(
        self, context: ValidationContext
    ) -> None:
        """Belt and braces, and worth knowing which is which.

        The check passes on a clean import. Trying to *create* the violation
        shows why it rarely fires: ``ck_bar_knowledge`` refuses a bar whose
        knowledge_time precedes its event_time at the database level. The check
        earns its place by covering the tables and paths the constraint does
        not, and by reporting a count rather than aborting a transaction.
        """
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        assert KnowledgeTimeOrdering().run(context).status is CheckStatus.PASS

        bar = context.session.scalars(select(t.OhlcvBar).limit(1)).one()
        bar.knowledge_time = bar.event_time - dt.timedelta(days=2)
        with pytest.raises(IntegrityError, match="ck_bar_knowledge"):
            context.session.flush()
        context.session.rollback()

    def test_point_in_time_passes_when_every_fact_has_a_filing_timestamp(
        self, context: ValidationContext
    ) -> None:
        result = PointInTimeFundamentals().run(context)
        assert result.status is CheckStatus.PASS
        assert result.evidence["estimated_knowledge_time"] == 0

    def test_point_in_time_fails_on_a_fact_knowable_at_its_period_end(
        self, context: ValidationContext
    ) -> None:
        # Written directly, bypassing the importer, because the importer refuses
        # to produce this row. The check exists for the case where something
        # else wrote it.
        context.session.add(
            t.FundamentalFact(
                instrument_id=1,
                metric="revenue",
                fiscal_period="Q3",
                fiscal_year=2019,
                period_end=dt.date(2019, 9, 30),
                event_time=dt.datetime(2019, 9, 30, tzinfo=dt.UTC),
                knowledge_time=dt.datetime(2019, 9, 30, tzinfo=dt.UTC),
                knowledge_source=str(KnowledgeTimeSource.SYNTHETIC),
                value=Decimal(1),
                source="test",
            )
        )
        context.session.flush()
        result = PointInTimeFundamentals().run(context)
        assert result.status is CheckStatus.FAIL
        assert "reading the future" in result.summary

    def test_point_in_time_warns_when_timestamps_were_estimated(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        write_package(tmp_path)
        (tmp_path / "fundamentals.csv").write_text(
            "instrument_id,metric,value,period_end,fiscal_period,fiscal_year\n"
            "1,revenue,1000,2019-03-31,Q1,2019\n",
            encoding="utf-8",
        )
        package = import_package(
            db_session,
            tmp_path,
            build_manifest(tmp_path),
            options=ImportOptions(
                knowledge=KnowledgeTimePolicy(require_reported_fundamentals=False)
            ),
        )
        context = load_context(db_session, package.snapshot_id, universe=EMPTY_UNIVERSE)
        result = PointInTimeFundamentals().run(context)
        assert result.status is CheckStatus.WARN
        assert "measure the rule" in result.summary

    def test_survivorship_fails_when_the_delisted_names_are_absent(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        from tradeit.data.validation_universe import UniverseCategory

        write_package(tmp_path)
        package = import_package(db_session, tmp_path, build_manifest(tmp_path))
        universe = ValidationUniverse(
            name="delisted-only",
            description="",
            instruments=(
                ValidationInstrument(
                    "LEH",
                    UniverseCategory.DELISTED,
                    "survivorship",
                    last_trade_date=dt.date(2008, 9, 15),
                ),
            ),
        )
        context = load_context(db_session, package.snapshot_id, universe=universe)
        result = SurvivorshipCoverage().run(context)
        assert result.status is CheckStatus.FAIL
        assert "survivors" in result.summary

    def test_survivorship_names_the_reason_and_still_fails(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Classification changes the remedy, never the verdict."""
        from tradeit.data.validation_universe import UniverseCategory

        write_package(tmp_path)
        package = import_package(db_session, tmp_path, build_manifest(tmp_path))
        # Whatever the package's coverage actually is, this name stopped
        # trading long before it — so no request over that window could have
        # returned it, and the finding belongs to our acquisition plan.
        universe = ValidationUniverse(
            name="delisted-only",
            description="",
            instruments=(
                ValidationInstrument(
                    "LEH",
                    UniverseCategory.DELISTED,
                    "survivorship",
                    last_trade_date=package.coverage_start - dt.timedelta(days=365),
                    alias_candidates=("LEHMQ",),
                ),
            ),
        )
        context = load_context(db_session, package.snapshot_id, universe=universe)
        result = SurvivorshipCoverage().run(context)

        assert result.status is CheckStatus.FAIL
        assert result.evidence["by_status"] == {"outside_requested_window": 1}
        assert result.evidence["acquisition_outcomes_recorded"] is False
        rendered = "\n".join(result.detail)
        assert "LEH" in rendered
        assert "outside_requested_window" in rendered
        # The remedy is stated, and it is ours: widen the requested window.
        assert "start date at or before" in rendered


class TestPhaseChecks:
    def test_indicators_are_deterministic_on_real_bars(self, context: ValidationContext) -> None:
        assert IndicatorDeterminism().run(context).status is CheckStatus.PASS

    def test_indicators_do_not_change_when_later_bars_arrive(
        self, context: ValidationContext
    ) -> None:
        result = IndicatorPrefixConsistency().run(context)
        # Either it ran and agreed, or the series was too short to cut past
        # warm-up — never a silent pass over nothing.
        assert result.status in (CheckStatus.PASS, CheckStatus.SKIPPED)
        if result.status is CheckStatus.SKIPPED:
            assert "warm-up" in result.summary

    def test_warmup_reports_conservative_declarations_without_failing(
        self, context: ValidationContext
    ) -> None:
        # An indicator defined earlier than declared wastes bars; it does not
        # leak the future. Failing on it would make the check useless noise.
        result = IndicatorWarmup().run(context)
        assert result.status in (CheckStatus.PASS, CheckStatus.WARN)
        assert result.evidence["defined_later_than_declared"] == 0

    def test_phase5_checks_skip_rather_than_pass_with_no_events(
        self, context: ValidationContext
    ) -> None:
        result = BreakoutBoundaryProvenance().run(context)
        assert result.status is CheckStatus.SKIPPED

    def test_boundary_provenance_fails_on_a_non_structural_event(
        self, context: ValidationContext
    ) -> None:
        context.session.add(
            t.BreakoutEvent(
                event_key="a" * 32,
                instrument_id=1,
                pattern_key="b" * 32,
                timeframe=str(Bartimeframe.D1),
                attempt_number=1,
                state="confirmed",
                boundary_kind="manual_boundary",
                boundary_level=Decimal(100),
                boundary_anchor_date=dt.date(2019, 6, 3),
                boundary_tolerance_pct=0.5,
                opened_session=dt.date(2019, 6, 3),
                last_observed_session=dt.date(2019, 6, 10),
            )
        )
        context.session.flush()
        result = BreakoutBoundaryProvenance().run(context)
        assert result.status is CheckStatus.FAIL
        assert "mixture" in result.summary


class TestCheckClock:
    def test_a_naive_as_of_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="timezone-aware"):
            CheckClock(as_of=dt.datetime(2020, 1, 1))
