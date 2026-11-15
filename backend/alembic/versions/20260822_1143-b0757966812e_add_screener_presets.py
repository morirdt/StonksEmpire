"""add screener presets

Phase 4, part two. One table: a saved filter tree under a name.

filters is JSONB for the same reason user_chart_preferences stores its
indicator lists that way — it is read whole, written whole, and never a query
predicate. The database therefore enforces nothing about its contents;
app/schemas/screener.py is what does, on the way in and on the way out. The way
out is not belt-and-braces: a stored tree can go stale when a later phase
removes an indicator column, and re-validating on read is what turns that into
a 422 naming the field rather than a query built from a column that is gone.

sort_field holds a **registry key**, not a column name. Nothing interpolates it
into SQL — it is resolved through the field registry like any other key, which
is the rule the whole phase is built on.

sort_direction is VARCHAR + CHECK rather than a native enum, per CLAUDE.md.
Names are unique per user, like watchlists.

Revision ID: b0757966812e
Revises: bafd78ac6767
Create Date: 2026-08-22 11:43

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b0757966812e"
down_revision: str | None = "bafd78ac6767"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "screener_presets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sort_field", sa.String(length=40), nullable=False),
        sa.Column("sort_direction", sa.String(length=4), nullable=False),
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
            "sort_direction IN ('asc', 'desc')",
            name=op.f("ck_screener_presets_sort_direction_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_screener_presets_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_screener_presets")),
        sa.UniqueConstraint("user_id", "name", name="uq_screener_presets_user_id_name"),
    )
    op.create_index(
        "ix_screener_presets_user_id_created_at",
        "screener_presets",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # A preset is a saved convenience, not a record of anything that happened:
    # dropping the table loses screens a user can rebuild, and nothing else.
    op.drop_index("ix_screener_presets_user_id_created_at", table_name="screener_presets")
    op.drop_table("screener_presets")
