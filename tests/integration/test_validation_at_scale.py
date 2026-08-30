"""The validation harness against a corpus big enough to plan like the real one.

The full-universe run died here. `phase5.monitor_floor` asked PostgreSQL for

    pattern_key NOT IN (SELECT identity_key FROM patterns WHERE scan_run_id = ...)

`NOT IN` over a subquery can never become an anti-join — the NULL semantics
forbid it — so PostgreSQL compiles it to a `SubPlan`. That is *fast while the
subquery result fits in `work_mem`*, because the SubPlan is hashed; past that it
degrades to a non-hashed SubPlan re-evaluated per row. Measured on a 60,000-key
fixture, that one cliff moves the planner's estimate from 7,654 to **53,634,079**
and turns 0.07s into a timeout. The real corpus had 272,537 keys and fell off it.

It hit the statement timeout, and because a failed statement leaves the
transaction aborted, the six checks that run after it all raised
``InFailedSqlTransaction``. Four of those are Phase 4 checks, which made the
report look as though Phase 4 had failed first. It had not: the report groups by
phase and the execution order is `phase_checks()` then `scan_checks()`.

Two independent defects, so two independent fixes, and this module pins both:

* the query is an anti-join now, and the checks aggregate in SQL rather than
  materialising hundreds of thousands of ORM rows;
* every check runs inside its own SAVEPOINT, so one failure cannot reach the
  next check.

The corpus is inserted rather than scanned. What is under test is the *plans*,
and those depend on row counts and distributions, not on how the rows arrived.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tradeit.storage import tables as t
from tradeit.validation.checks import CheckClock, CheckStatus, Phase
from tradeit.validation.context import ValidationContext
from tradeit.validation.phase_checks import (
    BreakoutMonitorFloor,
    BreakoutQualityFrozen,
    BreakoutStateDistribution,
    PatternCausality,
    PatternDetectionRate,
)
from tradeit.validation.runner import run_validation
from tradeit.validation.scan_checks import (
    BreakoutCausality,
    BreakoutLifecycle,
    PatternConcentration,
    PatternIdentityChurn,
    PatternIdentityStability,
    PatternScoreDistribution,
)

DSN = os.environ.get("TRADEIT_TEST_PG_DSN")

if not DSN:
    pytest.skip("set TRADEIT_TEST_PG_DSN to run PostgreSQL tests", allow_module_level=True)

pytestmark = pytest.mark.performance

#: Big enough that the planner chooses the same shapes it chooses on the real
#: corpus — hash anti-join, parallel aggregate — and small enough to build in
#: seconds. The full-universe run was 272,537 / 341,093.
PATTERNS = 60_000
EVENTS = 75_000
INSTRUMENTS = 78

#: What the checks must finish inside. The production default is 60s
#: (`DatabaseSettings.statement_timeout_s`); this is deliberately far stricter,
#: so a regression to a per-row subplan fails here long before it reaches a real
#: corpus where it would take minutes.
STATEMENT_TIMEOUT = "10s"

#: The knob that reproduces the production plan at test scale.
#:
#: What broke the full-universe run was not row count as such — it was 272,537
#: keys exceeding `work_mem`, which flips a hashed SubPlan to a non-hashed one.
#: A 60,000-key fixture fits comfortably in the default 4MB and would run the
#: *fast* plan, so a test at this size would pass with the defect fully intact.
#: Shrinking `work_mem` puts the fixture on the far side of the same cliff.
#: Verified: `NOT IN` times out here, `NOT EXISTS` returns in 0.06s.
WORK_MEM = "64kB"

SNAPSHOT = "scale-fixture"

_CORPUS_SQL = f"""
INSERT INTO instruments (instrument_id, name, asset_class, primary_exchange, source,
                         country, currency, listing_status)
SELECT g, 'Name ' || g, 'common_stock', 'XNYS', 'fixture', 'US', 'USD', 'active'
FROM generate_series(1, {INSTRUMENTS}) g;

INSERT INTO scan_runs (scan_id, snapshot_id, timeframe, as_of, code_version, status,
                       pattern_config_digest, breakout_config_digest,
                       instruments_requested, instruments_completed)
VALUES ('big', '{SNAPSHOT}', '1d', now(), 'fixture', 'completed', 'p', 'b',
        {INSTRUMENTS}, {INSTRUMENTS}),
       ('small', '{SNAPSHOT}', '1d', now(), 'fixture', 'completed', 'p', 'b', 2, 2);

INSERT INTO scan_progress (scan_run_id, instrument_id, first_session, last_session,
                           sessions_scanned, bars_read, patterns_persisted,
                           breakouts_persisted, feed_mode, elapsed_seconds)
