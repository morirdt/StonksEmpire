"""Login and registration rate limits, end to end."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api.v1.routes import auth as auth_routes
from app.core.config import get_settings
from tests.integration.conftest import VALID_PASSWORD, unique_email

pytestmark = pytest.mark.integration


async def test_repeated_failed_logins_are_throttled(client: AsyncClient) -> None:
    """Blunts credential stuffing without needing Redis yet."""
    limit = get_settings().auth.login_attempts_per_minute
    payload = {"email": unique_email(), "password": "a-wrong-password-here"}

    for _ in range(limit):
        assert (await client.post("/api/v1/auth/login", json=payload)).status_code == 401

    throttled = await client.post("/api/v1/auth/login", json=payload)

    assert throttled.status_code == 429
    assert throttled.json()["code"] == "rate_limited"


async def test_registration_is_throttled(client: AsyncClient) -> None:
    limit = get_settings().auth.register_attempts_per_hour

    for _ in range(limit):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": unique_email(), "password": VALID_PASSWORD},
        )
        assert response.status_code == 201

    throttled = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": VALID_PASSWORD},
    )

    assert throttled.status_code == 429


async def test_the_limiter_is_reset_between_tests(client: AsyncClient) -> None:
    """Guards the fixture: module-level state would otherwise leak forward."""
    assert auth_routes._login_limiter.allow("probe") is True
