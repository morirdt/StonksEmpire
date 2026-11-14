"""Watchlists — the first user-scoped resource after auth itself.

Ordinary CRUD, so per ``CLAUDE.md`` there is no repository: services use the
session directly. What is not ordinary is the scoping, and every query against
these tables must filter by the calling user's id. The parameterized harness in
``tests/integration/test_cross_user_authorization.py`` exists to prove it does.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Watchlist(Base, TimestampMixin):
    __tablename__ = "watchlists"
    __table_args__ = (
        # Names are unique per user, not globally: two people may both have a
        # list called "Earnings plays".
        UniqueConstraint("user_id", "name", name="uq_watchlists_user_id_name"),
        Index("ix_watchlists_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)

    items: Mapped[list[WatchlistItem]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
        order_by="WatchlistItem.position",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Watchlist {self.id} {self.name!r}>"


class WatchlistItem(Base, TimestampMixin):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        # A symbol appears at most once per list; adding it twice is a 409.
        UniqueConstraint(
            "watchlist_id", "symbol_id", name="uq_watchlist_items_watchlist_id_symbol_id"
        ),
        Index("ix_watchlist_items_watchlist_id_position", "watchlist_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        # RESTRICT rather than CASCADE: a symbol should never be hard-deleted
        # while someone is watching it. The universe seed deactivates instead.
        ForeignKey("symbols.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Manual ordering within the list. Dense and zero-based after any reorder.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")

    def __repr__(self) -> str:
        return f"<WatchlistItem {self.id} list={self.watchlist_id}>"
