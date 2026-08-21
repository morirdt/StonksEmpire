from __future__ import annotations

import pytest
from httpx import AsyncClient

import app.api.health as health_module
from app.core.config import get_settings


async def test_health_reports_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == get_settings().environment
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
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] is True
    assert body["degraded"] == []


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


async def test_a_degraded_provider_is_reported_but_stays_ready(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A third party having a bad afternoon must not take the app out of rotation.

    Getting this backwards is how one provider outage becomes a full outage: the
    orchestrator pulls every pod because a dependency the app can serve without
    is failing.
    """

    async def ok() -> bool:
        return True

    monkeypatch.setattr(health_module, "check_database", ok)
    monkeypatch.setattr(health_module, "market_data_is_healthy", lambda: False)

    response = await client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["market_data"] is False
    assert body["degraded"] == ["market_data"]


async def test_a_lost_database_is_still_fatal(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The soft-failure path must not have made every check optional."""

    async def down() -> bool:
        return False

    monkeypatch.setattr(health_module, "check_database", down)
    monkeypatch.setattr(health_module, "market_data_is_healthy", lambda: True)

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
