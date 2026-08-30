"""Structured logging setup.

Trading systems are audited after the fact, and "the log said something went
wrong around Tuesday" is not an audit trail. Logs are structured key-value
records so that a run can be reconstructed by filtering on instrument, dataset,
or as-of timestamp.
"""

from __future__ import annotations

import logging
import sys

import structlog

from tradeit.config import get_settings


def configure_logging(level: str | None = None, json_output: bool | None = None) -> None:
    settings = get_settings()
    resolved_level = (level or settings.log_level).upper()
    as_json = settings.log_json if json_output is None else json_output

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, resolved_level, logging.INFO),
    )

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if as_json else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, resolved_level, logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
