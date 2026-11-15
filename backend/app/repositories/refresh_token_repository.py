"""Queries against ``refresh_tokens``.

Earns its place because lookup is by hash and revocation is by family — neither
is a one-liner, and both are security-critical.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    def add(self, token: RefreshToken) -> RefreshToken:
        """Stage a new token. The caller owns the commit."""
        self._session.add(token)
        return token

    async def revoke_family(self, family_id: uuid.UUID, *, at: datetime | None = None) -> int:
        """Revoke every unrevoked token in a rotation chain.

        Used both for logout and for reuse detection, where the whole lineage
        has to die because we cannot tell the thief's token from the victim's.
        Returns the number of rows actually revoked.
        """
        moment = at or datetime.now(UTC)
        # An UPDATE always yields a CursorResult; the generic execute() overload
        # is what loses that, so rowcount needs the narrower type back.
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.family_id == family_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=moment)
            ),
        )
        return result.rowcount

    async def revoke(
        self,
        token: RefreshToken,
        *,
        replaced_by_id: uuid.UUID | None = None,
        at: datetime | None = None,
    ) -> None:
        """Mark one token spent, optionally recording its successor."""
        token.revoked_at = at or datetime.now(UTC)
        if replaced_by_id is not None:
            token.replaced_by_id = replaced_by_id
