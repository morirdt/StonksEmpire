"""Refresh-token rotation, reuse detection, logout, and deactivation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken
from app.models.user import User
from tests.integration.conftest import VALID_PASSWORD, unique_email

pytestmark = pytest.mark.integration

REFRESH_COOKIE = "refresh_token"
REFRESH_URL = "/api/v1/auth/refresh"


def _present(client: AsyncClient, token: str) -> None:
    """Make ``token`` the only refresh cookie the client will send."""
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE, token)


async def _register(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": VALID_PASSWORD},
    )
    assert response.status_code == 201
    return response.cookies[REFRESH_COOKIE]


async def test_refresh_issues_a_new_pair(client: AsyncClient) -> None:
    original = await _register(client)

    response = await client.post(REFRESH_URL)

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.cookies[REFRESH_COOKIE] != original


async def test_the_rotated_token_stops_working(client: AsyncClient) -> None:
    original = await _register(client)
    await client.post(REFRESH_URL)

    _present(client, original)
    replay = await client.post(REFRESH_URL)

    assert replay.status_code == 401


async def test_reuse_detection_revokes_the_whole_family(client: AsyncClient) -> None:
    """The core anti-theft property.

    Replaying a spent token means either it was stolen, or its replacement was.
    We cannot tell which, so the entire lineage dies and the real user has to
    log in again — the intended cost.
    """
    original = await _register(client)

    rotated = await client.post(REFRESH_URL)
    current = rotated.cookies[REFRESH_COOKIE]

    # The thief replays the token the victim already spent.
    _present(client, original)
    assert (await client.post(REFRESH_URL)).status_code == 401

    # The victim's still-valid token must now be dead too.
    _present(client, current)
    assert (await client.post(REFRESH_URL)).status_code == 401


async def test_rotation_records_the_successor(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The chain has to be walkable, or reuse detection cannot be audited."""
    await _register(client)
    await client.post(REFRESH_URL)

    tokens = (await db_session.execute(select(RefreshToken))).scalars().all()

    spent = [token for token in tokens if token.revoked_at is not None]
    assert len(spent) == 1
    assert spent[0].replaced_by_id is not None
    # One family across the rotation, not two.
    assert len({token.family_id for token in tokens}) == 1


async def test_refresh_without_a_cookie_is_rejected(client: AsyncClient) -> None:
    response = await client.post(REFRESH_URL)

    assert response.status_code == 401


async def test_logout_revokes_the_family_and_clears_the_cookie(
    client: AsyncClient,
) -> None:
    token = await _register(client)

    logout = await client.post("/api/v1/auth/logout")

    assert logout.status_code == 204

    _present(client, token)
    assert (await client.post(REFRESH_URL)).status_code == 401


async def test_logout_is_idempotent(client: AsyncClient) -> None:
    """A stale cookie must still end in a logged-out client, not a 500."""
    await _register(client)
    await client.post("/api/v1/auth/logout")

    second = await client.post("/api/v1/auth/logout")

    assert second.status_code == 204


async def test_an_inactive_user_cannot_log_in(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    email = unique_email()
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD},
    )

    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.is_active = False
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": VALID_PASSWORD},
    )

    assert response.status_code == 401
    # Same message as a bad password: deactivation is not public information.
    assert response.json()["detail"] == "Invalid email or password."


async def test_deactivation_takes_effect_before_the_token_expires(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Checked in get_current_user too, so it does not wait for next login."""
    email = unique_email()
    registered = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD},
    )
    token = registered.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.is_active = False
    await db_session.commit()

    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_an_inactive_user_cannot_refresh(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    email = unique_email()
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD},
    )

    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.is_active = False
    await db_session.commit()

    assert (await client.post(REFRESH_URL)).status_code == 401
