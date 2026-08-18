from __future__ import annotations

import pytest
from httpx import AsyncClient

import app.api.health as health_module


async def test_health_reports_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"
    assert body["version"]


async def test_health_does_not_touch_the_database(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Liveness must stay green even when Postgres is unreachable."""

    async def boom() -> bool:
        raise AssertionError("liveness probe must not query the database")

    monkeypatch.setattr(health_module, "check_database", boom)
    assert (await client.get("/health")).status_code == 200


async def test_ready_returns_200_when_database_is_up(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ok() -> bool:
        return True

    monkeypatch.setattr(health_module, "check_database", ok)

    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": True}}


async def test_ready_returns_503_when_database_is_down(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down() -> bool:
        return False

    monkeypatch.setattr(health_module, "check_database", down)

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] is False


async def test_correlation_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


async def test_correlation_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers.get("X-Request-ID")
