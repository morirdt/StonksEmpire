"""The cross-user authorization harness.

Phase 1 owns almost no per-user resources, so this file looks thin. It exists
now because **every later phase adds its endpoints to `CROSS_USER_RESOURCES`**,
and a harness introduced after the resources exist is a harness that gets
retrofitted selectively and misses things.

The rule it enforces: a request authenticated as user A must never read or
mutate anything belonging to user B. Prefer 404 over 403 for another user's
row — 403 confirms the row exists, which is itself a leak.

There are two registries, because user-scoped resources come in two shapes.
``CROSS_USER_RESOURCES`` covers the ones addressed by an id in the path, where
the test is "name someone else's row, expect to be denied".
``CALLER_SCOPED_RESOURCES`` covers the ones whose identity *is* the caller
(``/me/...``), where 200 is the correct answer and the real failure is one
user's write landing on another's row. Add a new resource to whichever fits;
adding a caller-scoped one to the first list would assert something false.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import AsyncClient

from app.models.symbol import Symbol
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
CROSS_USER_RESOURCES: list[ResourceCase] = [
    # Phase 2 — watchlists. Every method that names another user's row.
    ResourceCase("GET", "/api/v1/watchlists/{watchlist_id}"),
    ResourceCase(
        "PATCH",
        "/api/v1/watchlists/{watchlist_id}",
        body={"name": "hijacked"},
    ),
    ResourceCase("DELETE", "/api/v1/watchlists/{watchlist_id}"),
    ResourceCase("GET", "/api/v1/watchlists/{watchlist_id}/quotes"),
    ResourceCase(
        "POST",
        "/api/v1/watchlists/{watchlist_id}/items",
        body={"ticker": "MSFT"},
    ),
    ResourceCase(
        "PATCH",
        "/api/v1/watchlists/{watchlist_id}/items",
        body={"item_ids": ["00000000-0000-7000-8000-000000000000"]},
    ),
    ResourceCase("DELETE", "/api/v1/watchlists/{watchlist_id}/items/{item_id}"),
]


@dataclass(frozen=True)
class CallerScopedCase:
    """A resource whose identity *is* the caller, so there is no id to forge.

    ``CROSS_USER_RESOURCES`` cannot express these. Its whole shape is "name
    another user's row and expect to be denied", and a caller-scoped endpoint
    correctly answers 200 — with the caller's own row. Asserting a denial there
    would assert something false.

    The failure these can actually have is different, and worth its own harness:
    one user's write landing on another user's row, which a shared key, a
    mis-scoped upsert, or a cached row would all produce. So each case names a
    read, a write, and two distinguishable payloads, and the test proves the two
    users' rows stay independent.
    """

    method: str
    path: str
    mine: dict[str, Any]
    theirs: dict[str, Any]


# ---------------------------------------------------------------------------
# Phase 3: chart preferences. Later caller-scoped resources (profile settings,
# notification preferences) belong here rather than in the list above.
# ---------------------------------------------------------------------------
CALLER_SCOPED_RESOURCES: list[CallerScopedCase] = [
    CallerScopedCase(
        "PUT",
        "/api/v1/me/chart-preferences",
        mine={
            "default_range": "1M",
            "active_overlays": ["sma_20"],
            "active_oscillators": [],
        },
        theirs={
            "default_range": "2Y",
            "active_overlays": ["sma_50", "sma_200"],
            "active_oscillators": ["macd"],
        },
    ),
]


async def _register_another_user(client: AsyncClient) -> dict[str, str]:
    """A second account, independent of the ``registered_user`` fixture."""
    email = unique_email("other")
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD, "display_name": "Other"},
    )
    assert response.status_code == 201
    return {"email": email, "access_token": response.json()["access_token"]}


@pytest.fixture
async def other_users_rows(
    client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> dict[str, str]:
    """A watchlist with one item, owned by somebody who is not the caller.

    Built through the API rather than the session, so the ids are exactly what
    a real client would hold — and so a scoping bug in creation shows up here
    too rather than being papered over by a direct insert.
    """
    other = await _register_another_user(client)
    headers = {"Authorization": f"Bearer {other['access_token']}"}

    created = await client.post(
        "/api/v1/watchlists",
        json={"name": "Private list"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    watchlist_id = created.json()["id"]

    item = await client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        json={"ticker": seeded_symbols[0].ticker},
        headers=headers,
    )
    assert item.status_code == 201, item.text

    return {"watchlist_id": watchlist_id, "item_id": item.json()["id"]}


@pytest.mark.skipif(
    not CROSS_USER_RESOURCES,
    reason="No user-scoped resources yet; Phase 2 fills this list.",
)
@pytest.mark.parametrize("case", CROSS_USER_RESOURCES, ids=lambda c: f"{c.method} {c.path}")
async def test_one_user_cannot_touch_another_users_resource(
    auth_client: AsyncClient,
    other_users_rows: dict[str, str],
    case: ResourceCase,
) -> None:
    response = await auth_client.request(
        case.method,
        case.path.format(**other_users_rows),
        json=case.body,
    )

    assert response.status_code in DENIED, (
        f"{case.method} {case.path} leaked another user's row "
        f"with {response.status_code}: {response.text}"
    )


@pytest.mark.parametrize("case", CALLER_SCOPED_RESOURCES, ids=lambda c: f"{c.method} {c.path}")
async def test_a_caller_scoped_write_cannot_reach_another_users_row(
    client: AsyncClient,
    auth_client: AsyncClient,
    case: CallerScopedCase,
) -> None:
    """Two users, two writes, two rows that must not have merged.

    The order matters: the other user writes *first*, so that a bug where the
    second write overwrites a shared row is caught by their read, not hidden by
    it. Reading both back afterwards catches the mirror-image bug where a stale
    identity-mapped row serves the wrong user their neighbour's settings.
    """
    other = await _register_another_user(client)
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}

    theirs = await client.request(case.method, case.path, json=case.theirs, headers=other_headers)
    assert theirs.status_code == 200, theirs.text

    mine = await auth_client.request(case.method, case.path, json=case.mine)
    assert mine.status_code == 200, mine.text

    still_theirs = await client.get(case.path, headers=other_headers)
    assert still_theirs.json() == theirs.json(), (
        f"{case.method} {case.path} let one user's write land on another's row"
    )

    still_mine = await auth_client.get(case.path)
    assert still_mine.json() == mine.json()


async def test_another_users_watchlist_is_404_not_403(
    auth_client: AsyncClient,
    other_users_rows: dict[str, str],
) -> None:
    """403 would confirm the row exists, which is the leak we are avoiding.

    The harness above accepts either status because some resources' existence
    is not secret. A watchlist's is: knowing an id is valid tells you something.
    """
    response = await auth_client.get(f"/api/v1/watchlists/{other_users_rows['watchlist_id']}")

    assert response.status_code == 404


async def test_another_users_watchlist_is_absent_from_the_listing(
    auth_client: AsyncClient,
    other_users_rows: dict[str, str],
) -> None:
    response = await auth_client.get("/api/v1/watchlists")

    assert response.status_code == 200
    assert other_users_rows["watchlist_id"] not in {w["id"] for w in response.json()}


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
