from __future__ import annotations

from httpx import AsyncClient


async def test_meta_returns_api_identity(client: AsyncClient) -> None:
    response = await client.get("/api/v1/meta")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Stonks Empire"
    assert body["api_version"] == "v1"
    assert body["environment"] == "test"
