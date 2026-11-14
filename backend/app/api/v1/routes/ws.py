"""``GET /api/v1/ws``: one socket per tab, authenticated after it opens.

A browser cannot set an ``Authorization`` header on a WebSocket, and a token in
the query string ends up in every access log between the browser and the app.
So:

1. **The ``Origin`` is checked** against ``cors_origins`` before the upgrade,
   and anything else is refused (1008). CORS does not apply to WebSockets. The
   auth step already stops cross-site hijacking, but the check costs one line.
2. **The first message must be** ``{"type": "auth", "token": "<access>"}``,
   sent within 5 seconds. Otherwise the server closes with **4401**.
3. The token is validated **exactly as ``CurrentUser`` validates it**, through
   the same function, including whether the account is still active.
4. **The connection lives only as long as the token.** At expiry the server
   closes with 4401, unless the client sent a newer token on the same socket
   before then, which it does after every refresh. So deactivating an account
   still takes effect within one access-token lifetime, socket or not.

On 4401 the client goes through the single in-flight refresh in
``lib/api/client.ts`` and reconnects. It never refreshes on its own.

Messages to the client: ``{"type": "ready"}`` after each successful auth, then
the hints described in ``app/api/realtime.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Annotated, Any, Final

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import authenticate_token
from app.api.realtime import CLOSE_TRY_AGAIN, Connection, RealtimeHub
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.db.session import get_session_factory

router = APIRouter(tags=["realtime"])

logger = structlog.stdlib.get_logger(__name__)

#: Application close codes, in the 4000-4999 range RFC 6455 leaves to apps.
#: 4401 mirrors HTTP 401: authenticate (refresh, then reconnect) and try again.
CLOSE_UNAUTHENTICATED: Final = 4401
CLOSE_POLICY: Final = 1008
CLOSE_UNSUPPORTED: Final = 1003

AUTH_TIMEOUT_SECONDS: Final = 5.0

Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


async def _authenticate(sessions: async_sessionmaker[AsyncSession], message: Any) -> Any:
    """``(user, claims)`` for an auth message, or raise ``AuthenticationError``."""
    if not isinstance(message, dict) or message.get("type") != "auth":
        raise AuthenticationError("Expected an auth message.")
    token = message.get("token")
    if not isinstance(token, str):
        raise AuthenticationError("Missing token.")
    async with sessions() as session:
        return await authenticate_token(session, token)


@router.websocket("/ws")
async def events_socket(websocket: WebSocket, sessions: Sessions) -> None:
    if websocket.headers.get("origin") not in get_settings().cors_origins:
        await websocket.close(code=CLOSE_POLICY)
        return

    hub: RealtimeHub = websocket.app.state.realtime
    await websocket.accept()
    if not await hub.ensure_listening():
        # No subscription, so no pushes: tell the client to keep polling.
        await websocket.close(code=CLOSE_TRY_AGAIN)
        return

    try:
        first = await asyncio.wait_for(websocket.receive_json(), AUTH_TIMEOUT_SECONDS)
        user, claims = await _authenticate(sessions, first)
    except (TimeoutError, AuthenticationError, ValueError):
        await websocket.close(code=CLOSE_UNAUTHENTICATED)
        return
    except WebSocketDisconnect:
        return

    connection = Connection(user_id=user.id, expires_at=claims.expires_at)
    hub.register(connection)
    try:
        await websocket.send_json({"type": "ready"})
        await _serve(websocket, connection, sessions)
    finally:
        hub.unregister(connection)


async def _serve(
    websocket: WebSocket,
    connection: Connection,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Relay hints out and accept re-auths in, until the token or the socket ends."""
    incoming = asyncio.ensure_future(websocket.receive_json())
    outgoing = asyncio.ensure_future(connection.queue.get())
    try:
        while True:
            remaining = (connection.expires_at - datetime.now(UTC)).total_seconds()
            done, _ = await asyncio.wait(
                {incoming, outgoing},
                timeout=max(remaining, 0.0),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await websocket.close(code=CLOSE_UNAUTHENTICATED, reason="token expired")
                return

            if outgoing in done:
                item = outgoing.result()
                if isinstance(item, int):
                    await websocket.close(code=item)
                    return
                await websocket.send_json(item)
                outgoing = asyncio.ensure_future(connection.queue.get())

            if incoming in done:
                try:
                    message = incoming.result()
                except WebSocketDisconnect:
                    return
                except ValueError:
                    await websocket.close(code=CLOSE_UNSUPPORTED)
                    return
                try:
                    user, claims = await _authenticate(sessions, message)
                except AuthenticationError:
                    await websocket.close(code=CLOSE_UNAUTHENTICATED)
                    return
                if user.id != connection.user_id:
                    # A socket belongs to one user for its whole life.
                    await websocket.close(code=CLOSE_UNAUTHENTICATED)
                    return
                connection.expires_at = claims.expires_at
                await websocket.send_json({"type": "ready"})
                incoming = asyncio.ensure_future(websocket.receive_json())
    finally:
        for task in (incoming, outgoing):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                await task
