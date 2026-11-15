"""The caller's chart preferences.

One row per user, global across every symbol. There is no path id here — the
resource *is* the caller — which is what makes the cross-user test for it look
different from the watchlist ones: there is no id to forge, so the thing to
prove is that a write by one user leaves another user's row untouched.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.chart import ChartPreferencesRequest, ChartPreferencesResponse
from app.services.chart_service import ChartPreferencesService

router = APIRouter(prefix="/me", tags=["chart"])


@router.get(
    "/chart-preferences",
    response_model=ChartPreferencesResponse,
    summary="Your chart preferences",
)
async def get_chart_preferences(
    session: DbSession,
    user: CurrentUser,
) -> ChartPreferencesResponse:
    """Defaults, not a 404, when nothing has ever been saved.

    A client that has to special-case "no preferences yet" will get it wrong on
    first load, and there is no meaningful difference between "unset" and "set
    to the defaults".
    """
    row = await ChartPreferencesService(session).get_for_user(user.id)
    return ChartPreferencesResponse.from_row(row)


@router.put(
    "/chart-preferences",
    response_model=ChartPreferencesResponse,
    summary="Replace your chart preferences",
)
async def put_chart_preferences(
    session: DbSession,
    user: CurrentUser,
    payload: ChartPreferencesRequest,
) -> ChartPreferencesResponse:
    """Full replacement, not a patch. A second PUT replaces rather than merges."""
    row = await ChartPreferencesService(session).replace(
        user.id,
        default_range=payload.default_range,
        active_overlays=[k.value for k in payload.active_overlays],
        active_oscillators=[k.value for k in payload.active_oscillators],
    )
    return ChartPreferencesResponse.from_row(row)
