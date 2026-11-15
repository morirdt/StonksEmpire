"""Saved screens.

One table. The filter tree is ``JSONB`` for the same reason
``user_chart_preferences`` stores its indicator lists that way: it is read
whole, written whole, and never a query predicate. The consequence is that the
database enforces nothing about its contents — ``app/schemas/screener.py`` is
what does, on the way in *and* on the way out.

The way out matters more than it looks. A stored tree can go stale: if a later
phase removes an indicator column, a preset naming it no longer validates. That
is handled where it is cheapest to tolerate — the list endpoint returns name,
id, and timestamps without parsing ``filters``, so the screener page always
loads, and only opening or running a preset validates and fails with a message
naming the offending field. A user whose one broken preset 500s the whole page
has no way back.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import SortDirection
from app.db.base import Base, TimestampMixin

#: VARCHAR + CHECK rather than a native Postgres enum. See CLAUDE.md.
_SORT_DIRECTIONS = tuple(d.value for d in SortDirection)


class ScreenerPreset(Base, TimestampMixin):
    __tablename__ = "screener_presets"
    __table_args__ = (
        # Names are unique per user, like watchlists: two people may both have
        # a screen called "Oversold large caps".
        UniqueConstraint("user_id", "name", name="uq_screener_presets_user_id_name"),
        CheckConstraint(
            "sort_direction IN ({})".format(", ".join(f"'{d}'" for d in _SORT_DIRECTIONS)),
            name="sort_direction_valid",
        ),
        Index("ix_screener_presets_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: A registry key, not a column name. Nothing interpolates it into SQL —
    #: it is resolved through app/services/screener/fields.py like any other.
    sort_field: Mapped[str] = mapped_column(String(40), nullable=False)
    sort_direction: Mapped[str] = mapped_column(String(4), nullable=False)

    def __repr__(self) -> str:
        return f"<ScreenerPreset {self.id} {self.name!r}>"