SELECT (SELECT id FROM scan_runs WHERE scan_id = 'big'), g,
       DATE '2010-01-04', DATE '2026-08-07', 4174, 4174, 1, 1, 'verified_prefix', 1.0
FROM generate_series(1, {INSTRUMENTS}) g;

INSERT INTO scan_progress (scan_run_id, instrument_id, first_session, last_session,
                           sessions_scanned, bars_read, patterns_persisted,
                           breakouts_persisted, feed_mode, elapsed_seconds)
SELECT (SELECT id FROM scan_runs WHERE scan_id = 'small'), g,
       DATE '2010-01-04', DATE '2026-08-07', 4174, 4174, 1, 1, 'verified_prefix', 1.0
FROM generate_series(1, 2) g;

INSERT INTO patterns (
    identity_key, scan_run_id, instrument_id, pattern_type, timeframe,
    detector_name, detector_version, config_digest, data_snapshot_digest,
    state, state_changed_at, first_detected_at, last_observed_at,
    structural_start_date, structural_end_date, structure_known_through,
    first_detected_session, last_observed_session,
    quality, peak_quality, evidence_coverage, confidence, session_count)
SELECT
    md5(r.scan_id || g::text),
    r.id,
    1 + (g % {INSTRUMENTS}),
    (ARRAY['bull_flag','vcp','cup_with_handle','double_bottom','flat_base'])[1 + g % 5],
    '1d',
    (ARRAY['bull_flag','vcp','cup_handle','double_bottom','flat_base'])[1 + g % 5],
    2, 'cfg', 'snap',
    (ARRAY['mature','near_breakout','broken_out_unconfirmed'])[1 + g % 3],
    now(), now(), now(),
    DATE '2010-01-04' + (g % 4000), DATE '2010-01-04' + (g % 4000) + 20,
    DATE '2010-01-04' + (g % 4000) + 12,
    DATE '2010-01-04' + (g % 4000) + 12,
    DATE '2010-01-04' + (g % 4000) + 20,
    50.0 + (g % 40), 60.0 + (g % 30), 80.0, 0.5, 12
FROM scan_runs r,
     generate_series(1, {PATTERNS}) g
WHERE r.snapshot_id = '{SNAPSHOT}'
  AND (r.scan_id = 'big' OR g <= 500);

INSERT INTO pattern_observations (
    pattern_id, session_date, observed_at, knowledge_time, from_state, to_state,
    reason, structure_end_observed, quality, evidence_coverage, confidence)
SELECT p.id, p.first_detected_session + s, now(), now(),
       CASE WHEN s = 0 THEN NULL ELSE 'mature' END,
       CASE WHEN s = 4 THEN p.state ELSE 'mature' END,
       CASE WHEN s = 0 THEN 'detected' ELSE 'advanced' END,
       p.first_detected_session + s, 55.0, 80.0, 0.5
FROM patterns p, generate_series(0, 4) s;

INSERT INTO breakout_events (
    event_key, scan_run_id, instrument_id, pattern_key, pattern_type,
    pattern_detector_name, pattern_detector_version, pattern_config_digest,
    breakout_config_digest, scorer_version, data_snapshot_digest,
    timeframe, attempt_number, profile, boundary_kind, boundary_level,
    boundary_anchor_date, boundary_tolerance_pct, boundary_confidence,
    boundary_method, boundary_touches, boundary_slope, pattern_quality,
    confirmed_path, confirmation_score, evidence_coverage, confidence,
    qualifying_closes, rejection_count, gap_class, earnings_context,
    state, opened_session, last_observed_session,
    breakout_quality, first_qualifying_close_session)
SELECT
    md5(p.scan_run_id::text || 'e' || g::text),
    p.scan_run_id, p.instrument_id, p.identity_key,
    p.pattern_type, p.detector_name, 2, 'cfg', 'bcfg', 1, 'snap',
    '1d', 1 + ((g - 1) / {PATTERNS})::int, 'balanced',
    'structural_pattern_boundary', 100.0, p.structural_end_date, 0.5, 0.8,
    'swing', 3, 0.0, 70.0,
    (ARRAY['immediate','retest','none','none'])[1 + g % 4], 65.0, 80.0, 0.6,
    2, 0, 'none', 'none',
    (ARRAY['confirmed','failed_breakout','expired'])[1 + g % 3],
    p.first_detected_session + 3, p.last_observed_session,
    60.0, p.first_detected_session + 3
FROM generate_series(1, {EVENTS}) g
JOIN patterns p ON p.id = 1 + (g % (SELECT count(*) FROM patterns));

