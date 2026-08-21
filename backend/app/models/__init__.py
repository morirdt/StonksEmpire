"""SQLAlchemy models.

Every model module MUST be imported here. ``alembic/env.py`` imports this
package to populate ``Base.metadata``; a model that is not reachable from this
file is invisible to autogenerate, and Alembic will emit a migration that drops
its table.
"""

from __future__ import annotations

from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__: list[str] = ["RefreshToken", "User"]
