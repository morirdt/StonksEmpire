"""The worker container's healthcheck: ``python -m app.jobs.healthcheck``.

Exits 0 when the heartbeat is fresh, 1 otherwise. It reads the same key, with
the same age limit, as ``/ready``, so Compose and the API can never disagree
about whether the worker is alive.
"""

from __future__ import annotations

import asyncio

from app.core.redis import close_redis
from app.jobs.heartbeat import worker_is_alive


async def _check() -> bool:
    try:
        return await worker_is_alive()
    finally:
        await close_redis()


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(_check()) else 1)
