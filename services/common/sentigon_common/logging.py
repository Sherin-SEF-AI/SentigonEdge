"""Structured JSON logging with a correlation ID propagated across services.

The correlation ID rides the Kafka bus in message headers (see kafka.py) so a
single incident can be traced end to end: ingest to perception to context to
reason to API.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

from .config import settings

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def set_correlation_id(cid: str | None) -> None:
    _correlation_id.set(cid)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def _inject_correlation(_logger: object, _method: str, event_dict: dict) -> dict:
    cid = _correlation_id.get()
    if cid is not None:
        event_dict.setdefault("correlation_id", cid)
    return event_dict


def configure_logging(service: str | None = None) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    processors: list = [
        structlog.contextvars.merge_contextvars,
        _inject_correlation,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    if service:
        structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
