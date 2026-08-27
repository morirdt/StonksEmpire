"""The screener over a real database.

The unit suite covers the compiler; this file covers the things that are only
true once there are rows: the shared ``as_of``, nulls failing a predicate
without vanishing from the universe, the pre-cap ``total_matched``, and the
preset lifecycle including a preset that has gone stale.

The universe here is built by hand rather than by the Fake provider, because
every assertion below depends on knowing exactly which symbol has how much
history — which is precisely what a random-walk generator will not tell you.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.market_data.base import Bar
from app.models.screener import ScreenerPreset
from app.models.symbol import Symbol
from app.models.user import User
from app.repositories.bar_repository import BarRepository
from app.services.indicator_service import compute_indicators

pytestmark = pytest.mark.integration

#: The date every symbol with a current series ends on. Fixed rather than
#: `date.today()` so the assertions do not depend on when the suite runs.
AS_OF = date(2026, 6, 30)

#: Long enough that sma_200 and the 52-week windows are populated.
LONG_SERIES = 300
#: Too short for sma_200 — which is the point of it.
SHORT_SERIES = 30

#: How far behind `AS_OF` the stale symbol's last bar sits.
STALE_BY_DAYS = 10


def _series(end: date, count: int, close: Decimal, step: Decimal = Decimal("0")) -> list[Bar]:
    """A gap-free series ending on ``end``, rising by ``step`` per bar."""
    start = end - timedelta(days=count - 1)
    bars: list[Bar] = []
    for i in range(count):
        price = close + step * i
        bars.append(
            Bar(
                trade_date=start + timedelta(days=i),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=1_000_000 + i,
                is_adjusted=True,
            )
        )
    return bars


async def _store(session: AsyncSession, symbol: Symbol, bars: list[Bar]) -> None:
    repository = BarRepository(session)
    await repository.upsert_bars(symbol.id, bars)
    await repository.upsert_indicators(symbol.id, compute_indicators(bars))
    await session.commit()


@pytest.fixture
async def screened_universe(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> dict[str, Symbol]:
    """Three symbols, each present for a different reason.

    * ``AAPL`` — a long, rising series. Every indicator is populated, and its
      close is above its 200-day average.
    * ``MSFT`` — a series too short for ``sma_200``, so that column is ``NULL``.
      It is in the universe and must fail an ``sma_200`` predicate on its merits.
    * ``SPY`` — a long series that stopped ten days before ``AS_OF``. It has no
      bar on the shared date, so it is outside the universe for every run.
    """
    apple, microsoft, spy = seeded_symbols

    await _store(db_session, apple, _series(AS_OF, LONG_SERIES, Decimal("100"), Decimal("1")))
    await _store(db_session, microsoft, _series(AS_OF, SHORT_SERIES, Decimal("50")))
    await _store(
        db_session,
        spy,
        _series(AS_OF - timedelta(days=STALE_BY_DAYS), LONG_SERIES, Decimal("400"), Decimal("1")),
    )
    return {"AAPL": apple, "MSFT": microsoft, "SPY": spy}


def _above_sma_200() -> dict:
    return {"kind": "compare", "left": "close", "op": "gt", "right": "sma_200"}


def _any_close() -> dict:
    return {"kind": "numeric", "field": "close", "op": "gt", "value": "0"}


# ------------------------------------------------------------------ catalogue


async def test_the_catalogue_lists_the_registry(auth_client: AsyncClient) -> None:
    response = await auth_client.get("/api/v1/screener/fields")

    assert response.status_code == 200
    body = response.json()
    keys = {f["key"] for f in body["fields"]}
    assert {"close", "sma_200", "rsi_14", "exchange", "asset_type"} <= keys
    assert body["default_sort"] == {"field": "ticker", "direction": "asc"}


async def test_the_catalogue_offers_the_exchanges_that_exist(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """Not a hard-coded list: the builder offers what the universe actually has."""
    response = await auth_client.get("/api/v1/screener/fields")

    exchange = next(f for f in response.json()["fields"] if f["key"] == "exchange")
    assert set(exchange["values"]) >= {s.exchange for s in seeded_symbols if s.exchange}


async def test_a_display_only_field_is_sortable_but_offers_no_filter_kinds(
    auth_client: AsyncClient,
) -> None:
    body = (await auth_client.get("/api/v1/screener/fields")).json()

    ticker = next(f for f in body["fields"] if f["key"] == "ticker")
    assert ticker["kinds"] == []
    assert "ticker" in body["sort_fields"]


async def test_the_catalogue_needs_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/screener/fields")).status_code == 401


# ------------------------------------------------------------------- running


async def test_a_run_reports_one_shared_as_of(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    response = await auth_client.post("/api/v1/screener/run", json={"filters": _any_close()})

    assert response.status_code == 200
    assert response.json()["as_of"] == AS_OF.isoformat()


async def test_a_symbol_whose_latest_bar_is_older_is_outside_the_universe(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    """Per-symbol latest would compare a symbol that stopped ingesting against
    one priced yesterday, and the result would look like a screen rather than
    like a bug."""
    body = (await auth_client.post("/api/v1/screener/run", json={"filters": _any_close()})).json()

    assert body["universe_size"] == 2
    assert "SPY" not in {row["ticker"] for row in body["rows"]}


async def test_a_null_indicator_fails_the_predicate_without_leaving_the_universe(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    """The behaviour that must not be "fixed" with COALESCE.

    MSFT has 30 bars, so its ``sma_200`` is NULL and ``close > sma_200`` is NULL
    rather than TRUE — it drops out of the screen. It is still counted in
    ``universe_size``, which is how the drop is made visible rather than silent.
    """
    body = (
        await auth_client.post("/api/v1/screener/run", json={"filters": _above_sma_200()})
    ).json()

    tickers = {row["ticker"] for row in body["rows"]}
    assert tickers == {"AAPL"}
    assert body["universe_size"] == 2
    assert body["total_matched"] == 1


async def test_a_null_fails_both_eq_and_neq(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    """`neq` is not the complement of `eq` in SQL, and pretending it is would
    require exactly the COALESCE this phase refuses."""
    matched = set()
    for op in ("eq", "neq"):
        body = (
            await auth_client.post(
                "/api/v1/screener/run",
                json={
                    "filters": {
                        "kind": "numeric",
                        "field": "sma_200",
                        "op": op,
                        "value": "12345",
                    }
                },
            )
        ).json()
        matched |= {row["ticker"] for row in body["rows"]}

    assert "MSFT" not in matched


async def test_total_matched_is_the_count_before_the_row_cap(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    """The signal that a screen is too loose, and the reason there is no pagination."""
    body = (
        await auth_client.post(
            "/api/v1/screener/run",
            json={"filters": _any_close(), "limit": 1},
        )
    ).json()

    assert body["total_matched"] == 2
    assert len(body["rows"]) == 1


async def test_total_matched_equals_the_row_count_when_the_cap_does_not_bite(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    body = (await auth_client.post("/api/v1/screener/run", json={"filters": _any_close()})).json()

    assert body["total_matched"] == len(body["rows"]) == 2


async def test_rows_carry_the_fields_the_run_referenced(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    """So a user can see *why* a symbol matched without opening it."""
    body = (
        await auth_client.post(
            "/api/v1/screener/run",
            json={
                "filters": _above_sma_200(),
                "sort": {"field": "rsi_14", "direction": "desc"},
            },
        )
    ).json()

    columns = [c["key"] for c in body["columns"]]
    assert columns[:3] == ["close", "volume", "change_percent_1d"]
    assert "sma_200" in columns and "rsi_14" in columns
    assert set(body["rows"][0]["values"]) == set(columns)


async def test_prices_cross_the_wire_as_strings(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    """A JSON number would be parsed back into a float by every JS client."""
    body = (await auth_client.post("/api/v1/screener/run", json={"filters": _any_close()})).json()

    assert isinstance(body["rows"][0]["values"]["close"], str)


async def test_sorting_puts_nulls_last_in_both_directions(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    """A symbol with no value for the sorted field is not the best match, and
    showing it at the top reads as one."""
    for direction in ("asc", "desc"):
        body = (
            await auth_client.post(
                "/api/v1/screener/run",
                json={
                    "filters": _any_close(),
                    "sort": {"field": "sma_200", "direction": direction},
                },
            )
        ).json()

        assert [row["ticker"] for row in body["rows"]] == ["AAPL", "MSFT"], direction


async def test_an_empty_universe_is_distinguishable_from_no_matches(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """The third empty state: symbols exist, but nothing has been backfilled.

    Without this the UI would diagnose an un-backfilled database as "no symbol
    matched", which is the one wrong answer a user cannot act on.
    """
    body = (await auth_client.post("/api/v1/screener/run", json={"filters": _any_close()})).json()

    assert body["as_of"] is None
    assert body["universe_size"] == 0
    assert body["rows"] == []


async def test_nesting_changes_the_result_set(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    """`a AND (b OR c)` and `(a AND b) OR c` must not return the same symbols."""
    a = {"kind": "numeric", "field": "close", "op": "gt", "value": "100"}
    b = {"kind": "numeric", "field": "close", "op": "lt", "value": "10"}
    c = {"kind": "numeric", "field": "close", "op": "lt", "value": "60"}

    async def run(tree: dict) -> set[str]:
        body = (await auth_client.post("/api/v1/screener/run", json={"filters": tree})).json()
        return {row["ticker"] for row in body["rows"]}

    and_over_or = await run(
        {
            "kind": "group",
            "op": "and",
            "children": [a, {"kind": "group", "op": "or", "children": [b, c]}],
        }
    )
    or_over_and = await run(
        {
            "kind": "group",
            "op": "or",
            "children": [{"kind": "group", "op": "and", "children": [a, b]}, c],
        }
    )

    assert and_over_or == set()
    assert or_over_and == {"MSFT"}


# ------------------------------------------------------------- rejected input


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"kind": "numeric", "field": "market_cap", "op": "gt", "value": "1"}, "unknown field"),
        ({"kind": "numeric", "field": "close", "op": "roughly", "value": "1"}, "unknown operator"),
        ({"kind": "compare", "left": "close", "op": "gt", "right": "volume"}, "unit mismatch"),
        ({"kind": "numeric", "field": "close", "op": "gt", "value": 1.5}, "json number"),
    ],
)
async def test_a_malformed_filter_is_a_422_from_the_schema(
    auth_client: AsyncClient,
    payload: dict,
    why: str,
) -> None:
    response = await auth_client.post("/api/v1/screener/run", json={"filters": payload})

    assert response.status_code == 422, why
    assert response.json()["code"] == "validation_error"


async def test_a_limit_past_the_cap_is_rejected(auth_client: AsyncClient) -> None:
    response = await auth_client.post(
        "/api/v1/screener/run",
        json={"filters": _any_close(), "limit": 5_000},
    )

    assert response.status_code == 422


async def test_running_needs_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/screener/run", json={"filters": _any_close()})

    assert response.status_code == 401


# -------------------------------------------------------------------- presets


async def test_a_preset_round_trips(auth_client: AsyncClient) -> None:
    created = await auth_client.post(
        "/api/v1/screener/presets",
        json={
            "name": "Above the 200",
            "filters": _above_sma_200(),
            "sort": {"field": "rsi_14", "direction": "desc"},
        },
    )
    assert created.status_code == 201, created.text

    fetched = await auth_client.get(f"/api/v1/screener/presets/{created.json()['id']}")

    assert fetched.status_code == 200
    assert fetched.json()["filters"] == created.json()["filters"]
    assert fetched.json()["sort"] == {"field": "rsi_14", "direction": "desc"}


async def test_a_duplicate_preset_name_is_a_409(auth_client: AsyncClient) -> None:
    body = {"name": "Momentum", "filters": _any_close()}
    assert (await auth_client.post("/api/v1/screener/presets", json=body)).status_code == 201

    assert (await auth_client.post("/api/v1/screener/presets", json=body)).status_code == 409


async def test_a_preset_can_be_renamed_and_refiltered(auth_client: AsyncClient) -> None:
    created = await auth_client.post(
        "/api/v1/screener/presets",
        json={"name": "First", "filters": _any_close()},
    )
    preset_id = created.json()["id"]

    patched = await auth_client.patch(
        f"/api/v1/screener/presets/{preset_id}",
        json={"name": "Second", "filters": _above_sma_200()},
    )

    assert patched.status_code == 200
    assert patched.json()["name"] == "Second"
    assert patched.json()["filters"]["kind"] == "compare"


async def test_a_patch_leaves_omitted_fields_alone(auth_client: AsyncClient) -> None:
    created = await auth_client.post(
        "/api/v1/screener/presets",
        json={"name": "Keep me", "filters": _above_sma_200()},
    )
    preset_id = created.json()["id"]

    patched = await auth_client.patch(
        f"/api/v1/screener/presets/{preset_id}",
        json={"name": "Renamed"},
    )

    assert patched.json()["filters"] == created.json()["filters"]


async def test_a_preset_can_be_deleted(auth_client: AsyncClient) -> None:
    created = await auth_client.post(
        "/api/v1/screener/presets",
        json={"name": "Temporary", "filters": _any_close()},
    )
    preset_id = created.json()["id"]

    assert (await auth_client.delete(f"/api/v1/screener/presets/{preset_id}")).status_code == 204
    assert (await auth_client.get(f"/api/v1/screener/presets/{preset_id}")).status_code == 404


async def test_presets_come_back_newest_first(auth_client: AsyncClient) -> None:
    for name in ("One", "Two", "Three"):
        await auth_client.post(
            "/api/v1/screener/presets",
            json={"name": name, "filters": _any_close()},
        )

    listing = await auth_client.get("/api/v1/screener/presets")

    assert [p["name"] for p in listing.json()] == ["Three", "Two", "One"]


async def test_running_a_preset_uses_what_was_saved(
    auth_client: AsyncClient,
    screened_universe: dict[str, Symbol],
) -> None:
    created = await auth_client.post(
        "/api/v1/screener/presets",
        json={"name": "Above the 200", "filters": _above_sma_200()},
    )

    run = await auth_client.post(f"/api/v1/screener/presets/{created.json()['id']}/run", json={})

    assert run.status_code == 200
    assert {row["ticker"] for row in run.json()["rows"]} == {"AAPL"}


# ---------------------------------------------------------------- stale trees


@pytest.fixture
async def stale_preset(db_session: AsyncSession, registered_user: dict[str, str]) -> ScreenerPreset:
    """A preset naming a field that does not exist.

    Written straight to the table, because the API would never accept it — which
    is the point: this is the row a *later* phase creates by removing a column,
    and the shape of it has to be handled before that phase happens.
    """
    user = (
        await db_session.execute(
            User.__table__.select().where(User.email == registered_user["email"])
        )
    ).one()
    preset = ScreenerPreset(
        user_id=user.id,
        name="Cheap on book value",
        filters={"kind": "numeric", "field": "price_to_book", "op": "lt", "value": "1"},
        sort_field="ticker",
        sort_direction="asc",
    )
    db_session.add(preset)
    await db_session.commit()
    await db_session.refresh(preset)
    return preset


async def test_the_listing_still_loads_with_a_stale_preset(
    auth_client: AsyncClient,
    stale_preset: ScreenerPreset,
) -> None:
    """A user whose one broken preset 500s the whole screener page has no way back."""
    listing = await auth_client.get("/api/v1/screener/presets")

    assert listing.status_code == 200
    assert stale_preset.name in {p["name"] for p in listing.json()}


async def test_opening_a_stale_preset_is_a_422_naming_the_field(
    auth_client: AsyncClient,
    stale_preset: ScreenerPreset,
) -> None:
    response = await auth_client.get(f"/api/v1/screener/presets/{stale_preset.id}")

    assert response.status_code == 422
    assert "price_to_book" in response.json()["detail"]


async def test_running_a_stale_preset_is_a_422_not_a_500(
    auth_client: AsyncClient,
    stale_preset: ScreenerPreset,
) -> None:
    response = await auth_client.post(f"/api/v1/screener/presets/{stale_preset.id}/run", json={})

    assert response.status_code == 422
