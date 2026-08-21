"""The user account."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String, Uuid
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid7,
    )
    # CITEXT so that uniqueness and lookup are both case-insensitive: nobody
    # should be able to register Alice@example.com against alice@example.com.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Unused until Phase 7 brings email delivery; the column exists now so the
    # table does not need a migration the moment verification is turned on.
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<User {self.id} {self.email}>"
