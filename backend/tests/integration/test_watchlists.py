"""Watchlist CRUD, ordering, and the quote grid.

Cross-user scoping lives in ``test_cross_user_authorization.py``; this file is
about the behaviour a single user sees.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import LatestQuote
from app.models.symbol import Symbol

pytestmark = pytest.mark.integration


async def _create(client: AsyncClient, name: str, **extra: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/watchlists", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def _add(client: AsyncClient, watchlist_id: str, ticker: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        json={"ticker": ticker},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------- CRUD


async def test_a_new_account_has_no_watchlists(auth_client: AsyncClient) -> None:
    """The empty state is the first thing a new user sees, so it must be real."""
    response = await auth_client.get("/api/v1/watchlists")

    assert response.status_code == 200
    assert response.json() == []


async def test_create_and_read_back(auth_client: AsyncClient) -> None:
    created = await _create(auth_client, "Tech")

    response = await auth_client.get(f"/api/v1/watchlists/{created['id']}")

    assert response.status_code == 200
    assert response.json()["name"] == "Tech"
    assert response.json()["item_count"] == 0


async def test_duplicate_names_conflict(auth_client: AsyncClient) -> None:
    await _create(auth_client, "Tech")

    response = await auth_client.post("/api/v1/watchlists", json={"name": "Tech"})

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


async def test_two_users_may_both_have_the_same_name(
    client: AsyncClient,
    auth_client: AsyncClient,
) -> None:
    """Names are unique per user, not globally."""
    await _create(auth_client, "Earnings plays")

    from tests.integration.conftest import VALID_PASSWORD, unique_email

    email = unique_email("second")
    registered = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD},
    )
    token = registered.json()["access_token"]

    response = await client.post(
        "/api/v1/watchlists",
        json={"name": "Earnings plays"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201


async def test_rename(auth_client: AsyncClient) -> None:
    created = await _create(auth_client, "Tech")

    response = await auth_client.patch(
        f"/api/v1/watchlists/{created['id']}",
        json={"name": "Megacaps"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Megacaps"


async def test_delete(auth_client: AsyncClient) -> None:
    created = await _create(auth_client, "Tech")

    assert (await auth_client.delete(f"/api/v1/watchlists/{created['id']}")).status_code == 204
    assert (await auth_client.get(f"/api/v1/watchlists/{created['id']}")).status_code == 404


async def test_a_missing_watchlist_is_404(auth_client: AsyncClient) -> None:
    response = await auth_client.get("/api/v1/watchlists/00000000-0000-7000-8000-000000000000")

    assert response.status_code == 404


async def test_only_one_default_at_a_time(auth_client: AsyncClient) -> None:
    """Promoting a list demotes whichever one held the flag."""
    first = await _create(auth_client, "First", is_default=True)
    second = await _create(auth_client, "Second", is_default=True)

    listing = {w["id"]: w for w in (await auth_client.get("/api/v1/watchlists")).json()}

    assert listing[second["id"]]["is_default"] is True
    assert listing[first["id"]]["is_default"] is False


# --------------------------------------------------------------------- items


async def test_add_a_symbol(auth_client: AsyncClient, seeded_symbols: list[Symbol]) -> None:
    watchlist = await _create(auth_client, "Tech")

    item = await _add(auth_client, watchlist["id"], "AAPL")

    assert item["position"] == 0
    detail = await auth_client.get(f"/api/v1/watchlists/{watchlist['id']}")
    assert detail.json()["item_count"] == 1


async def test_tickers_are_case_insensitive(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    watchlist = await _create(auth_client, "Tech")

    response = await auth_client.post(
        f"/api/v1/watchlists/{watchlist['id']}/items",
        json={"ticker": "aapl"},
    )

    assert response.status_code == 201


async def test_the_same_symbol_twice_conflicts(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    watchlist = await _create(auth_client, "Tech")
    await _add(auth_client, watchlist["id"], "AAPL")

    response = await auth_client.post(
        f"/api/v1/watchlists/{watchlist['id']}/items",
        json={"ticker": "AAPL"},
    )

    assert response.status_code == 409


async def test_an_unknown_ticker_is_404(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    watchlist = await _create(auth_client, "Tech")

    response = await auth_client.post(
        f"/api/v1/watchlists/{watchlist['id']}/items",
        json={"ticker": "NOTREAL"},
    )

    assert response.status_code == 404


async def test_positions_are_assigned_in_order(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    watchlist = await _create(auth_client, "Tech")

    positions = [
        (await _add(auth_client, watchlist["id"], t))["position"] for t in ("AAPL", "MSFT", "SPY")
    ]

    assert positions == [0, 1, 2]


async def test_removing_an_item_compacts_the_rest(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """Leaving a hole makes the next insert collide or sort strangely."""
    watchlist = await _create(auth_client, "Tech")
    items = [await _add(auth_client, watchlist["id"], t) for t in ("AAPL", "MSFT", "SPY")]

    removed = await auth_client.delete(
        f"/api/v1/watchlists/{watchlist['id']}/items/{items[0]['id']}"
    )
    assert removed.status_code == 204

    grid = await auth_client.get(f"/api/v1/watchlists/{watchlist['id']}/quotes")
    assert [row["item"]["position"] for row in grid.json()["rows"]] == [0, 1]


async def test_removing_a_missing_item_is_404(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    watchlist = await _create(auth_client, "Tech")

    response = await auth_client.delete(
        f"/api/v1/watchlists/{watchlist['id']}/items/00000000-0000-7000-8000-000000000000"
    )

    assert response.status_code == 404


# ------------------------------------------------------------------ reorder


async def test_reorder_sets_the_requested_order(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    watchlist = await _create(auth_client, "Tech")
    items = [await _add(auth_client, watchlist["id"], t) for t in ("AAPL", "MSFT", "SPY")]
    reversed_ids = [i["id"] for i in reversed(items)]

    response = await auth_client.patch(
        f"/api/v1/watchlists/{watchlist['id']}/items",
        json={"item_ids": reversed_ids},
    )

    assert response.status_code == 200
    assert [i["id"] for i in response.json()] == reversed_ids
    assert [i["position"] for i in response.json()] == [0, 1, 2]


async def test_a_partial_reorder_is_rejected(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """Omitting items has no well-defined answer, so it is a 422 rather than a guess."""
    watchlist = await _create(auth_client, "Tech")
    items = [await _add(auth_client, watchlist["id"], t) for t in ("AAPL", "MSFT", "SPY")]

    response = await auth_client.patch(
        f"/api/v1/watchlists/{watchlist['id']}/items",
        json={"item_ids": [items[0]["id"]]},
    )

    assert response.status_code == 422


async def test_a_reorder_naming_a_foreign_item_is_rejected(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    watchlist = await _create(auth_client, "Tech")
    items = [await _add(auth_client, watchlist["id"], t) for t in ("AAPL", "MSFT")]

    response = await auth_client.patch(
        f"/api/v1/watchlists/{watchlist['id']}/items",
        json={"item_ids": [items[0]["id"], "00000000-0000-7000-8000-000000000000"]},
    )

    assert response.status_code == 422


# --------------------------------------------------------------------- grid


async def test_the_grid_returns_a_row_per_item(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    watchlist = await _create(auth_client, "Tech")
    for ticker in ("AAPL", "MSFT"):
        await _add(auth_client, watchlist["id"], ticker)

    response = await auth_client.get(f"/api/v1/watchlists/{watchlist['id']}/quotes")

    assert response.status_code == 200
    body = response.json()
    assert [row["symbol"]["ticker"] for row in body["rows"]] == ["AAPL", "MSFT"]
    assert body["watchlist"]["name"] == "Tech"


async def test_the_grid_prices_its_rows(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """The grid is what the frontend polls, so it has to carry prices itself."""
    watchlist = await _create(auth_client, "Tech")
    await _add(auth_client, watchlist["id"], "AAPL")

    response = await auth_client.get(f"/api/v1/watchlists/{watchlist['id']}/quotes")

    quote = response.json()["rows"][0]["quote"]
    assert quote is not None
    # Prices cross the wire as strings so no JavaScript client can float them.
    assert isinstance(quote["price"], str)
    assert float(quote["price"]) > 0
    assert quote["fetched_at"]


async def test_an_empty_grid_is_an_empty_list_not_an_error(auth_client: AsyncClient) -> None:
    watchlist = await _create(auth_client, "Tech")

    response = await auth_client.get(f"/api/v1/watchlists/{watchlist['id']}/quotes")

    assert response.status_code == 200
    assert response.json()["rows"] == []


# ----------------------------------------------------------------- auth gate


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/watchlists"),
        ("POST", "/api/v1/watchlists"),
        ("GET", "/api/v1/watchlists/00000000-0000-7000-8000-000000000000"),
        ("GET", "/api/v1/symbols?search=AAPL"),
        ("GET", "/api/v1/quotes?tickers=AAPL"),
    ],
)
async def test_every_endpoint_requires_authentication(
    client: AsyncClient,
    method: str,
    path: str,
) -> None:
    response = await client.request(method, path, json={"name": "x"})

    assert response.status_code == 401


async def test_the_grid_shows_refreshed_prices_not_the_ones_it_joined(
    db_session: AsyncSession,
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """Polling has to actually move the prices.

    The grid joins in whatever quote is stored, then refreshes anything past
    its TTL. Those two steps touch the same row, so a session that served the
    joined copy from its identity map would render the stale price forever and
    look exactly like a provider that had stopped updating.
    """
    watchlist = await _create(auth_client, "Tech")
    await _add(auth_client, watchlist["id"], "AAPL")
    await auth_client.get(f"/api/v1/watchlists/{watchlist['id']}/quotes")

    aged = datetime.now(UTC) - timedelta(hours=1)
    await db_session.execute(update(LatestQuote).values(fetched_at=aged))
    await db_session.commit()

    response = await auth_client.get(f"/api/v1/watchlists/{watchlist['id']}/quotes")

    fetched_at = datetime.fromisoformat(response.json()["rows"][0]["quote"]["fetched_at"])
    assert fetched_at > datetime.now(UTC) - timedelta(minutes=1)
