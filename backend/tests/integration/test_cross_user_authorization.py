"""The cross-user authorization harness.

Phase 1 owns almost no per-user resources, so this file looks thin. It exists
now because **every later phase adds its endpoints to `CROSS_USER_RESOURCES`**,
and a harness introduced after the resources exist is a harness that gets
retrofitted selectively and misses things.

The rule it enforces: a request authenticated as user A must never read or
mutate anything belonging to user B. Prefer 404 over 403 for another user's
row — 403 confirms the row exists, which is itself a leak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.conftest import VALID_PASSWORD, unique_email

pytestmark = pytest.mark.integration

# Statuses that mean "you may not have this". 404 is preferred; 403 is accepted
# where the resource's existence is not itself a secret.
DENIED = frozenset({403, 404})


@dataclass(frozen=True)
class ResourceCase:
    """One endpoint that must be unreachable across user boundaries.

    ``path`` is formatted with the ids captured from the *other* user's
    fixtures, e.g. ``"/api/v1/watchlists/{watchlist_id}"``.
    """

    method: str
    path: str
    body: dict[str, Any] | None = None
    ids: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Later phases append here. Phase 2: watchlists. Phase 5: alerts. Phase 6:
# trades and executions. Keep one entry per method that touches a user's row.
# ---------------------------------------------------------------------------
CROSS_USER_RESOURCES: list[ResourceCase] = []


async def _register_another_user(client: AsyncClient) -> dict[str, str]:
    """A second account, independent of the ``registered_user`` fixture."""
    email = unique_email("other")
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD, "display_name": "Other"},
    )
    assert response.status_code == 201
    return {"email": email, "access_token": response.json()["access_token"]}


@pytest.mark.skipif(
    not CROSS_USER_RESOURCES,
    reason="No user-scoped resources yet; Phase 2 fills this list.",
)
@pytest.mark.parametrize("case", CROSS_USER_RESOURCES, ids=lambda c: f"{c.method} {c.path}")
async def test_one_user_cannot_touch_another_users_resource(
    auth_client: AsyncClient,
    case: ResourceCase,
) -> None:
    response = await auth_client.request(case.method, case.path, json=case.body)

    assert response.status_code in DENIED


async def test_me_returns_the_caller_not_the_last_registered_user(
    client: AsyncClient,
    registered_user: dict[str, str],
) -> None:
    """The obvious way to get this wrong is to resolve the wrong subject."""
    other = await _register_another_user(client)

    first = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {registered_user['access_token']}"},
    )
    second = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )

    assert first.json()["email"] == registered_user["email"]
    assert second.json()["email"] == other["email"]
    assert first.json()["id"] != second.json()["id"]


async def test_one_users_refresh_token_does_not_authenticate_another(
    client: AsyncClient,
    registered_user: dict[str, str],
) -> None:
    """Registering a second user must not hand the first one's session away."""
    other = await _register_another_user(client)

    # The client's cookie jar now holds the *second* user's refresh token.
    rotated = await client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {rotated.json()['access_token']}"},
    )

    assert me.json()["email"] == other["email"]
    assert me.json()["email"] != registered_user["email"]
