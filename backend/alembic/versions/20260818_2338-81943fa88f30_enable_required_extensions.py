"""enable required extensions

Baseline migration. Creates the Postgres extensions the schema depends on
before any table exists:

  * citext   — case-insensitive email addresses (users.email, Phase 1)
  * pg_trgm  — trigram indexes for symbol/ticker search (Phase 2)

Revision ID: 81943fa88f30
Revises:
Create Date: 2026-08-18 23:38

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "81943fa88f30"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("citext", "pg_trgm")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(sa.text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"'))


def downgrade() -> None:
    for extension in reversed(EXTENSIONS):
        op.execute(sa.text(f'DROP EXTENSION IF EXISTS "{extension}"'))
