"""Domain exception hierarchy.

Services raise these; the HTTP layer is responsible for translating them into
responses (see ``app.api.errors``). Nothing under ``app/services`` or
``app/repositories`` should import ``fastapi.HTTPException``.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected, domain-level failure."""

    status_code: int = 500
    code: str = "internal_error"
    title: str = "Internal Server Error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail or self.title
        self.extra = extra or {}
        super().__init__(self.detail)


class AuthenticationError(AppError):
    status_code = 401
    code = "authentication_failed"
    title = "Authentication Failed"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "permission_denied"
    title = "Permission Denied"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    title = "Resource Not Found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    title = "Conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    title = "Validation Error"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    title = "Too Many Requests"


class ExternalServiceError(AppError):
    """An upstream dependency (e.g. a market data provider) failed."""

    status_code = 502
    code = "external_service_error"
    title = "Upstream Service Error"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    title = "Service Unavailable"
