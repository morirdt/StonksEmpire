"""Push hints, published to Redis for the WebSocket fan-out to deliver.

**A push is a hint, not the data.** Every event says *that* something changed,
and the client refetches over REST to find out *what*. So there is one path the
data takes, and a dropped message costs a few seconds rather than a lost
notification.

Publishing therefore never fails its caller. It runs after the commit that made
the change, and if Redis is unreachable the hint is dropped with a log line:
the next fetch, or the client's fallback polling, catches up.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

import structlog

from app.core.redis import REDIS_UNAVAILABLE, get_redis

__all__ = ["EVENTS_CHANNEL", "Publisher", "publish_event"]

logger = structlog.stdlib.get_logger(__name__)

#: The one pub/sub channel. Each API process subscribes once and routes by type.
EVENTS_CHANNEL: Final = "events"

type Publisher = Callable[[Mapping[str, Any]], Awaitable[None]]


async def publish_event(event: Mapping[str, Any]) -> None:
    """Publish ``event`` on :data:`EVENTS_CHANNEL`, or drop it if Redis is down."""
    try:
        await get_redis().publish(EVENTS_CHANNEL, json.dumps(event, default=str))
    except REDIS_UNAVAILABLE as exc:
        logger.warning("event_dropped", type=event.get("type"), error=str(exc))
