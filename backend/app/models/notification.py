"""In-app notifications: one row per thing the user should hear about.

Written in the same transaction as the change that caused it, so a firing and
its notification exist together or not at all. The WebSocket push that follows
the commit is only a hint; this table is the data.

**This is also where email will attach in Phase 7**, as outbox columns on this
same row. That is why the firing writes the notification inside its own
transaction: a later delivery job gets a durable row, written exactly once, to
deliver from. Those columns are not added before they are used.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import NotificationKind
from app.db.base import Base, TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k.value}'" for k in NotificationKind)),
            name="kind_valid",
        ),
        # The cursor: newest first, with the id breaking ties within one
        # transaction's now().
        Index("ix_notifications_user_id_created_at_id", "user_id", "created_at", "id"),
        # The unread badge.
        Index(
            "ix_notifications_unread_user_id",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: The link target: alert id, ticker.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: SET NULL, so the notification outlives the alert it was about.
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Notification {self.id} {self.kind}>"
