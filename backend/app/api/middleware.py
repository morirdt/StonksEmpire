"""Request-scoped middleware: correlation ids and structured access logging."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.stdlib.get_logger(__name__)

CORRELATION_ID_HEADER = "X-Request-ID"

RequestHandler = Callable[[Request], Awaitable[Response]]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Bind a correlation id to every log line emitted during a request.

    Reuses an inbound ``X-Request-ID`` when present so a trace can be followed
    across services, and always echoes it back on the response.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            method=request.method,
            path=request.url.path,
        )
        try:
            response = await call_next(request)
        finally:
            pass
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Replaces uvicorn's access log with a structured one."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception("request_failed", duration_ms=round(duration_ms, 2))
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "request_completed",
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response
