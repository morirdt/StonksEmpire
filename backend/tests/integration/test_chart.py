"""The two chart reads and the preferences resource.

Phase 3 adds no new backend concepts, so these tests are mostly about the
contract the chart depends on: that bars and indicators for one window come
back as the *same dates in the same order*, because the frontend aligns them
positionally and a one-row skew would draw every indicator against the wrong
candle without erroring anywhere.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.market_data.fake import FakeMarketDataProvider
from app.models.chart_preferences import UserChartPreferences
from app.models.symbol import Symbol
from app.services.chart_service import MAX_BARS
from app.services.market_data_service import MarketDataService

pytestmark = pytest.mark.integration

#: Wide enough that MAX has more rows than the cap allows, so the cap is
#: actually exercised rather than merely configured.
BACKFILL_DAYS = 1000


@pytest.fixture
async def charted_symbol(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> Symbol:
    """One symbol with a real bar and indicator history behind it."""
    symbol = seeded_symbols[0]
    service = MarketDataService(db_session, provider=FakeMarketDataProvider())
    await service.backfill_symbol(symbol, days=BACKFILL_DAYS)
    return symbol


# ---------------------------------------------------------------------- bars


async def test_bars_come_back_oldest_first(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
) -> None:
    response = await auth_client.get(f"/api/v1/symbols/{charted_symbol.ticker}/bars")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == charted_symbol.ticker

    dates = [bar["trade_date"] for bar in body["bars"]]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))


async def test_prices_cross_the_wire_as_strings(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
) -> None:
    """A JSON number would be parsed back into a float and lose the cents."""
    response = await auth_client.get(f"/api/v1/symbols/{charted_symbol.ticker}/bars")

    first = response.json()["bars"][0]
    assert isinstance(first["close"], str)
    assert isinstance(first["volume"], int), "volume is a count, not a price"


async def test_a_symbol_with_no_history_is_an_empty_window_not_an_error(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """The common case when clicking around a seeded-but-unbackfilled universe."""
    response = await auth_client.get(f"/api/v1/symbols/{seeded_symbols[1].ticker}/bars")

    assert response.status_code == 200
    assert response.json()["bars"] == []


@pytest.mark.parametrize("resource", ["bars", "indicators"])
async def test_an_unknown_ticker_is_404(auth_client: AsyncClient, resource: str) -> None:
    """ "No such symbol" and "no history yet" are different answers."""
    response = await auth_client.get(f"/api/v1/symbols/NOSUCHTICKER/{resource}")

    assert response.status_code == 404


@pytest.mark.parametrize("resource", ["bars", "indicators"])
async def test_a_range_together_with_explicit_dates_is_422(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
    resource: str,
) -> None:
    response = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/{resource}",
        params={"range": "1Y", "start": "2025-01-01", "end": "2025-06-01"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("resource", ["bars", "indicators"])
async def test_an_unknown_range_is_422(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
    resource: str,
) -> None:
    response = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/{resource}",
        params={"range": "10Y"},
    )

    assert response.status_code == 422


async def test_a_narrow_range_returns_fewer_bars_than_a_wide_one(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
) -> None:
    async def count(chart_range: str) -> int:
        response = await auth_client.get(
            f"/api/v1/symbols/{charted_symbol.ticker}/bars",
            params={"range": chart_range},
        )
        assert response.status_code == 200, response.text
        return len(response.json()["bars"])

    assert await count("1M") < await count("6M") < await count("1Y")


async def test_max_is_capped_rather_than_paginated(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
) -> None:
    """There is no case where a chart wants more rows than a screen can plot."""
    response = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/bars",
        params={"range": "MAX"},
    )

    assert len(response.json()["bars"]) == MAX_BARS


async def test_the_cap_keeps_the_most_recent_bars(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
) -> None:
    """Truncating from the wrong end would show a chart that stops months ago."""
    capped = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/bars",
        params={"range": "MAX"},
    )
    recent = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/bars",
        params={"range": "1M"},
    )

    assert capped.json()["bars"][-1] == recent.json()["bars"][-1]


# ---------------------------------------------------------------- indicators


async def test_bars_and_indicators_share_the_same_dates_in_the_same_order(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
) -> None:
    """The chart aligns them positionally, so a skew here is invisible and wrong."""
    bars = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/bars", params={"range": "1Y"}
    )
    indicators = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/indicators", params={"range": "1Y"}
    )

    assert [b["trade_date"] for b in bars.json()["bars"]] == [
        i["trade_date"] for i in indicators.json()["indicators"]
    ]


async def test_the_two_endpoints_agree_under_the_row_cap_too(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
) -> None:
    """The cap truncates both, so it has to truncate both from the same end."""
    bars = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/bars", params={"range": "MAX"}
    )
    indicators = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/indicators", params={"range": "MAX"}
    )

    assert [b["trade_date"] for b in bars.json()["bars"]] == [
        i["trade_date"] for i in indicators.json()["indicators"]
    ]


async def test_a_leading_indicator_value_is_null_not_zero(
    auth_client: AsyncClient,
    charted_symbol: Symbol,
) -> None:
    """A zero plots a line to the bottom of the chart and looks like a crash."""
    response = await auth_client.get(
        f"/api/v1/symbols/{charted_symbol.ticker}/indicators",
        params={"range": "MAX"},
    )

    rows = response.json()["indicators"]
    assert rows[0]["sma_200"] is None
    assert any(row["sma_200"] is not None for row in rows), "…but not null forever"


# --------------------------------------------------------------- preferences


async def test_preferences_return_defaults_before_anything_is_saved(
    auth_client: AsyncClient,
) -> None:
    """A client that must special-case "unset" will get it wrong on first load."""
    response = await auth_client.get("/api/v1/me/chart-preferences")

    assert response.status_code == 200
    body = response.json()
    assert body["default_range"] == "1Y"
    assert body["active_overlays"] == ["sma_20", "sma_50"]
    assert body["active_oscillators"] == ["rsi_14"]


async def test_a_put_round_trips(auth_client: AsyncClient) -> None:
    saved = await auth_client.put(
        "/api/v1/me/chart-preferences",
        json={
            "default_range": "6M",
            "active_overlays": ["sma_200"],
            "active_oscillators": ["macd"],
        },
    )
    assert saved.status_code == 200, saved.text

    reread = await auth_client.get("/api/v1/me/chart-preferences")
    assert reread.json() == saved.json()
    assert reread.json()["default_range"] == "6M"


async def test_a_second_put_replaces_rather_than_merges(auth_client: AsyncClient) -> None:
    """This is a PUT, not a PATCH: absent means "gone", not "leave alone"."""
    await auth_client.put(
        "/api/v1/me/chart-preferences",
        json={
            "default_range": "1Y",
            "active_overlays": ["sma_20", "sma_50"],
            "active_oscillators": ["rsi_14", "macd"],
        },
    )
    await auth_client.put(
        "/api/v1/me/chart-preferences",
        json={
            "default_range": "1M",
            "active_overlays": [],
            "active_oscillators": [],
        },
    )

    body = (await auth_client.get("/api/v1/me/chart-preferences")).json()
    assert body == {
        "default_range": "1M",
        "active_overlays": [],
        "active_oscillators": [],
    }


async def test_repeated_puts_never_produce_a_second_row(
    auth_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The primary key *is* the user id, which is what makes that unrepresentable."""
    for chart_range in ("1M", "3M", "6M"):
        response = await auth_client.put(
            "/api/v1/me/chart-preferences",
            json={
                "default_range": chart_range,
                "active_overlays": [],
                "active_oscillators": [],
            },
        )
        assert response.status_code == 200

    rows = (
        await db_session.execute(select(func.count()).select_from(UserChartPreferences))
    ).scalar_one()
    assert rows == 1


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"default_range": "1Y", "active_overlays": ["rsi_14"], "active_oscillators": []},
            id="an oscillator is not an overlay",
        ),
        pytest.param(
            {"default_range": "1Y", "active_overlays": ["ema_12"], "active_oscillators": []},
            id="a stored indicator the price pane does not offer",
        ),
        pytest.param(
            {"default_range": "1Y", "active_overlays": [], "active_oscillators": ["sma_20"]},
            id="an overlay is not an oscillator",
        ),
        pytest.param(
            {"default_range": "10Y", "active_overlays": [], "active_oscillators": []},
            id="an unknown range",
        ),
        pytest.param(
            {
                "default_range": "1Y",
                "active_overlays": ["sma_20", "sma_50", "sma_200", "sma_20"],
                "active_oscillators": [],
            },
            id="more overlays than the price pane can carry",
        ),
    ],
)
async def test_an_unstorable_preference_is_rejected(
    auth_client: AsyncClient,
    payload: dict[str, object],
) -> None:
    """JSONB enforces nothing, so the schema layer has to.

    Every case here would otherwise be stored happily and then fail to render
    months later, with nothing to point at.
    """
    response = await auth_client.put("/api/v1/me/chart-preferences", json=payload)

    assert response.status_code == 422


async def test_duplicate_keys_are_collapsed_rather_than_stored_twice(
    auth_client: AsyncClient,
) -> None:
    """Storing a duplicate would draw the same line twice, at the same place."""
    response = await auth_client.put(
        "/api/v1/me/chart-preferences",
        json={
            "default_range": "1Y",
            "active_overlays": ["sma_20", "sma_20"],
            "active_oscillators": [],
        },
    )

    assert response.status_code == 200
    assert response.json()["active_overlays"] == ["sma_20"]


@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_chart_preferences_require_authentication(
    client: AsyncClient,
    method: str,
) -> None:
    response = await client.request(
        method,
        "/api/v1/me/chart-preferences",
        json={"default_range": "1Y", "active_overlays": [], "active_oscillators": []},
    )

    assert response.status_code == 401
