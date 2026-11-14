"""Shared test fixtures.

Environment defaults are set at import time, before anything imports
``app.core.config`` — settings are cached on first access, so this module must
win the race.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("SECRET_KEY", "test-secret-key-long-enough-for-validation-rules")
os.environ.setdefault("DB__HOST", "localhost")
os.environ.setdefault("DB__PORT", "5433")
os.environ.setdefault("DB__USER", "stonks")
os.environ.setdefault("DB__PASSWORD", "stonks")
os.environ.setdefault("DB__NAME", "stonks_empire_test")
# Database 15, never the dev stack's 0: the integration suite deletes keys.
os.environ.setdefault("REDIS__URL", "redis://localhost:6379/15")

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.redis import close_redis, redis_guard
from app.main import create_app


@pytest.fixture(autouse=True)
async def _fresh_redis_client() -> AsyncGenerator[None]:
    """Close the cached Redis client after every test, and forget any outage.

    A redis.asyncio connection belongs to the event loop that opened it, and
    each test gets a new loop. A client cached across tests fails on its next
    command with "attached to a different loop".
    """
    redis_guard.reset()
    yield
    await close_redis()
    redis_guard.reset()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    """An in-process HTTP client.

    ASGITransport does not run the lifespan, which keeps unit tests off the
    database. Tests needing a real session use the integration fixtures.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
