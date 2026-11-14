"""The WebSocket: handshake, auth by first message, token lifetime, and fan-out.

Driven in-process through ``httpx-ws``'s ASGI transport, on the test's own event
loop, so the socket's auth sees the test's rolled-back users and the hub uses
the real test Redis. Hints are published through the same ``publish_event`` the
worker uses.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.routes.ws import AUTH_TIMEOUT_SECONDS
from app.core.config import get_settings
from app.core.events import publish_event
from app.core.security import create_access_token, decode_access_token
from app.db.session import get_session_factory
from app.main import create_app
from app.models.user import User
from tests.integration.conftest import VALID_PASSWORD, unique_email

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": get_settings().cors_origins[0]}
WS = "/api/v1/ws"


@pytest.fixture
async def ws_app(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[FastAPI]:
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    yield app
    await app.state.realtime.close()


@asynccontextmanager
async def connect(app: FastAPI, origin: dict[str, str] = ORIGIN) -> AsyncIterator[Any]:
    """A client and a socket, opened and closed inside the test's own task.

    Not a fixture on purpose: the transport opens an anyio task group, and
    pytest-asyncio may tear a fixture down in a different task from the one
    that set it up, which anyio refuses.
    """
    async with (
        AsyncClient(transport=ASGIWebSocketTransport(app), base_url="http://testserver") as http,
        aconnect_ws(WS, http, headers=origin) as ws,
    ):
        yield ws


async def _user(client: AsyncClient) -> tuple[uuid.UUID, str]:
    response = await client.post(
        "/api/v1/auth/register", json={"email": unique_email("ws"), "password": VALID_PASSWORD}
    )
    token = response.json()["access_token"]
    return decode_access_token(token), token


async def _next(ws: Any, within: float = 2.0) -> dict[str, Any]:
    message: dict[str, Any] = await ws.receive_json(timeout=within)
    return message


async def _closed_with(ws: Any, within: float = 3.0) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        await ws.receive_json(timeout=within)
    return closed.value.code


async def test_auth_by_first_message_then_ready(ws_app: FastAPI, client: AsyncClient) -> None:
    _, token = await _user(client)

    async with connect(ws_app) as ws:
        await ws.send_json({"type": "auth", "token": token})
        assert await _next(ws) == {"type": "ready"}


async def test_no_auth_message_in_time_is_4401(
    ws_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.v1.routes.ws.AUTH_TIMEOUT_SECONDS", 0.2)
    assert AUTH_TIMEOUT_SECONDS == 5.0, "the real window is five seconds"

    async with connect(ws_app) as ws:
        assert await _closed_with(ws) == 4401


async def test_a_bad_token_is_4401(ws_app: FastAPI) -> None:
    async with connect(ws_app) as ws:
        await ws.send_json({"type": "auth", "token": "not-a-jwt"})
        assert await _closed_with(ws) == 4401


async def test_a_foreign_origin_is_refused(ws_app: FastAPI) -> None:
    with pytest.raises(ExceptionGroup) as refused:
        async with connect(ws_app, {"Origin": "https://evil.example"}) as ws:
            await ws.receive_json(timeout=1)

    (closed,) = refused.value.exceptions
    assert isinstance(closed, WebSocketDisconnect)
    assert closed.code == 1008


async def test_the_socket_closes_when_its_token_expires(
    ws_app: FastAPI, client: AsyncClient
) -> None:
    user_id, _ = await _user(client)
    # Issued 15 minutes minus 1 second ago, so it has one second left.
    short, _ = create_access_token(
        user_id, now=datetime.now(UTC) - get_settings().auth.access_token_ttl + timedelta(seconds=1)
    )

    async with connect(ws_app) as ws:
        await ws.send_json({"type": "auth", "token": short})
        assert await _next(ws) == {"type": "ready"}
        assert await _closed_with(ws, within=4) == 4401


async def test_a_re_auth_before_expiry_keeps_the_socket_open(
    ws_app: FastAPI, client: AsyncClient
) -> None:
    user_id, fresh = await _user(client)
    short, _ = create_access_token(
        user_id, now=datetime.now(UTC) - get_settings().auth.access_token_ttl + timedelta(seconds=1)
    )

    async with connect(ws_app) as ws:
        await ws.send_json({"type": "auth", "token": short})
        assert await _next(ws) == {"type": "ready"}
        await ws.send_json({"type": "auth", "token": fresh})
        assert await _next(ws) == {"type": "ready"}

        await asyncio.sleep(1.5)  # past the first token's expiry
        await publish_event({"type": "quotes_updated", "fetched_at": "x", "tickers": ["AAPL"]})
        assert (await _next(ws))["type"] == "quotes_updated", "still open on the newer token"


async def test_a_deactivated_user_is_closed_at_the_next_re_auth(
    ws_app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id, token = await _user(client)

    async with connect(ws_app) as ws:
        await ws.send_json({"type": "auth", "token": token})
        assert await _next(ws) == {"type": "ready"}

        await db_session.execute(update(User).where(User.id == user_id).values(is_active=False))
        await db_session.commit()
        await ws.send_json({"type": "auth", "token": token})

        assert await _closed_with(ws) == 4401


async def test_a_socket_cannot_change_user(ws_app: FastAPI, client: AsyncClient) -> None:
    _, mine = await _user(client)
    _, theirs = await _user(client)

    async with connect(ws_app) as ws:
        await ws.send_json({"type": "auth", "token": mine})
        await _next(ws)
        await ws.send_json({"type": "auth", "token": theirs})
        assert await _closed_with(ws) == 4401


async def test_quotes_reach_everyone_and_notifications_only_their_owner(
    ws_app: FastAPI, client: AsyncClient
) -> None:
    """User A's socket never receives user B's notification_created."""
    _, token_a = await _user(client)
    user_b, token_b = await _user(client)

    async with (
        connect(ws_app) as a,
        connect(ws_app) as b,
    ):
        # One at a time: in tests every session shares the one rolled-back
        # connection, and two concurrent auths would interleave its savepoints.
        await a.send_json({"type": "auth", "token": token_a})
        await _next(a)
        await b.send_json({"type": "auth", "token": token_b})
        await _next(b)

        notification = {
            "type": "notification_created",
            "user_id": str(user_b),
            "notification_id": str(uuid.uuid7()),
        }
        await publish_event(notification)
        await publish_event({"type": "quotes_updated", "fetched_at": "x", "tickers": ["MSFT"]})

        assert await _next(b) == notification
        assert (await _next(b))["type"] == "quotes_updated"
        # Messages arrive in publish order, so if A had been sent B's
        # notification it would be the first thing here.
        assert (await _next(a))["type"] == "quotes_updated"


async def test_with_redis_down_the_socket_says_try_again(
    ws_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket with no subscription behind it would stop the client polling for nothing."""
    monkeypatch.setattr(ws_app.state.realtime, "_redis_url", "redis://127.0.0.1:1/0")

    async with connect(ws_app) as ws:
        assert await _closed_with(ws) == 1013
