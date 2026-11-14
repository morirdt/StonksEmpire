"""add symbols, bars, indicators, quotes and watchlists

Phase 2. Six tables in two groups.

Market data, which is not user-scoped — it is the same for everyone, and the
database is the system of record so that Phase 3 charting and Phase 4 screening
never have to hit a provider API:

  * symbols          — the tradable universe. Trigram indexes on ticker and
                       name for search; pg_trgm comes from the baseline
                       migration. Rows are deactivated, never deleted.
  * daily_bars       — OHLCV keyed (symbol_id, trade_date). The natural key is
                       deliberate: it makes the idempotent upsert every
                       backfill re-run depends on trivial, and it is the index
                       the range queries want anyway.
  * daily_indicators — same key, one nullable column per indicator. Wide rather
                       than tall because Phase 4 screens on several at once and
                       compiles to SQL; tall storage would mean self-joins.
  * latest_quotes    — keyed by symbol_id alone. A cache of current state, not
                       a history; daily_bars is the history.

User-scoped, and the first such resource since auth itself:

  * watchlists       — names unique per user, not globally.
  * watchlist_items  — a symbol at most once per list. The FK to symbols is
                       RESTRICT rather than CASCADE, so a symbol cannot be
                       hard-deleted out from under a list that watches it.

Revision ID: f78de038978f
Revises: 67424a7be9b0
Create Date: 2026-08-21 20:16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f78de038978f"
down_revision: str | None = "67424a7be9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "symbols",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=True),
        sa.Column("asset_type", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "asset_type IN ('common_stock', 'etf', 'adr', 'reit', 'preferred', 'other')",
            name=op.f("ck_symbols_asset_type_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbols")),
        sa.UniqueConstraint("ticker", name=op.f("uq_symbols_ticker")),
    )
    op.create_index(
        "ix_symbols_name_trgm",
        "symbols",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_symbols_ticker_trgm",
        "symbols",
        ["ticker"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"ticker": "gin_trgm_ops"},
    )
    op.create_table(
        "daily_bars",
        sa.Column("symbol_id", sa.Uuid(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("is_adjusted", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_daily_bars_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("symbol_id", "trade_date", name=op.f("pk_daily_bars")),
    )
    op.create_table(
        "daily_indicators",
        sa.Column("symbol_id", sa.Uuid(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("sma_20", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("sma_50", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("sma_200", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("ema_12", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("ema_26", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("rsi_14", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("macd", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("macd_signal", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("macd_histogram", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("atr_14", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("volume_sma_20", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_daily_indicators_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("symbol_id", "trade_date", name=op.f("pk_daily_indicators")),
    )
    op.create_table(
        "latest_quotes",
        sa.Column("symbol_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("change", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("change_percent", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("day_open", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("day_high", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("day_low", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("previous_close", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("quoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_latest_quotes_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("symbol_id", name=op.f("pk_latest_quotes")),
    )
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_watchlists_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlists")),
        sa.UniqueConstraint("user_id", "name", name="uq_watchlists_user_id_name"),
    )
    op.create_index(
        "ix_watchlists_user_id_created_at", "watchlists", ["user_id", "created_at"], unique=False
    )
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("watchlist_id", sa.Uuid(), nullable=False),
        sa.Column("symbol_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_watchlist_items_symbol_id_symbols"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["watchlists.id"],
            name=op.f("fk_watchlist_items_watchlist_id_watchlists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlist_items")),
        sa.UniqueConstraint(
            "watchlist_id", "symbol_id", name="uq_watchlist_items_watchlist_id_symbol_id"
        ),
    )
    op.create_index(
        "ix_watchlist_items_watchlist_id_position",
        "watchlist_items",
        ["watchlist_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_watchlist_items_watchlist_id_position", table_name="watchlist_items")
    op.drop_table("watchlist_items")
    op.drop_index("ix_watchlists_user_id_created_at", table_name="watchlists")
    op.drop_table("watchlists")
    op.drop_table("latest_quotes")
    op.drop_table("daily_indicators")
    op.drop_table("daily_bars")
    op.drop_index(
        "ix_symbols_ticker_trgm",
        table_name="symbols",
        postgresql_using="gin",
        postgresql_ops={"ticker": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_symbols_name_trgm",
        table_name="symbols",
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.drop_table("symbols")
