"""drop alerts name

Alerts are identified by what they watch (ticker and condition, or preset), so
the free-text name is gone. The downgrade restores the column with a
placeholder, since the original names are not recoverable.

Revision ID: ffcc04d73065
Revises: 9967015f727d
Create Date: 2026-11-15 22:16:13.338869

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'ffcc04d73065'
down_revision: str | None = '9967015f727d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('alerts', 'name')


def downgrade() -> None:
    # Existing rows need a value before NOT NULL can hold; the default is
    # dropped afterwards so the column matches the old model exactly.
    op.add_column(
        'alerts',
        sa.Column('name', sa.VARCHAR(length=80), nullable=False, server_default='Alert'),
    )
    op.alter_column('alerts', 'name', server_default=None)
