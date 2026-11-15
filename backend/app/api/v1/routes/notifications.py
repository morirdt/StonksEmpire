"""The caller's notifications.

Under ``/me/`` because the list, the count, and read-all are about the caller
and nobody else. ``POST /me/notifications/{id}/read`` still takes an id, so it is
a forged-id case like any other: another user's id is a 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.notification import (
    NotificationPage,
    NotificationResponse,
    ReadAllResponse,
    UnreadCountResponse,
)
from app.schemas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/me/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage, summary="Your notifications")
async def list_notifications(
    session: DbSession,
    user: CurrentUser,
    unread: Annotated[bool, Query()] = False,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> NotificationPage:
    """Newest first. Pass ``next_cursor`` back as ``cursor`` for older ones."""
    return await NotificationService(session).list(
        user.id, unread_only=unread, cursor=cursor, limit=limit
    )


@router.get(
    "/unread-count", response_model=UnreadCountResponse, summary="Unread count, for the badge"
)
async def unread_count(session: DbSession, user: CurrentUser) -> UnreadCountResponse:
    return UnreadCountResponse(count=await NotificationService(session).unread_count(user.id))


@router.post("/read-all", response_model=ReadAllResponse, summary="Mark every notification read")
async def read_all(session: DbSession, user: CurrentUser) -> ReadAllResponse:
    """Idempotent: a repeat marks nothing and returns ``updated: 0``."""
    return ReadAllResponse(updated=await NotificationService(session).mark_all_read(user.id))


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark one notification read",
)
async def read_one(
    session: DbSession, user: CurrentUser, notification_id: uuid.UUID
) -> NotificationResponse:
    """Idempotent: marking a read notification again leaves its ``read_at`` alone."""
    notification = await NotificationService(session).mark_read(user.id, notification_id)
    return NotificationResponse.model_validate(notification)
