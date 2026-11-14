"""The WebSocket fan-out: one Redis subscription per API process.

Each process subscribes to the ``events`` channel **once**, not once per socket,
and routes every message to its own connections. There are two message types,
and both are invalidation hints rather than data:

- ``quotes_updated`` goes to every connection. It carries tickers and no
  prices. A client showing one of those tickers refetches through REST, so
  the grid cannot disagree with the endpoint.
- ``notification_created`` goes to that user's connections **only**. Routing it
  anywhere else is a cross-user leak like any other, and it is tested as one.

**A socket without a subscription behind it would be a lie.** The client stops
polling while it is connected, so if pub/sub is down the grid would freeze with
nothing to tell anyone. So when the subscription cannot start, a new socket is
closed with 1013 ("try again later"). When an established subscription drops,
every socket is closed the same way. Either way the client sees the drop and
goes back to polling.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import structlog
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.core.config import get_settings
from app.core.events import EVENTS_CHANNEL
from app.core.redis import REDIS_UNAVAILABLE

__all__ = ["Connection", "RealtimeHub"]

logger = structlog.stdlib.get_logger(__name__)

#: Per-connection buffer. Pushes are hints, so a client this far behind loses
#: the oldest ones and catches up on its next fetch rather than holding memory.
_QUEUE_SIZE: Final = 256

#: Put on a connection's queue to tell its handler to close with this code.
CLOSE_TRY_AGAIN: Final = 1013


@dataclass(eq=False)
class Connection:
    """One authenticated socket, as the hub sees it."""

    user_id: uuid.UUID
    expires_at: datetime
    queue: asyncio.Queue[dict[str, Any] | int] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_SIZE)
    )

    def offer(self, item: dict[str, Any] | int) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            logger.warning("ws_push_dropped", user_id=str(self.user_id))


class RealtimeHub:
    """The process's one subscription, and the connections it routes to.

    Built per app (``app.state.realtime``) and started lazily by the first
    socket, so it always runs on the loop that serves the sockets.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or get_settings().redis.url
        self._connections: set[Connection] = set()
        self._task: asyncio.Task[None] | None = None
        self._client: Redis | None = None
        self._lock = asyncio.Lock()

    @property
    def connections(self) -> int:
        return len(self._connections)

    def register(self, connection: Connection) -> None:
        self._connections.add(connection)

    def unregister(self, connection: Connection) -> None:
        self._connections.discard(connection)

    async def ensure_listening(self) -> bool:
        """Start the subscription if it is not running. False if Redis is down."""
        async with self._lock:
            if self._task is not None and not self._task.done():
                return True
            # A dedicated client with no read timeout: a subscriber spends its
            # life blocked on a read, which the shared client's 0.5 s timeout
            # would turn into an error every half second.
            client = Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=get_settings().redis.timeout_seconds,
                health_check_interval=30,
                retry=Retry(NoBackoff(), retries=0),
            )
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.subscribe(EVENTS_CHANNEL)
            except REDIS_UNAVAILABLE as exc:
                logger.warning("ws_subscription_unavailable", error=str(exc))
                await pubsub.aclose()  # type: ignore[no-untyped-call]
                await client.aclose()
                return False
            self._client = client
            self._task = asyncio.create_task(self._listen(pubsub), name="realtime-hub")
            logger.info("ws_subscription_started")
            return True

    async def _listen(self, pubsub: PubSub) -> None:
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    self.route(json.loads(message["data"]))
                except (ValueError, TypeError, KeyError):
                    logger.warning("ws_bad_event", data=str(message.get("data"))[:200])
        except REDIS_UNAVAILABLE as exc:
            logger.warning("ws_subscription_lost", error=str(exc), connections=self.connections)
            for connection in list(self._connections):
                connection.offer(CLOSE_TRY_AGAIN)
        finally:
            with contextlib.suppress(*REDIS_UNAVAILABLE):
                await pubsub.aclose()  # type: ignore[no-untyped-call]

    def route(self, event: dict[str, Any]) -> None:
        """Deliver one event to the connections that may see it."""
        kind = event.get("type")
        if kind == "quotes_updated":
            for connection in self._connections:
                connection.offer(event)
        elif kind == "notification_created":
            owner = uuid.UUID(str(event["user_id"]))
            for connection in self._connections:
                if connection.user_id == owner:
                    connection.offer(event)

    async def close(self) -> None:
        """Stop the subscription. Called at shutdown, and by tests."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            with contextlib.suppress(*REDIS_UNAVAILABLE):
                await self._client.aclose()
            self._client = None
