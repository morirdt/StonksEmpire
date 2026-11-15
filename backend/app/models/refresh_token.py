"""Refresh tokens, stored hashed, rotated on every use.

Rows are revoked rather than deleted: reuse detection depends on being able to
recognise a token that was already spent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # Every user-scoped table gets a composite index leading with user_id.
        Index("ix_refresh_tokens_user_id_expires_at", "user_id", "expires_at"),
        # Reuse detection revokes a whole family at once, keyed by family alone.
        Index("ix_refresh_tokens_family_id", "family_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SHA-256 hex of the opaque token. Hashed so that a database leak does not
    # hand over usable sessions; SHA-256 rather than Argon2 because the token is
    # 48 random bytes, so there is nothing to brute-force.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Constant across a rotation chain, so one stolen token invalidates the
    # whole lineage rather than just itself.
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        # SET NULL, not CASCADE: deleting one link must not unravel the chain.
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Captured for the session-listing UI in a later phase.
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    def __repr__(self) -> str:
        return f"<RefreshToken {self.id} user={self.user_id}>"
