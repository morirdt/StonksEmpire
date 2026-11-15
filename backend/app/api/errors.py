"""HTTP translation of domain errors, as RFC 9457 problem details.

Every error response — domain, validation, or unhandled — leaves the app with
the same JSON shape, so the frontend has exactly one error contract to parse.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError

logger = structlog.stdlib.get_logger(__name__)

PROBLEM_JSON = "application/problem+json"


def problem_response(
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    request: Request,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        # "type" is a stable, dereferenceable-ish identifier for the error class.
        "type": f"https://stonks.empire/errors/{code}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "correlation_id": getattr(request.state, "correlation_id", None),
    }
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_JSON)


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Starlette types every handler as taking a bare Exception; the registration
    # in register_exception_handlers guarantees the concrete type.
    exc = cast(AppError, exc)
    logger.info(
        "domain_error",
        code=exc.code,
        status_code=exc.status_code,
        detail=exc.detail,
    )
    return problem_response(
        status_code=exc.status_code,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        request=request,
        extra=exc.extra or None,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    errors = [
        {
            "location": list(err.get("loc", ())),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    return problem_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation_error",
        title="Validation Error",
        detail="The request payload failed validation.",
        request=request,
        extra={"errors": errors},
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return problem_response(
        status_code=exc.status_code,
        code="http_error",
        title=detail,
        detail=detail,
        request=request,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence: log the traceback, leak nothing to the client."""
    logger.exception("unhandled_exception", exc_type=type(exc).__name__)
    return problem_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        title="Internal Server Error",
        detail=("An unexpected error occurred. Quote the correlation id when reporting this."),
        request=request,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
