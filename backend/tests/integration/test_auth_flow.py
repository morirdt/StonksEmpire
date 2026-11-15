"""Register, login, refresh, logout — against a real database."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.integration.conftest import VALID_PASSWORD, unique_email

pytestmark = pytest.mark.integration

REFRESH_COOKIE = "refresh_token"


async def test_register_logs_the_user_straight_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": VALID_PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] == 900
    assert REFRESH_COOKIE in response.cookies


async def test_the_refresh_cookie_is_httponly_and_scoped(client: AsyncClient) -> None:
    """A token readable from JavaScript is a token stealable by XSS."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": VALID_PASSWORD},
    )

    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "path=/api/v1/auth" in cookie_header
    # Secure everywhere but `local`; the test environment is not local.
    assert "secure" in cookie_header


async def test_register_then_login_then_me(client: AsyncClient) -> None:
    email = unique_email()
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD, "display_name": "Ada"},
    )

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": VALID_PASSWORD},
    )
    assert login.status_code == 200

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert me.status_code == 200
    body = me.json()
    assert body["email"] == email
    assert body["display_name"] == "Ada"
    assert body["is_active"] is True
    assert "hashed_password" not in body


async def test_email_is_case_insensitive(client: AsyncClient) -> None:
    """CITEXT means Alice@ and alice@ are the same account, not two."""
    email = unique_email()
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD},
    )

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email.upper(), "password": VALID_PASSWORD},
    )

    assert login.status_code == 200


async def test_duplicate_email_conflicts(client: AsyncClient) -> None:
    email = unique_email()
    payload = {"email": email, "password": VALID_PASSWORD}
    await client.post("/api/v1/auth/register", json=payload)

    duplicate = await client.post("/api/v1/auth/register", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"


@pytest.mark.parametrize("password", ["short", "a" * 11])
async def test_short_passwords_are_rejected(client: AsyncClient, password: str) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": password},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_an_overlong_password_is_rejected(client: AsyncClient) -> None:
    """The 128 cap is an Argon2 denial-of-service guard, not a style rule."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": "a" * 129},
    )

    assert response.status_code == 422


async def test_unknown_email_and_wrong_password_are_indistinguishable(
    client: AsyncClient,
) -> None:
    """The single most important property here: no account enumeration."""
    email = unique_email()
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD},
    )

    wrong_password = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "a-completely-wrong-password"},
    )
    unknown_email = await client.post(
        "/api/v1/auth/login",
        json={"email": unique_email(), "password": VALID_PASSWORD},
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    left, right = wrong_password.json(), unknown_email.json()
    assert left["code"] == right["code"] == "authentication_failed"
    assert left["detail"] == right["detail"] == "Invalid email or password."


async def test_me_requires_a_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_failed"


async def test_me_rejects_a_garbage_token(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401
