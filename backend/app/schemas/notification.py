"""Notifications on the wire."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.enums import NotificationKind

__all__ = ["NotificationPage", "NotificationResponse", "ReadAllResponse", "UnreadCountResponse"]


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: NotificationKind
    title: str
    body: str
    #: The link target: ``alert_id``, plus ``ticker`` or ``preset_id``.
    payload: dict[str, Any]
    #: ``null`` once the alert it was about has been deleted.
    alert_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationResponse]
    #: Pass back as ``cursor`` for the next, older page. ``null`` at the end.
    next_cursor: str | None


class UnreadCountResponse(BaseModel):
    count: int


class ReadAllResponse(BaseModel):
    #: How many notifications this call marked read. ``0`` on a repeat.
    updated: int
