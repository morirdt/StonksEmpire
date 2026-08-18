"""Structured logging setup.

structlog renders coloured key-value lines locally and JSON in production.
Standard-library loggers (uvicorn, sqlalchemy) are routed through the same
pipeline so every line in the process shares one format and one context.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# Keys that must never reach a log sink, wherever they appear in an event.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "hashed_password",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "set-cookie",
        "secret_key",
        "api_key",
    }
)
REDACTED = "***redacted***"


def redact_sensitive(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Replace the value of any known-sensitive key."""
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(*, level: str = "INFO", json_logs: bool = False) -> None:
    """Configure structlog and the stdlib root logger. Idempotent."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        redact_sensitive,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn ships its own handlers; drop them so we do not double-log.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers.clear()
        stdlib_logger.propagate = True

    # The access log is emitted by our own middleware with richer context.
    logging.getLogger("uvicorn.access").disabled = True


get_logger: Callable[..., Any] = structlog.stdlib.get_logger
