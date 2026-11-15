"""Alerts: the field catalogues, CRUD, and firing history.

Every route requires ``CurrentUser``. Another user's alert is a **404, not a
403**, because a 403 confirms the row exists.
``tests/integration/test_cross_user_authorization.py`` enforces that for every
path here that takes an id.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.enums import AlertKind
from app.schemas.alert import (
    AlertCreateRequest,
    AlertCreateResponse,
    AlertResponse,
    AlertSummary,
    AlertTriggerPage,
    AlertUpdateRequest,
    registry_for,
)
from app.schemas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.screener import ScreenerFieldsResponse
from app.services.alerts.service import AlertService
from app.services.screener.service import ScreenerService

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get(
    "/fields",
    response_model=ScreenerFieldsResponse,
    summary="The fields an alert condition can name",
)
async def alert_fields(
    session: DbSession,
    user: CurrentUser,
    kind: Annotated[Literal[AlertKind.INTRADAY, AlertKind.END_OF_DAY], Query()],
) -> ScreenerFieldsResponse:
    """The same catalogue shape as ``GET /screener/fields``, for one alert kind.

    ``end_of_day`` is the screener's registry. ``intraday`` swaps the bar
    fields for live ones (``price``, ``day_high``, …) and keeps every indicator.
    A screen alert has no catalogue, because its condition is its preset's.
    """
    return await ScreenerService(session).catalogue(registry_for(kind))


@router.get("", response_model=list[AlertSummary], summary="Your alerts")
async def list_alerts(session: DbSession, user: CurrentUser) -> list[AlertSummary]:
    """Every alert, newest first, without parsing stored conditions."""
    return await AlertService(session).list(user.id)


@router.post(
    "",
    response_model=AlertCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an alert",
)
async def create_alert(
    session: DbSession, user: CurrentUser, payload: AlertCreateRequest
) -> AlertCreateResponse:
    """A 422 past the active-alert cap, or for a condition the kind cannot use.

    ``condition_met_now`` says whether the condition already holds, because a
    new alert does not fire on its first evaluation.
    """
    return await AlertService(session).create(user.id, payload)


@router.get("/{alert_id}", response_model=AlertResponse, summary="One alert")
async def get_alert(session: DbSession, user: CurrentUser, alert_id: uuid.UUID) -> AlertResponse:
    return await AlertService(session).get(user.id, alert_id)


@router.patch("/{alert_id}", response_model=AlertResponse, summary="Edit or pause an alert")
async def update_alert(
    session: DbSession,
    user: CurrentUser,
    alert_id: uuid.UUID,
    payload: AlertUpdateRequest,
) -> AlertResponse:
    return await AlertService(session).update(user.id, alert_id, payload)


@router.delete(
    "/{alert_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete an alert",
)
async def delete_alert(session: DbSession, user: CurrentUser, alert_id: uuid.UUID) -> Response:
    await AlertService(session).delete(user.id, alert_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{alert_id}/triggers",
    response_model=AlertTriggerPage,
    summary="An alert's firing history",
)
async def alert_triggers(
    session: DbSession,
    user: CurrentUser,
    alert_id: uuid.UUID,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> AlertTriggerPage:
    """Newest first, each with a snapshot of the values that made it fire."""
    return await AlertService(session).triggers(user.id, alert_id, cursor=cursor, limit=limit)
