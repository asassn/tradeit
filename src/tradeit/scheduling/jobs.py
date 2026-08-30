"""Declarative job catalogue.

Jobs are *declared* here and *executed* by a runner built in a later phase. The
separation is deliberate: the schedule is a design artifact that should be
reviewable without reading a Celery configuration, and every job's dependencies,
idempotency and failure policy should be visible in one place.

Three properties every job must have, encoded in :class:`JobSpec`:

**Idempotent.** A job that runs twice for the same trading date produces the
same state as running once. Schedulers retry, operators re-run, and a pipeline
that double-counts on retry is worse than one that fails.

**Explicitly ordered.** ``depends_on`` names what must have completed. Ordering
by cron times ("ingestion at 21:00, indicators at 21:15") is not ordering; it is
a hope about runtime.

**Honest about failure.** ``blocks_trading`` marks the jobs whose failure must
stop the system from trading rather than let it act on stale or partial data.
A failed ingestion followed by a successful scan produces recommendations built
on yesterday's prices, which is worse than no recommendations.

All times are UTC. US regular hours are 14:30-21:00 UTC under EDT and 14:30-22:00 UTC
under EST — which is exactly why the runner resolves
market-relative jobs through the exchange calendar rather than a fixed cron
expression. Cron fields here are the fallback for jobs genuinely tied to
wall-clock time, not to the session.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tradeit.errors import ConfigError


class JobTrigger(StrEnum):
    """How a job's run time is determined.

    Market-relative triggers are resolved against the exchange calendar at run
    time, so a half-day before Thanksgiving shifts the close-relative jobs
    automatically instead of running them three hours after the market shut.
    """

    CRON = "cron"
    MARKET_OPEN_OFFSET = "market_open_offset"
    MARKET_CLOSE_OFFSET = "market_close_offset"
    INTERVAL_DURING_SESSION = "interval_during_session"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class JobSpec:
    """One scheduled unit of work."""

    name: str
    description: str
    trigger: JobTrigger
    #: Cron expression (UTC) for CRON triggers.
    cron: str | None = None
    #: Minutes relative to the session boundary for market-relative triggers.
    offset_minutes: int | None = None
    #: Repeat interval for INTERVAL_DURING_SESSION.
    interval_minutes: int | None = None
    depends_on: tuple[str, ...] = ()
    timeout_minutes: int = 60
    max_retries: int = 2
    idempotent: bool = True
    blocks_trading: bool = False
    trading_days_only: bool = True
    queue: str = "default"

    def __post_init__(self) -> None:
        if self.trigger is JobTrigger.CRON and not self.cron:
            raise ConfigError(f"job {self.name!r} uses a cron trigger without an expression")
        if (
            self.trigger in (JobTrigger.MARKET_OPEN_OFFSET, JobTrigger.MARKET_CLOSE_OFFSET)
            and self.offset_minutes is None
        ):
            raise ConfigError(f"job {self.name!r} is market-relative but has no offset")
        if self.trigger is JobTrigger.INTERVAL_DURING_SESSION and not self.interval_minutes:
            raise ConfigError(f"job {self.name!r} is interval-triggered but has no interval")
        if not self.idempotent and self.max_retries > 0:
            raise ConfigError(
                f"job {self.name!r} is not idempotent but allows retries; "
                "retrying a non-idempotent job corrupts state"
            )


#: The daily and intraday pipeline. Ordering here is documentation; the runner
#: enforces it through ``depends_on``.
JOB_CATALOGUE: tuple[JobSpec, ...] = (
    # -- data acquisition ---------------------------------------------------
    JobSpec(
        name="ingest_reference_data",
        description="Refresh instrument identity, ticker mappings and universe membership.",
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=15,
        blocks_trading=True,
        timeout_minutes=30,
    ),
    JobSpec(
        name="ingest_corporate_actions",
        description=(
            "Load splits, dividends and symbol changes. Runs before bars so that "
            "a split announced today is available when today's prices are read."
        ),
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=20,
        depends_on=("ingest_reference_data",),
        blocks_trading=True,
    ),
    JobSpec(
        name="ingest_daily_bars",
        description="Load the session's OHLCV bars for the tradable universe.",
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=30,
        depends_on=("ingest_corporate_actions",),
        blocks_trading=True,
        timeout_minutes=45,
    ),
    JobSpec(
        name="ingest_fundamentals",
        description=(
            "Load newly filed financials and restatements. Daily rather than "
            "quarterly because filings arrive every day of the year."
        ),
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=45,
        depends_on=("ingest_reference_data",),
        timeout_minutes=45,
    ),
    JobSpec(
        name="ingest_earnings_calendar",
        description="Refresh scheduled and reported earnings dates.",
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=45,
        depends_on=("ingest_reference_data",),
        blocks_trading=True,
    ),
    JobSpec(
        name="ingest_macro_series",
        description="Load economic releases for the regime model.",
        trigger=JobTrigger.CRON,
        cron="0 23 * * *",
        trading_days_only=False,
    ),
    # -- validation ---------------------------------------------------------
    JobSpec(
        name="validate_data_quality",
        description=(
            "Run the integrity checks: missing sessions, duplicate bars, price "
            "discontinuities without a matching corporate action, stale series. "
            "Blocks trading because acting on data that failed validation is "
            "worse than not acting."
        ),
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=55,
        depends_on=("ingest_daily_bars", "ingest_corporate_actions"),
        blocks_trading=True,
    ),
    # -- analytics ----------------------------------------------------------
    JobSpec(
        name="compute_indicators",
        description="Materialise the indicator set for the universe.",
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=60,
        depends_on=("validate_data_quality",),
        blocks_trading=True,
        timeout_minutes=90,
        queue="compute",
    ),
    JobSpec(
        name="update_market_regime",
        description="Classify the market environment from benchmark data.",
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=65,
        depends_on=("compute_indicators",),
    ),
    JobSpec(
        name="update_sector_strength",
        description="Aggregate sector relative strength and rotation ranks.",
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=65,
        depends_on=("compute_indicators",),
    ),
    # -- opportunity --------------------------------------------------------
    JobSpec(
        name="daily_scan",
        description=(
            "Screen the universe, detect patterns, score candidates, and write "
            "the watchlist for the next session."
        ),
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=75,
        depends_on=("compute_indicators", "update_market_regime", "update_sector_strength"),
        timeout_minutes=90,
        queue="compute",
    ),
    JobSpec(
        name="intraday_breakout_monitor",
        description=(
            "Watch watchlist names approaching their pivots during the session "
            "and record trigger and confirmation events."
        ),
        trigger=JobTrigger.INTERVAL_DURING_SESSION,
        interval_minutes=15,
        depends_on=("daily_scan",),
        timeout_minutes=10,
        queue="realtime",
    ),
    JobSpec(
        name="intraday_scan",
        description="Re-screen for setups that developed during the session.",
        trigger=JobTrigger.INTERVAL_DURING_SESSION,
        interval_minutes=60,
        depends_on=("daily_scan",),
        queue="compute",
    ),
    # -- portfolio ----------------------------------------------------------
    JobSpec(
        name="update_portfolio_state",
        description="Mark positions to market and refresh cash and equity.",
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=35,
        depends_on=("ingest_daily_bars",),
        blocks_trading=True,
    ),
    JobSpec(
        name="manage_open_positions",
        description=(
            "Evaluate stops, trailing stops, targets, time stops and "
            "earnings-proximity exits for every open position."
        ),
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=40,
        depends_on=("update_portfolio_state", "ingest_earnings_calendar"),
        blocks_trading=True,
    ),
    JobSpec(
        name="risk_check",
        description=(
            "Evaluate portfolio-level limits and write the risk snapshot. Runs "
            "whether or not anything traded -- the quiet days are what make the "
            "series useful later."
        ),
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=50,
        depends_on=("update_portfolio_state",),
        blocks_trading=True,
    ),
    JobSpec(
        name="intraday_risk_check",
        description="Re-evaluate drawdown and heat limits during the session.",
        trigger=JobTrigger.INTERVAL_DURING_SESSION,
        interval_minutes=30,
        queue="realtime",
        timeout_minutes=5,
    ),
    JobSpec(
        name="generate_trade_plan",
        description=(
            "Size candidates, apply risk rules, rank capital allocation, and "
            "write the next session's orders. Writes a plan; does not place it."
        ),
        trigger=JobTrigger.MARKET_CLOSE_OFFSET,
        offset_minutes=90,
        depends_on=("daily_scan", "risk_check", "manage_open_positions"),
    ),
    # -- maintenance --------------------------------------------------------
    JobSpec(
        name="database_maintenance",
        description=(
            "Create next month's partitions, refresh statistics, reindex, and "
            "archive expired detail. Partition creation runs well ahead of need "
            "-- discovering a missing partition at ingest time means data loss."
        ),
        trigger=JobTrigger.CRON,
        cron="0 6 * * 0",
        trading_days_only=False,
        timeout_minutes=120,
        queue="maintenance",
    ),
    JobSpec(
        name="backfill_gaps",
        description="Re-request windows the validation job flagged as incomplete.",
        trigger=JobTrigger.CRON,
        cron="0 4 * * *",
        trading_days_only=False,
        queue="maintenance",
    ),
    JobSpec(
        name="scheduled_backtest",
        description=(
            "Re-run the reference backtest on the current configuration and "
            "compare against the stored baseline, so strategy decay and "
            "accidental behaviour changes surface without anyone remembering "
            "to look."
        ),
        trigger=JobTrigger.CRON,
        cron="0 8 * * 6",
        trading_days_only=False,
        timeout_minutes=240,
        queue="backtest",
    ),
    JobSpec(
        name="strategy_health_review",
        description=(
            "Compare live/paper results against backtested expectations and "
            "flag statistically significant degradation."
        ),
        trigger=JobTrigger.CRON,
        cron="0 10 * * 6",
        depends_on=("scheduled_backtest",),
        trading_days_only=False,
        queue="backtest",
    ),
)


_BY_NAME: dict[str, JobSpec] = {job.name: job for job in JOB_CATALOGUE}


def get_job(name: str) -> JobSpec:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise ConfigError(f"unknown job {name!r}") from None


def trading_blockers() -> tuple[JobSpec, ...]:
    """Jobs whose failure must prevent the system from trading."""
    return tuple(job for job in JOB_CATALOGUE if job.blocks_trading)


def validate_catalogue(catalogue: tuple[JobSpec, ...] = JOB_CATALOGUE) -> None:
    """Check the catalogue is internally consistent.

    Run as a unit test. A dependency on a job that does not exist, or a cycle,
    would otherwise be discovered at 21:30 on a trading day.
    """
    names = {job.name for job in catalogue}
    if len(names) != len(catalogue):
        raise ConfigError("duplicate job names in catalogue")

    by_name = {job.name: job for job in catalogue}
    for job in catalogue:
        for dependency in job.depends_on:
            if dependency not in names:
                raise ConfigError(f"job {job.name!r} depends on unknown job {dependency!r}")

    # Depth-first cycle detection.
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if name in done:
            return
        if name in visiting:
            raise ConfigError(f"dependency cycle: {' -> '.join([*trail, name])}")
        visiting.add(name)
        for dependency in by_name[name].depends_on:
            visit(dependency, (*trail, name))
        visiting.discard(name)
        done.add(name)

    for job in catalogue:
        visit(job.name, ())


def execution_order(catalogue: tuple[JobSpec, ...] = JOB_CATALOGUE) -> list[str]:
    """Topologically sorted job names. Ties broken by name for determinism."""
    validate_catalogue(catalogue)
    by_name = {job.name: job for job in catalogue}
    resolved: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for dependency in sorted(by_name[name].depends_on):
            visit(dependency)
        resolved.append(name)

    for name in sorted(by_name):
        visit(name)
    return resolved
