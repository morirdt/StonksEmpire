"""The notification housekeeping job."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.services.notification_service import NotificationService

__all__ = ["PruneResult", "prune_notifications"]

#: Read notifications older than this are deleted. Unread ones are never pruned.
RETENTION: Final = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class PruneResult:
    deleted: int


async def prune_notifications(
    session_factory: async_sessionmaker[AsyncSession], clock: Clock
) -> PruneResult:
    """Delete read notifications more than 90 days old.

    The trigger history on each alert is kept indefinitely, since it is part of
    the feature. A notification is only the message about it.
    """
    async with session_factory() as session:
        deleted = await NotificationService(session).prune_read(older_than=clock() - RETENTION)
    return PruneResult(deleted=deleted)
