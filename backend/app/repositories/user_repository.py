"""Queries against ``users``.

Earns its place over raw session use because email lookup is case-insensitive
and is the hot path of every login.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive by virtue of the CITEXT column, not by LOWER()."""
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        result = await self._session.execute(select(User.id).where(User.email == email).limit(1))
        return result.scalar_one_or_none() is not None

    def add(self, user: User) -> User:
        """Stage a new user. The caller owns the commit."""
        self._session.add(user)
        return user
