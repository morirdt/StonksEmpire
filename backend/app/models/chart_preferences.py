"""How one user wants every chart drawn.

Scoped **globally per user**, not per symbol. Someone who wants MACD wants it
everywhere, and a per-symbol table would grow with idle browsing while still
leaving every newly-visited symbol back at the defaults.

The primary key *is* the foreign key, which is what makes the write a single
idempotent upsert and makes "two rows for one user" unrepresentable.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ChartRange, Oscillator, PriceOverlay
from app.db.base import Base, TimestampMixin

#: VARCHAR + CHECK rather than a native Postgres enum. See CLAUDE.md.
_RANGES = tuple(r.value for r in ChartRange)

#: What a user who has never saved anything sees. Deliberately not empty: a
#: chart with two moving averages and RSI shows what the controls are *for*,
#: where a bare candlestick chart looks like the indicators are broken.
DEFAULT_RANGE = ChartRange.ONE_YEAR
DEFAULT_OVERLAYS: tuple[str, ...] = (PriceOverlay.SMA_20.value, PriceOverlay.SMA_50.value)
DEFAULT_OSCILLATORS: tuple[str, ...] = (Oscillator.RSI_14.value,)


class UserChartPreferences(Base, TimestampMixin):
    __tablename__ = "user_chart_preferences"
    __table_args__ = (
        CheckConstraint(
            "default_range IN ({})".format(", ".join(f"'{r}'" for r in _RANGES)),
            name="default_range_valid",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    default_range: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=DEFAULT_RANGE.value,
    )
    #: JSONB rather than a boolean column per indicator, which is a deliberate
    #: departure from how ``daily_indicators`` is shaped. That table is wide
    #: because Phase 4 *filters* on it in SQL; this one is only ever read whole
    #: and written whole, and is never a query predicate. The database is
    #: therefore not what enforces the contents — the Pydantic schema is.
    active_overlays: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: list(DEFAULT_OVERLAYS),
    )
    active_oscillators: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: list(DEFAULT_OSCILLATORS),
    )

    def __repr__(self) -> str:
        return f"<UserChartPreferences {self.user_id} {self.default_range}>"
