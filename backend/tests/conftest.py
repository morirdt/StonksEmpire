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

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


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