INSERT INTO breakout_observations (
    event_id, session_date, observed_at, knowledge_time, from_state, to_state,
    reason, breakout_quality, confirmation_score, evidence_coverage, confidence,
    close, high, low, distance_pct)
SELECT e.id, e.opened_session + s, now(), now(),
       CASE WHEN s = 0 THEN NULL ELSE 'approaching' END,
       'approaching',
       'close_above_boundary', 60.0, 65.0, 80.0, 0.6, 101.0, 102.0, 99.0, 1.0
FROM breakout_events e, generate_series(0, 5) s;
"""


@pytest.fixture(scope="module")
def pg_engine():  # type: ignore[no-untyped-def]
    """A schema built from the migration chain, then filled by `corpus`."""
    engine = create_engine(DSN, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DSN)
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def corpus(pg_engine) -> None:  # type: ignore[no-untyped-def]
    """Insert the corpus once for the module, then ANALYZE it.

    Without the ANALYZE the planner works from default estimates and picks
    plans no real database would, which would make every assertion below a test
    of the wrong thing.
    """
    with pg_engine.begin() as conn:
        conn.execute(text("SET statement_timeout = 0"))
        for statement in _CORPUS_SQL.split(";\n"):
            if statement.strip():
                conn.execute(text(statement))
    with pg_engine.begin() as conn:
        conn.execute(text("ANALYZE"))


@pytest.fixture
def context(pg_engine, corpus) -> ValidationContext:  # type: ignore[no-untyped-def]
    session = Session(pg_engine)
    session.execute(text(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'"))
    session.execute(text(f"SET work_mem = '{WORK_MEM}'"))
    package = session.scalars(
        select(t.DataPackage).where(t.DataPackage.snapshot_id == SNAPSHOT)
    ).first()
    if package is None:
        package = t.DataPackage(
            snapshot_id=SNAPSHOT,
            name="scale",
            provider="fixture",
            source_path="/tmp",
            export_date=dt.date(2026, 8, 7),
            coverage_start=dt.date(2010, 1, 4),
            coverage_end=dt.date(2026, 8, 7),
            adjustment_policy="raw_unadjusted",
            manifest_digest="d",
            timezone="America/New_York",
            digests_verified=True,
        )
        session.add(package)
        session.flush()
        session.add(
            t.DataPackageFile(
                package_id=package.id, dataset="daily_bars", path="bars.csv", sha256="x"
            )
        )
        session.commit()
    try:
        yield ValidationContext(
            session=session,
            package=package,
            clock=CheckClock(as_of=dt.datetime(2026, 8, 7, 23, 59, tzinfo=dt.UTC)),
            scan_id="big",
        )
    finally:
        session.close()


SCAN_DEPENDENT = (
    PatternDetectionRate,
    PatternCausality,
    BreakoutStateDistribution,
    BreakoutQualityFrozen,
    BreakoutMonitorFloor,
    PatternScoreDistribution,
    PatternIdentityStability,
    PatternIdentityChurn,
    PatternConcentration,
    BreakoutLifecycle,
    BreakoutCausality,
)


class TestNothingTimesOut:
    def test_every_scan_dependent_check_completes_inside_the_timeout(
        self, context: ValidationContext
    ) -> None:
        """The whole point. A statement timeout arrives as ERROR, not FAIL."""
        run = run_validation(context, checks=[cls() for cls in SCAN_DEPENDENT])
        errors = [r for r in run.results if r.status is CheckStatus.ERROR]
        assert errors == [], [f"{r.check_id}: {r.summary}" for r in errors]
        assert len(run.results) == len(SCAN_DEPENDENT)

    def test_the_old_query_shape_really_does_fail_here(self, context: ValidationContext) -> None:
        """Guards the guard.

        A regression test for a timeout is worthless if the fixture is too
        small to time out — and at 60,000 keys under the default 4MB
        `work_mem`, `NOT IN` runs the *fast* hashed plan and would pass with
        the defect fully intact. This runs the old shape under the same session
        settings and asserts it still falls over, so if a change to the fixture
        or to `work_mem` makes it fast again, this fails and says the rest of
        the module has stopped proving anything.
        """
        run_id = context.run.scan_run_id
        with pytest.raises(OperationalError):
            context.session.execute(
                text(
                    "SELECT count(*) FROM breakout_events WHERE scan_run_id = :run "
                    "AND pattern_key NOT IN "
                    "(SELECT identity_key FROM patterns WHERE scan_run_id = :run)"
                ),
                {"run": run_id},
            )
        context.session.rollback()

    def test_the_orphan_check_is_planned_as_an_anti_join(self, context: ValidationContext) -> None:
        """A plan assertion, because a clock assertion alone would be flaky."""
        run_id = context.run.scan_run_id
        plan = "\n".join(
            row[0]
            for row in context.session.execute(
                text(
                    "EXPLAIN SELECT count(*) FROM breakout_events e "
                    "WHERE e.scan_run_id = :run AND NOT EXISTS ("
                    "  SELECT 1 FROM patterns p"
                    "  WHERE p.identity_key = e.pattern_key"
                    "    AND p.scan_run_id = e.scan_run_id)"
                ),
                {"run": run_id},
            )
        )
        assert "Anti Join" in plan, plan
        assert "SubPlan" not in plan, plan

    def test_no_check_materialises_the_corpus_as_orm_objects(
        self, context: ValidationContext
    ) -> None:
        """Three checks used to build one ORM instance per breakout event.

        At full-universe scale that was 23.8s and ~1.9GB for tallies the
        database produces in a single grouped pass. Asserted through the
        session's identity map, which is where those instances would land.
        """
        for cls in (BreakoutStateDistribution, BreakoutQualityFrozen, PatternDetectionRate):
            context.session.expunge_all()
            cls().run(context)
            assert len(context.session.identity_map) < 100, (
                f"{cls.__name__} loaded {len(context.session.identity_map)} ORM rows"
            )


class TestOneFailingCheckDoesNotPoisonTheNext:
    """The cascade, and why catching the exception was never enough.

    A failed statement leaves PostgreSQL's transaction aborted; every later
    statement on it raises InFailedSqlTransaction. On the full-universe run one
    timeout produced six further errors in checks that had nothing wrong with
    them.
    """

    class Exploding:
        check_id = "diag.exploding"
        title = "A check whose SQL does not compile"
        phase = Phase.PHASE_4
        requires = ()

        def run(self, context: ValidationContext) -> None:
            context.session.execute(text("SELECT no_such_column FROM patterns"))
            raise AssertionError("unreachable")  # pragma: no cover

    def test_the_checks_after_a_failure_still_run(self, context: ValidationContext) -> None:
        run = run_validation(
            context,
            checks=[
                PatternCausality(),
                self.Exploding(),
                PatternConcentration(),
                BreakoutLifecycle(),
            ],
        )
        by_id = {r.check_id: r for r in run.results}
        assert by_id["diag.exploding"].status is CheckStatus.ERROR
        for check_id in ("phase4.causality", "phase4.concentration", "phase5.lifecycle"):
            assert by_id[check_id].status is not CheckStatus.ERROR, by_id[check_id].summary
            assert "aborted" not in by_id[check_id].summary

    def test_a_failure_in_the_last_check_leaves_a_usable_session(
        self, context: ValidationContext
    ) -> None:
        run_validation(context, checks=[self.Exploding()])
        assert context.session.scalar(select(func.count()).select_from(t.Pattern)) is not None

    def test_the_error_carries_its_own_duration(self, context: ValidationContext) -> None:
        """So a timeout is visible as a slow check, not just a failed one."""
        run = run_validation(context, checks=[self.Exploding()])
        assert run.results[0].duration_seconds >= 0.0


class TestRunScopingSurvivesTheRewrite:
    """The optimisation must not have widened what any check reads."""

    def test_each_run_reports_only_its_own_rows(self, context: ValidationContext) -> None:
        big = context.session.scalar(
            select(func.count())
            .select_from(t.Pattern)
            .where(t.Pattern.scan_run_id == context.run.scan_run_id)
        )
        everything = context.session.scalar(select(func.count()).select_from(t.Pattern))
        assert 0 < big < everything, "the fixture must hold a second run to be excluded"

        result = PatternIdentityStability().run(context)
        assert result.evidence["identities"] == big

        small = ValidationContext(
            session=context.session,
            package=context.package,
            clock=context.clock,
            scan_id="small",
        )
        assert PatternIdentityStability().run(small).evidence["identities"] == everything - big

    def test_the_monitor_floor_join_stays_inside_one_run(self, context: ValidationContext) -> None:
        big = BreakoutMonitorFloor().run(context)
        small = BreakoutMonitorFloor().run(
            ValidationContext(
                session=context.session,
                package=context.package,
                clock=context.clock,
                scan_id="small",
            )
        )
        assert big.evidence["joined_events"] and small.evidence["joined_events"]
        total = context.session.scalar(select(func.count()).select_from(t.BreakoutEvent))
        assert big.evidence["joined_events"] + small.evidence["joined_events"] == total
        assert big.evidence["events_with_no_pattern_row"] == 0
        assert small.evidence["events_with_no_pattern_row"] == 0
