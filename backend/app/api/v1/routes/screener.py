"""The screener: the field catalogue, ad-hoc runs, and saved presets.

Every route requires ``CurrentUser``, and every preset is scoped to the caller
by the service — a preset belonging to someone else comes back **404, not 403**,
because a 403 confirms the row exists.
``tests/integration/test_cross_user_authorization.py`` enforces that for each of
the four paths here that take an id.

``POST`` for a read on the run endpoints. A filter tree does not fit in a query
string legibly, and URL-encoding JSON into a ``GET`` trades a readable body for
an unreadable URL and a length limit. The lost HTTP caching is not a cost here:
the underlying data changes once a day, and the client caches through TanStack
Query anyway.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession
from app.models.screener import ScreenerPreset
from app.schemas.screener import (
    ScreenerFieldsResponse,
    ScreenerPresetCreateRequest,
    ScreenerPresetResponse,
    ScreenerPresetRunRequest,
    ScreenerPresetSummary,
    ScreenerPresetUpdateRequest,
    ScreenerRunRequest,
    ScreenerRunResponse,
)
from app.services.screener.service import ScreenerService, preset_filters, preset_sort

router = APIRouter(prefix="/screener", tags=["screener"])


def _to_response(preset: ScreenerPreset) -> ScreenerPresetResponse:
    """Re-validates the stored tree, so a stale preset is a 422 rather than a 500."""
    return ScreenerPresetResponse(
        id=preset.id,
        name=preset.name,
        created_at=preset.created_at,
        updated_at=preset.updated_at,
        filters=preset_filters(preset),
        sort=preset_sort(preset),
    )


@router.get(
    "/fields",
    response_model=ScreenerFieldsResponse,
    summary="The catalogue of screenable fields",
)
async def screener_fields(session: DbSession, user: CurrentUser) -> ScreenerFieldsResponse:
    """What the filter builder is generated from.

    Served from the same registry the compiler resolves against, so the UI
    cannot offer a field the compiler will reject. The frontend deliberately
    keeps no copy of this list.
    """
    return await ScreenerService(session).catalogue()


@router.post("/run", response_model=ScreenerRunResponse, summary="Run an ad-hoc screen")
async def run_screen(
    session: DbSession,
    user: CurrentUser,
    payload: ScreenerRunRequest,
) -> ScreenerRunResponse:
    """An unknown field, an unknown operator, or a unit-mismatched comparison is
    a 422 raised by the schema before this function is called."""
    return await ScreenerService(session).run(payload.filters, payload.sort, payload.limit)


@router.get(
    "/presets",
    response_model=list[ScreenerPresetSummary],
    summary="Your saved screens",
)
async def list_presets(session: DbSession, user: CurrentUser) -> list[ScreenerPresetSummary]:
    """Names and timestamps only — the stored trees are deliberately not parsed.

    A preset naming a field that a later phase removed would otherwise take the
    whole screener page down with it. It fails when it is opened or run, which
    is where a user can do something about it.
    """
    presets = await ScreenerService(session).list_presets(user.id)
    return [ScreenerPresetSummary.model_validate(p) for p in presets]


@router.post(
    "/presets",
    response_model=ScreenerPresetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a screen",
)
async def create_preset(
    session: DbSession,
    user: CurrentUser,
    payload: ScreenerPresetCreateRequest,
) -> ScreenerPresetResponse:
    """409 when the name is already taken — names are unique per user."""
    preset = await ScreenerService(session).create_preset(
        user.id,
        name=payload.name,
        filters=payload.filters,
        sort=payload.sort,
    )
    return _to_response(preset)


@router.get(
    "/presets/{preset_id}",
    response_model=ScreenerPresetResponse,
    summary="Get a saved screen",
)
async def get_preset(
    session: DbSession,
    user: CurrentUser,
    preset_id: uuid.UUID,
) -> ScreenerPresetResponse:
    preset = await ScreenerService(session).get_preset(user.id, preset_id)
    return _to_response(preset)


@router.patch(
    "/presets/{preset_id}",
    response_model=ScreenerPresetResponse,
    summary="Rename a screen or replace its filters",
)
async def update_preset(
    session: DbSession,
    user: CurrentUser,
    preset_id: uuid.UUID,
    payload: ScreenerPresetUpdateRequest,
) -> ScreenerPresetResponse:
    preset = await ScreenerService(session).update_preset(
        user.id,
        preset_id,
        name=payload.name,
        filters=payload.filters,
        sort=payload.sort,
    )
    return _to_response(preset)


@router.delete(
    "/presets/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved screen",
)
async def delete_preset(
    session: DbSession,
    user: CurrentUser,
    preset_id: uuid.UUID,
) -> Response:
    await ScreenerService(session).delete_preset(user.id, preset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/presets/{preset_id}/run",
    response_model=ScreenerRunResponse,
    summary="Run a saved screen",
)
async def run_preset(
    session: DbSession,
    user: CurrentUser,
    preset_id: uuid.UUID,
    payload: ScreenerPresetRunRequest | None = None,
) -> ScreenerRunResponse:
    """Runs what is stored, not what the client says is stored.

    This exists instead of making the client fetch-then-post: it is the primary
    path from the UI, it halves the round trips, and it removes the window in
    which a client can run something subtly different from what is saved under
    that name.
    """
    request = payload or ScreenerPresetRunRequest()
    return await ScreenerService(session).run_preset(user.id, preset_id, limit=request.limit)
