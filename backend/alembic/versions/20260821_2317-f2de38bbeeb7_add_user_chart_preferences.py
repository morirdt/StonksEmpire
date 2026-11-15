"""add user chart preferences

Phase 3. One table, and the only schema change the phase needs — the bars and
indicators it charts were already stored by Phase 2.

  * user_chart_preferences — how one user wants every chart drawn.

Two things about its shape are deliberate.

The **primary key is the foreign key**: one row per user, scoped globally
rather than per symbol. Someone who wants MACD wants it everywhere, a per-symbol
table would grow with idle browsing while still leaving every newly-visited
symbol back at the defaults, and a PK that is the user id makes "two rows for
one user" unrepresentable and the write a single idempotent upsert.

The indicator lists are **JSONB**, which departs from how daily_indicators is
shaped. That table is wide because Phase 4 filters on it in SQL; this one is
only ever read whole and written whole and is never a query predicate. The
consequence is that the database enforces nothing about the contents — the
Pydantic schema in app/schemas/chart.py is what validates the keys, and it has
to, because nothing here will.

default_range is VARCHAR + CHECK rather than a native enum, per CLAUDE.md.

Revision ID: f2de38bbeeb7
Revises: f78de038978f
Create Date: 2026-08-21 23:17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2de38bbeeb7"
down_revision: str | None = "f78de038978f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chart_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("default_range", sa.String(length=8), nullable=False),
        sa.Column("active_overlays", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active_oscillators", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            "default_range IN ('1M', '3M', '6M', '1Y', '2Y', 'MAX')",
            name=op.f("ck_user_chart_preferences_default_range_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_chart_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_chart_preferences")),
    )


def downgrade() -> None:
    # Preferences are a convenience, not a record: dropping the table loses
    # nothing that cannot be re-chosen from the defaults in two clicks.
    op.drop_table("user_chart_preferences")
