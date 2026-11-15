"""The caller's notifications: list, count, and mark read.

Notifications are created by the worker, inside the transaction that caused
them (``app/services/alerts/firing.py``). This service only reads them and
marks them read. Both "mark" operations are idempotent, so a client that retries
after a dropped response changes nothing the second time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.notification import Notification
from app.schemas.notification import NotificationPage, NotificationResponse
from app.schemas.pagination import decode_cursor, encode_cursor

__all__ = ["NotificationService"]


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self,
        user_id: uuid.UUID,
        *,
        unread_only: bool,
        cursor: str | None,
        limit: int,
    ) -> NotificationPage:
        """Newest first, cursor-paginated on ``(created_at, id)``.

        A cursor rather than an offset: new notifications arrive at the top
        while someone pages, and an offset would then skip or repeat rows.
        """
        query = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            query = query.where(Notification.read_at.is_(None))
        after = decode_cursor(cursor)
        if after is not None:
            query = query.where(
                tuple_(Notification.created_at, Notification.id) < (after.at, after.id)
            )
        rows = list(
            (
                await self._session.execute(
                    query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(
                        limit + 1
                    )
                )
            ).scalars()
        )
        page, more = rows[:limit], len(rows) > limit
        return NotificationPage(
            items=[NotificationResponse.model_validate(row) for row in page],
            next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if more else None,
        )

    async def unread_count(self, user_id: uuid.UUID) -> int:
        count = await self._session.scalar(
            select(func.count()).where(
                Notification.user_id == user_id, Notification.read_at.is_(None)
            )
        )
        return count or 0

    async def mark_read(self, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
        """Mark one read. Another user's notification is a 404, not a 403."""
        notification = (
            await self._session.execute(
                select(Notification).where(
                    Notification.id == notification_id, Notification.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if notification is None:
            raise NotFoundError("Notification not found.")
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            await self._session.commit()
            await self._session.refresh(notification)
        return notification

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def prune_read(self, *, older_than: datetime) -> int:
        """Delete read notifications created before ``older_than``. For the worker.

        Unread ones are kept however old they are: deleting something the user
        never saw would be losing it, not tidying up.
        """
        result = await self._session.execute(
            delete(Notification).where(
                Notification.read_at.is_not(None), Notification.created_at < older_than
            )
        )
        await self._session.commit()
        return int(result.rowcount)  # type: ignore[attr-defined]
