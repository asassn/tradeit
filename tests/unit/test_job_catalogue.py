"""The job catalogue is configuration, so it is validated like configuration.

A dependency on a job that does not exist, or a cycle, would otherwise be
discovered at 21:30 on a trading day.
"""

from __future__ import annotations

import pytest

from tradeit.errors import ConfigError
from tradeit.scheduling.jobs import (
    JOB_CATALOGUE,
    JobSpec,
    JobTrigger,
    execution_order,
    get_job,
    trading_blockers,
    validate_catalogue,
)


class TestCatalogueIntegrity:
    def test_the_shipped_catalogue_is_valid(self):
        validate_catalogue()

    def test_every_dependency_resolves(self):
        names = {job.name for job in JOB_CATALOGUE}
        for job in JOB_CATALOGUE:
            assert set(job.depends_on) <= names, f"{job.name} has a dangling dependency"

    def test_execution_order_places_dependencies_first(self):
        order = execution_order()
        position = {name: i for i, name in enumerate(order)}
        for job in JOB_CATALOGUE:
            for dependency in job.depends_on:
                assert position[dependency] < position[job.name], (
                    f"{dependency} must be ordered before {job.name}"
                )

    def test_execution_order_covers_every_job(self):
        assert sorted(execution_order()) == sorted(j.name for j in JOB_CATALOGUE)

    def test_a_cycle_is_detected(self):
        cyclic = (
            JobSpec(name="a", description="", trigger=JobTrigger.MANUAL, depends_on=("b",)),
            JobSpec(name="b", description="", trigger=JobTrigger.MANUAL, depends_on=("a",)),
        )
        with pytest.raises(ConfigError, match="cycle"):
            validate_catalogue(cyclic)

    def test_a_dangling_dependency_is_detected(self):
        broken = (
            JobSpec(name="a", description="", trigger=JobTrigger.MANUAL, depends_on=("ghost",)),
        )
        with pytest.raises(ConfigError, match="unknown job 'ghost'"):
            validate_catalogue(broken)

    def test_duplicate_names_are_detected(self):
        dupes = (
            JobSpec(name="a", description="", trigger=JobTrigger.MANUAL),
            JobSpec(name="a", description="", trigger=JobTrigger.MANUAL),
        )
        with pytest.raises(ConfigError, match="duplicate"):
            validate_catalogue(dupes)

    def test_unknown_job_lookup_fails_clearly(self):
        with pytest.raises(ConfigError, match="unknown job"):
            get_job("does_not_exist")


class TestTriggerConsistency:
    def test_a_cron_job_without_an_expression_is_refused(self):
        with pytest.raises(ConfigError, match="without an expression"):
            JobSpec(name="x", description="", trigger=JobTrigger.CRON)

    def test_a_market_relative_job_without_an_offset_is_refused(self):
        with pytest.raises(ConfigError, match="no offset"):
            JobSpec(name="x", description="", trigger=JobTrigger.MARKET_CLOSE_OFFSET)

    def test_an_interval_job_without_an_interval_is_refused(self):
        with pytest.raises(ConfigError, match="no interval"):
            JobSpec(name="x", description="", trigger=JobTrigger.INTERVAL_DURING_SESSION)

    def test_a_non_idempotent_job_may_not_retry(self):
        """Retrying a non-idempotent job corrupts state, so the combination is
        refused rather than documented."""
        with pytest.raises(ConfigError, match="not idempotent but allows retries"):
            JobSpec(
                name="x",
                description="",
                trigger=JobTrigger.MANUAL,
                idempotent=False,
                max_retries=2,
            )

    def test_every_catalogue_job_is_idempotent(self):
        assert all(job.idempotent for job in JOB_CATALOGUE)


class TestTradingGate:
    def test_the_data_pipeline_blocks_trading(self):
        blockers = {job.name for job in trading_blockers()}
        for essential in (
            "ingest_daily_bars",
            "ingest_corporate_actions",
            "validate_data_quality",
            "compute_indicators",
            "risk_check",
        ):
            assert essential in blockers, f"{essential} must block trading on failure"

    def test_a_trading_blocker_never_depends_on_a_non_blocker(self):
        """Otherwise the gate is porous: a blocking job could succeed while the
        non-blocking job it needed had already failed."""
        by_name = {job.name: job for job in JOB_CATALOGUE}
        for job in trading_blockers():
            for dependency in job.depends_on:
                assert by_name[dependency].blocks_trading, (
                    f"{job.name} blocks trading but depends on non-blocking {dependency}"
                )

    def test_the_plan_generator_runs_after_every_gate(self):
        plan = get_job("generate_trade_plan")
        assert {"daily_scan", "risk_check", "manage_open_positions"} <= set(plan.depends_on)

    def test_bars_are_ingested_after_corporate_actions(self):
        """A split announced today must be loadable before today's prices are
        read, or the adjustment on read is wrong for one session."""
        order = execution_order()
        assert order.index("ingest_corporate_actions") < order.index("ingest_daily_bars")
