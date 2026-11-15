"""The tradable universe.

One row per instrument, and the anchor every other market data table hangs off.
Rows are deactivated rather than deleted when an instrument stops trading, so a
Phase 6 journal entry against a delisted name still resolves to something.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AssetType
from app.db.base import Base, TimestampMixin

#: VARCHAR + CHECK rather than a native Postgres enum: these values change, and
#: ALTER TYPE is painful. See CLAUDE.md.
_ASSET_TYPES = tuple(a.value for a in AssetType)


class Symbol(Base, TimestampMixin):
    __tablename__ = "symbols"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ({})".format(", ".join(f"'{a}'" for a in _ASSET_TYPES)),
            name="asset_type_valid",
        ),
        # Trigram indexes for search. pg_trgm is enabled by the Phase 0 baseline
        # migration, which is the reason it is there. GIN over gist because this
        # table is read constantly and written once a day.
        Index(
            "ix_symbols_ticker_trgm",
            "ticker",
            postgresql_using="gin",
            postgresql_ops={"ticker": "gin_trgm_ops"},
        ),
        Index(
            "ix_symbols_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(20), nullable=True)
    asset_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AssetType.COMMON_STOCK.value,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: When the universe seed last confirmed this row against a provider.
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<Symbol {self.ticker}>"
