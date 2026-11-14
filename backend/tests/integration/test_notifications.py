"""The notification list, the unread count, and marking read.

Notifications are only ever written by the worker, so these tests produce them
the same way: three alerts on SPY, then a symbol refresh that delists SPY,
which disables each alert with a notification.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.jobs.tasks.market_data import refresh_symbols
from app.jobs.tasks.notifications import prune_notifications
from app.models.symbol import Symbol
from tests.integration.conftest import FrozenClock
from tests.integration.providers import UNIVERSE, Published, RecordingProvider

pytestmark = pytest.mark.integration

URL = "/api/v1/me/notifications"


async def make_notifications(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FrozenClock,
    count: int = 3,
) -> list[dict[str, Any]]:
    for i in range(count):
        created = await client.post(
            "/api/v1/alerts",
            json={
                "name": f"SPY {i}",
                "kind": "intraday",
                "ticker": "SPY",
                "condition": {"kind": "numeric", "field": "price", "op": "gt", "value": "1"},
            },
        )
        assert created.status_code == 201, created.text
    await refresh_symbols(
        session_factory,
        clock,
        provider=RecordingProvider(clock, [s for s in UNIVERSE if s.ticker != "SPY"]),
        max_deactivation_share=1.0,
        publish=Published(),
    )
    items: list[dict[str, Any]] = (await client.get(URL)).json()["items"]
    assert len(items) == count
    return items


@pytest.fixture
async def three(
    auth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> list[dict[str, Any]]:
    return await make_notifications(auth_client, session_factory, clock)


async def test_the_list_pages_by_cursor_newest_first(
    auth_client: AsyncClient, three: list[dict[str, Any]]
) -> None:
    first = (await auth_client.get(URL, params={"limit": 2})).json()
    second = (
        await auth_client.get(URL, params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()

    ids = [n["id"] for n in first["items"]] + [n["id"] for n in second["items"]]
    assert ids == [n["id"] for n in three], "two pages add up to the whole list, in order"
    assert second["next_cursor"] is None
    created = [n["created_at"] for n in three]
    assert created == sorted(created, reverse=True)


async def test_a_bad_cursor_is_a_422(auth_client: AsyncClient) -> None:
    response = await auth_client.get(URL, params={"cursor": "not-a-cursor"})
    assert response.status_code == 422


async def test_marking_read_is_idempotent_and_moves_the_count(
    auth_client: AsyncClient, three: list[dict[str, Any]]
) -> None:
    assert (await auth_client.get(f"{URL}/unread-count")).json() == {"count": 3}

    first = await auth_client.post(f"{URL}/{three[0]['id']}/read")
    again = await auth_client.post(f"{URL}/{three[0]['id']}/read")

    assert first.status_code == again.status_code == 200
    assert first.json()["read_at"] == again.json()["read_at"], "a repeat does not re-stamp it"
    assert (await auth_client.get(f"{URL}/unread-count")).json() == {"count": 2}
    unread = (await auth_client.get(URL, params={"unread": "true"})).json()["items"]
    assert three[0]["id"] not in {n["id"] for n in unread}


async def test_read_all_is_idempotent(
    auth_client: AsyncClient, three: list[dict[str, Any]]
) -> None:
    assert (await auth_client.post(f"{URL}/read-all")).json() == {"updated": 3}
    assert (await auth_client.post(f"{URL}/read-all")).json() == {"updated": 0}
    assert (await auth_client.get(f"{URL}/unread-count")).json() == {"count": 0}


async def test_a_notification_outlives_its_alert(
    auth_client: AsyncClient, three: list[dict[str, Any]]
) -> None:
    alert_id = three[0]["alert_id"]

    await auth_client.delete(f"/api/v1/alerts/{alert_id}")

    survivor = next(
        n for n in (await auth_client.get(URL)).json()["items"] if n["id"] == three[0]["id"]
    )
    assert survivor["alert_id"] is None
    assert survivor["payload"]["alert_id"] == alert_id, "the payload still says what it was about"


async def test_pruning_keeps_unread_and_recent_notifications(
    auth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FrozenClock,
    three: list[dict[str, Any]],
) -> None:
    await auth_client.post(f"{URL}/{three[0]['id']}/read")

    recent = await prune_notifications(session_factory, clock)
    clock.advance(days=400)
    old = await prune_notifications(session_factory, clock)

    assert recent.deleted == 0, "read, but not yet 90 days old"
    assert old.deleted == 1, "only the read one; unread is never pruned"
    assert (await auth_client.get(f"{URL}/unread-count")).json() == {"count": 2}
