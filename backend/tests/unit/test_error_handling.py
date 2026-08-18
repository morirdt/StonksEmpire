"""The error contract: every failure leaves the app as RFC 9457 problem+json."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.errors import register_exception_handlers
from app.api.middleware import CorrelationIdMiddleware
from app.core.exceptions import (
    AppError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    PermissionDeniedError,
)


@pytest.fixture
async def error_client() -> AsyncClient:
    """A tiny app whose only job is to raise."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    @app.get("/not-found")
    async def not_found() -> None:
        raise NotFoundError("Watchlist 42 does not exist.")

    @app.get("/forbidden")
    async def forbidden() -> None:
        raise PermissionDeniedError(extra={"resource": "watchlist"})

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("a secret internal detail")

    @app.get("/typed")
    async def typed(count: int) -> dict[str, int]:
        return {"count": count}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (NotFoundError, 404, "not_found"),
        (PermissionDeniedError, 403, "permission_denied"),
        (ConflictError, 409, "conflict"),
        (ExternalServiceError, 502, "external_service_error"),
        (AppError, 500, "internal_error"),
    ],
)
def test_domain_errors_carry_their_http_contract(
    error: type[AppError],
    expected_status: int,
    expected_code: str,
) -> None:
    assert error.status_code == expected_status
    assert error.code == expected_code


async def test_domain_error_renders_problem_json(error_client: AsyncClient) -> None:
    async with error_client as client:
        response = await client.get("/not-found")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["code"] == "not_found"
    assert body["detail"] == "Watchlist 42 does not exist."
    assert body["instance"] == "/not-found"
    assert body["correlation_id"]


async def test_domain_error_includes_extra_context(error_client: AsyncClient) -> None:
    async with error_client as client:
        response = await client.get("/forbidden")

    assert response.json()["resource"] == "watchlist"


async def test_unhandled_exception_does_not_leak_internals(
    error_client: AsyncClient,
) -> None:
    async with error_client as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert "a secret internal detail" not in response.text
    assert body["code"] == "internal_error"
    assert body["correlation_id"]


async def test_validation_error_uses_the_same_envelope(
    error_client: AsyncClient,
) -> None:
    async with error_client as client:
        response = await client.get("/typed", params={"count": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"]
    assert body["errors"][0]["location"] == ["query", "count"]
