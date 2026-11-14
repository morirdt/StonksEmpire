"""Alerts end to end: created through the API, evaluated by real job runs.

Prices come from ``Fake`` at frozen instants. A threshold is chosen between two
of its prices, so a test knows exactly which poll crosses it, and nothing is
mocked between the job and the database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.jobs.tasks.market_data import ingest_eod, poll_quotes, refresh_symbols
from app.models.alert import Alert
from app.models.market_data import DailyBar, LatestQuote
from app.models.symbol import Symbol
from app.models.user import User
from app.services.alerts.firing import AlertEvaluator
from app.services.market_data_service import MarketDataService
from tests.integration.conftest import FrozenClock
from tests.integration.providers import UNIVERSE, DownProvider, Published, RecordingProvider

pytestmark = pytest.mark.integration

type Factory = async_sessionmaker[AsyncSession]

SESSION_DAY = (2026, 11, 18)


def price_above(threshold: Decimal | str) -> dict[str, Any]:
    return {"kind": "numeric", "field": "price", "op": "gt", "value": str(threshold)}


async def create_alert(client: AsyncClient, **body: Any) -> dict[str, Any]:
    payload = {"name": "Test alert", "kind": "intraday", "ticker": "AAPL", **body}
    response = await client.post("/api/v1/alerts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def crossing(clock: FrozenClock) -> tuple[tuple[int, int], tuple[int, int], Decimal]:
    """Two minutes of the session and a threshold that AAPL crosses between them."""
    fake = RecordingProvider(clock)
    quotes: list[tuple[tuple[int, int], Decimal]] = []
    for minute in range(0, 120, 3):
        at = (10 + minute // 60, minute % 60)
        clock.set_et(*SESSION_DAY, *at)
        quotes.append((at, (await fake.get_quote("AAPL")).price))
    for i, (low_at, low) in enumerate(quotes):
        for high_at, high in quotes[i + 1 :]:
            if high > low:
                return low_at, high_at, ((low + high) / 2).quantize(Decimal("0.001"))
    raise AssertionError("Fake's path never rose in two hours")  # pragma: no cover


async def poll(
    factory: Factory, clock: FrozenClock, at: tuple[int, int], **kw: Any
) -> tuple[Any, Published]:
    clock.set_et(*SESSION_DAY, *at)
    published = Published()
    kw.setdefault("provider", RecordingProvider(clock))
    return await poll_quotes(factory, clock, publish=published, **kw), published


async def _force(session: AsyncSession, alert_id: str, **values: Any) -> None:
    """Set alert columns directly: for states the API deliberately cannot reach."""
    await session.execute(update(Alert).where(Alert.id == uuid.UUID(alert_id)).values(**values))
    await session.commit()


# ------------------------------------------------------------------- intraday


async def test_an_intraday_alert_fires_on_the_crossing_and_only_then(
    session_factory: Factory,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
    registered_user: dict[str, str],
) -> None:
    below, above, threshold = await crossing(clock)
    alert = await create_alert(auth_client, condition=price_above(threshold), trigger_mode="rearm")
    assert alert["condition_met_now"] is None, "no quote yet, so nothing to say"

    first, _ = await poll(session_factory, clock, below)
    assert (first.alerts.evaluated, first.alerts.fired) == (1, 0), "first evaluation records"

    second, published = await poll(session_factory, clock, above)
    assert second.alerts.fired == 1

    third, _ = await poll(session_factory, clock, above)
    assert third.alerts.fired == 0, "true -> true is not an edge"

    body = (await auth_client.get(f"/api/v1/alerts/{alert['id']}")).json()
    assert body["last_state"] is True
    assert body["last_triggered_at"] is not None
    assert body["status"] == "active", "rearm stays active"

    (trigger,) = (await auth_client.get(f"/api/v1/alerts/{alert['id']}/triggers")).json()["items"]
    assert Decimal(trigger["snapshot"]["values"]["price"]) > threshold
    assert trigger["snapshot"]["ticker"] == "AAPL"

    notes = (await auth_client.get("/api/v1/me/notifications")).json()["items"]
    assert [n["kind"] for n in notes] == ["alert_triggered"]
    assert notes[0]["title"] == "AAPL: Test alert"
    assert notes[0]["payload"]["alert_id"] == alert["id"]

    hints = [e for e in published.events if e["type"] == "notification_created"]
    assert hints == [
        {
            "type": "notification_created",
            "user_id": hints[0]["user_id"],
            "notification_id": notes[0]["id"],
        }
    ]


async def test_a_once_alert_becomes_triggered(
    session_factory: Factory,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    below, above, threshold = await crossing(clock)
    alert = await create_alert(auth_client, condition=price_above(threshold))
    await poll(session_factory, clock, below)

    await poll(session_factory, clock, above)

    body = (await auth_client.get(f"/api/v1/alerts/{alert['id']}")).json()
    assert body["status"] == "triggered"
    assert body["trigger_mode"] == "once"


async def test_a_new_alert_already_met_says_so_and_does_not_fire(
    session_factory: Factory,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    await create_alert(auth_client, condition=price_above("1"), name="warm-up")
    await poll(session_factory, clock, (11, 0))  # now AAPL has a quote

    alert = await create_alert(auth_client, condition=price_above("1"))
    result, _ = await poll(session_factory, clock, (11, 30))

    assert alert["condition_met_now"] is True
    assert result.alerts.fired == 0


async def test_intraday_alerts_ignore_stale_quotes(
    session_factory: Factory,
    db_session: AsyncSession,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    """With the breaker open, nothing is evaluated, however well the stored row matches."""
    alert = await create_alert(auth_client, condition=price_above("1"), trigger_mode="rearm")
    await poll(session_factory, clock, (10, 0))  # stores a quote, records last_state
    await _force(db_session, alert["id"], last_state=False)
    before = (await auth_client.get(f"/api/v1/alerts/{alert['id']}")).json()

    result, published = await poll(
        session_factory, clock, (10, 30), provider=DownProvider(UNIVERSE, clock=clock)
    )

    after = (await auth_client.get(f"/api/v1/alerts/{alert['id']}")).json()
    assert result.alerts.evaluated == 0
    assert after["last_evaluated_at"] == before["last_evaluated_at"]
    assert after["last_triggered_at"] is None
    assert published.events == []


async def test_one_broken_alert_does_not_stop_the_run(
    session_factory: Factory,
    db_session: AsyncSession,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    broken = await create_alert(auth_client, condition=price_above("1"), name="broken")
    healthy = await create_alert(
        auth_client, condition=price_above("1"), name="healthy", trigger_mode="rearm"
    )
    # A field that no longer exists, as a later phase removing a column would leave it.
    await _force(
        db_session,
        broken["id"],
        condition={"kind": "numeric", "field": "removed_field", "op": "gt", "value": "1"},
    )
    await _force(db_session, healthy["id"], last_state=False)

    result, _ = await poll(session_factory, clock, (10, 0))

    assert (result.alerts.disabled, result.alerts.fired) == (1, 1)
    broken_now = (await auth_client.get("/api/v1/alerts")).json()
    by_name = {a["name"]: a for a in broken_now}
    assert by_name["broken"]["status"] == "disabled"
    assert "removed_field" in by_name["broken"]["disabled_reason"]
    assert by_name["healthy"]["last_triggered_at"] is not None

    kinds = sorted(
        n["kind"] for n in (await auth_client.get("/api/v1/me/notifications")).json()["items"]
    )
    assert kinds == ["alert_disabled", "alert_triggered"]


# ----------------------------------------------------------------- end of day


async def test_an_end_of_day_alert_fires_from_the_nightly_ingest(
    session_factory: Factory,
    db_session: AsyncSession,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    clock.set_et(*SESSION_DAY, 17, 30)
    fake = RecordingProvider(clock)
    bars = await fake.get_daily_bars("AAPL", date(2026, 11, 17), date(2026, 11, 18))
    yesterday, today = bars[0].close, bars[1].close
    middle = str((yesterday + today) / 2)
    op = "gt" if today > yesterday else "lt"
    aapl = (await db_session.execute(select(Symbol).where(Symbol.ticker == "AAPL"))).scalar_one()
    await MarketDataService(db_session, provider=fake).ingest_range(
        aapl, date(2026, 9, 1), date(2026, 11, 17)
    )

    alert = await create_alert(
        auth_client,
        kind="end_of_day",
        condition={"kind": "numeric", "field": "close", "op": op, "value": middle},
    )
    assert alert["condition_met_now"] is False, "evaluated at yesterday's close"

    result = await ingest_eod(session_factory, clock, provider=fake, publish=Published())

    assert result.as_of == date(2026, 11, 18)
    assert result.alerts.fired == 1
    (trigger,) = (await auth_client.get(f"/api/v1/alerts/{alert['id']}/triggers")).json()["items"]
    assert trigger["as_of_date"] == "2026-11-18"
    assert Decimal(trigger["snapshot"]["values"]["close"]) == today


# --------------------------------------------------------------------- screens


async def _bar(session: AsyncSession, ticker: str, close: str, day: date) -> None:
    symbol = (await session.execute(select(Symbol).where(Symbol.ticker == ticker))).scalar_one()
    price = Decimal(close)
    await session.merge(
        DailyBar(
            symbol_id=symbol.id,
            trade_date=day,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1_000_000,
            is_adjusted=True,
        )
    )
    await session.commit()


async def test_a_screen_alert_fires_on_new_entrants_and_on_re_entry(
    session_factory: Factory,
    db_session: AsyncSession,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    day = date(*SESSION_DAY)
    await _bar(db_session, "AAPL", "150", day)
    await _bar(db_session, "MSFT", "50", day)
    preset = await auth_client.post(
        "/api/v1/screener/presets",
        json={
            "name": "Over 100",
            "filters": {"kind": "numeric", "field": "close", "op": "gt", "value": "100"},
        },
    )
    alert = (
        await auth_client.post(
            "/api/v1/alerts",
            json={"name": "Over 100", "kind": "screen", "preset_id": preset.json()["id"]},
        )
    ).json()
    assert alert["trigger_mode"] == "rearm"

    async def run() -> int:
        async with session_factory() as session:
            report = await AlertEvaluator(session, clock()).run_screens(day)
            await session.commit()
        return report.fired

    assert await run() == 0, "the first run only records the set"

    await _bar(db_session, "MSFT", "120", day)
    assert await run() == 1
    await _bar(db_session, "MSFT", "50", day)
    assert await run() == 0, "leaving is not news"
    await _bar(db_session, "MSFT", "120", day)
    assert await run() == 1, "re-entering is"

    triggers = (await auth_client.get(f"/api/v1/alerts/{alert['id']}/triggers")).json()["items"]
    assert [t["snapshot"]["entrants"] for t in triggers] == [["MSFT"], ["MSFT"]]
    note = (await auth_client.get("/api/v1/me/notifications")).json()["items"][0]
    assert note["title"] == "Over 100: 1 new match"
    assert note["body"] == "MSFT now match."


async def test_deleting_a_preset_deletes_its_screen_alerts(
    auth_client: AsyncClient, seeded_symbols: list[Symbol]
) -> None:
    preset = await auth_client.post(
        "/api/v1/screener/presets",
        json={
            "name": "Doomed",
            "filters": {"kind": "numeric", "field": "close", "op": "gt", "value": "1"},
        },
    )
    alert = await create_alert(
        auth_client, kind="screen", ticker=None, preset_id=preset.json()["id"]
    )

    (listed,) = (await auth_client.get("/api/v1/screener/presets")).json()
    assert listed["alert_count"] == 1, "the delete confirmation can say what goes with it"

    await auth_client.delete(f"/api/v1/screener/presets/{preset.json()['id']}")

    assert (await auth_client.get(f"/api/v1/alerts/{alert['id']}")).status_code == 404


# -------------------------------------------------------------------- delisting


async def test_a_delisted_symbol_disables_its_alerts_and_says_why(
    session_factory: Factory,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    alert = await create_alert(auth_client, ticker="SPY", condition=price_above("1"))

    result = await refresh_symbols(
        session_factory,
        clock,
        provider=RecordingProvider(clock, [s for s in UNIVERSE if s.ticker != "SPY"]),
        max_deactivation_share=1.0,
        publish=Published(),
    )

    assert result.alerts_disabled == 1
    body = (await auth_client.get(f"/api/v1/alerts/{alert['id']}")).json()
    assert body["status"] == "disabled"
    assert "SPY is no longer listed" in body["disabled_reason"]
    (note,) = (await auth_client.get("/api/v1/me/notifications")).json()["items"]
    assert note["kind"] == "alert_disabled"


# ------------------------------------------------------------------------ CRUD


async def test_editing_the_condition_resets_the_state(
    session_factory: Factory,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    alert = await create_alert(auth_client, condition=price_above("1"))
    await poll(session_factory, clock, (10, 0))
    assert (await auth_client.get(f"/api/v1/alerts/{alert['id']}")).json()["last_state"] is True

    patched = await auth_client.patch(
        f"/api/v1/alerts/{alert['id']}", json={"condition": price_above("2")}
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["last_state"] is None


async def test_an_intraday_condition_is_checked_against_the_intraday_registry_on_edit(
    auth_client: AsyncClient, seeded_symbols: list[Symbol]
) -> None:
    alert = await create_alert(auth_client, condition=price_above("1"))

    response = await auth_client.patch(
        f"/api/v1/alerts/{alert['id']}",
        json={"condition": {"kind": "numeric", "field": "close", "op": "gt", "value": "1"}},
    )

    assert response.status_code == 422
    assert "GET /alerts/fields" in response.text


async def test_an_unknown_ticker_is_a_422(
    auth_client: AsyncClient, seeded_symbols: list[Symbol]
) -> None:
    response = await auth_client.post(
        "/api/v1/alerts",
        json={"name": "x", "kind": "intraday", "ticker": "NOPE", "condition": price_above("1")},
    )
    assert response.status_code == 422


async def test_the_active_cap_at_the_limit_and_one_past(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings().alerts, "max_active_per_user", 2)
    first = await create_alert(auth_client, condition=price_above("1"))
    await create_alert(auth_client, condition=price_above("1"))

    past = await auth_client.post(
        "/api/v1/alerts",
        json={"name": "x", "kind": "intraday", "ticker": "AAPL", "condition": price_above("1")},
    )
    assert past.status_code == 422
    assert "2 active alerts" in past.json()["detail"]

    await auth_client.patch(f"/api/v1/alerts/{first['id']}", json={"status": "paused"})
    await create_alert(auth_client, condition=price_above("1"))
    resumed = await auth_client.patch(f"/api/v1/alerts/{first['id']}", json={"status": "active"})
    assert resumed.status_code == 422, "resuming counts against the cap too"


async def test_the_list_survives_a_stale_condition_that_opening_rejects(
    db_session: AsyncSession, auth_client: AsyncClient, seeded_symbols: list[Symbol]
) -> None:
    alert = await create_alert(auth_client, condition=price_above("1"))
    await _force(
        db_session,
        alert["id"],
        condition={"kind": "numeric", "field": "removed_field", "op": "gt", "value": "1"},
    )

    listing = await auth_client.get("/api/v1/alerts")
    opened = await auth_client.get(f"/api/v1/alerts/{alert['id']}")

    assert listing.status_code == 200
    assert listing.json()[0]["condition_summary"] == "removed_field > 1"
    assert opened.status_code == 422


async def test_a_paused_alert_is_not_evaluated(
    session_factory: Factory,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    alert = await create_alert(auth_client, condition=price_above("1"))
    await auth_client.patch(f"/api/v1/alerts/{alert['id']}", json={"status": "paused"})

    result, _ = await poll(session_factory, clock, (10, 0))

    assert result.hot_set == 0, "a paused alert does not keep its symbol hot"
    assert result.alerts.evaluated == 0


async def test_the_fields_catalogue_depends_on_kind(auth_client: AsyncClient) -> None:
    intraday = await auth_client.get("/api/v1/alerts/fields", params={"kind": "intraday"})
    eod = await auth_client.get("/api/v1/alerts/fields", params={"kind": "end_of_day"})
    screen = await auth_client.get("/api/v1/alerts/fields", params={"kind": "screen"})

    intraday_keys = {f["key"] for f in intraday.json()["fields"]}
    eod_keys = {f["key"] for f in eod.json()["fields"]}
    assert "price" in intraday_keys and "close" not in intraday_keys
    assert "close" in eod_keys and "price" not in eod_keys
    assert screen.status_code == 422


async def test_the_database_rejects_a_half_formed_alert(
    db_session: AsyncSession, seeded_symbols: list[Symbol], registered_user: dict[str, str]
) -> None:
    """The schema checks this too; the CHECK constraint is the last line of defence."""
    user = (
        await db_session.execute(select(User).where(User.email == registered_user["email"]))
    ).scalar_one()
    db_session.add(
        Alert(
            user_id=user.id,
            name="bad",
            kind="screen",
            symbol_id=seeded_symbols[0].id,
            trigger_mode="rearm",
            cooldown_minutes=60,
            status="active",
        )
    )
    with pytest.raises(IntegrityError, match="ck_alerts_shape_matches_kind"):
        await db_session.flush()


async def test_a_stale_quote_does_not_answer_condition_met_now(
    session_factory: Factory,
    db_session: AsyncSession,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    """Days-old prices say nothing about now, and must not seed the first state.

    Found on the live stack: a cached quote above the threshold made a new alert
    report `true` while the real price sat below it.
    """
    await create_alert(auth_client, condition=price_above("1"), name="warm-up")
    await poll(session_factory, clock, (11, 0))
    await db_session.execute(
        update(LatestQuote).values(fetched_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    await db_session.commit()

    alert = await create_alert(auth_client, condition=price_above("1"))

    assert alert["condition_met_now"] is None
    assert alert["last_state"] is None, "nothing definite was seen, so nothing is recorded"
