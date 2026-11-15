"""Registration, login, token rotation, and logout.

Owns its transaction boundary — ``get_db`` deliberately does not commit — and
raises domain errors from ``app.core.exceptions``, never ``HTTPException``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository

logger = structlog.stdlib.get_logger(__name__)

# One message for every failure mode, so the response cannot be used to probe
# which addresses are registered.
_INVALID_CREDENTIALS = "Invalid email or password."


@dataclass(frozen=True)
class IssuedTokens:
    """What a successful auth call hands back to the route."""

    access_token: str
    expires_in: int
    refresh_token: str
    user: User


@dataclass(frozen=True)
class ClientContext:
    """Where a token request came from, recorded for later session listing."""

    user_agent: str | None = None
    ip: str | None = None


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._tokens = RefreshTokenRepository(session)

    # ------------------------------------------------------------ register --
    async def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None = None,
        client: ClientContext | None = None,
    ) -> IssuedTokens:
        if await self._users.email_exists(email):
            # Accepted trade-off: this discloses that the address is taken. The
            # privacy-preserving alternative needs email delivery (Phase 7).
            raise ConflictError("That email address is already registered.")

        user = User(
            email=email,
            hashed_password=hash_password(password),
            display_name=display_name,
        )
        self._users.add(user)
        await self._session.flush()

        issued = await self._issue(user, family_id=uuid.uuid7(), client=client)
        await self._session.commit()

        logger.info("user_registered", user_id=str(user.id))
        return issued

    # --------------------------------------------------------------- login --
    async def login(
        self,
        *,
        email: str,
        password: str,
        client: ClientContext | None = None,
    ) -> IssuedTokens:
        user = await self._users.get_by_email(email)

        if user is None:
            # Still hash, so an unknown address costs the same as a known one.
            # Without this the response time alone enumerates accounts.
            verify_password(password, DUMMY_PASSWORD_HASH)
            logger.info("login_failed", reason="unknown_email")
            raise AuthenticationError(_INVALID_CREDENTIALS)

        if not verify_password(password, user.hashed_password):
            logger.info("login_failed", reason="bad_password", user_id=str(user.id))
            raise AuthenticationError(_INVALID_CREDENTIALS)

        if not user.is_active:
            # Same message and status as a bad password: whether an account is
            # deactivated is not something an anonymous caller should learn.
            logger.info("login_failed", reason="inactive", user_id=str(user.id))
            raise AuthenticationError(_INVALID_CREDENTIALS)

        issued = await self._issue(user, family_id=uuid.uuid7(), client=client)
        await self._session.commit()

        logger.info("login_succeeded", user_id=str(user.id))
        return issued

    # ------------------------------------------------------------- refresh --
    async def refresh(
        self,
        *,
        refresh_token: str,
        client: ClientContext | None = None,
    ) -> IssuedTokens:
        """Rotate a refresh token, detecting reuse of an already-spent one."""
        stored = await self._tokens.get_by_hash(hash_refresh_token(refresh_token))
        if stored is None:
            raise AuthenticationError("Invalid refresh token.")

        if stored.revoked_at is not None:
            # This token was already rotated. Either it was stolen and replayed,
            # or the legitimate holder's replacement was stolen — we cannot tell
            # which, so the entire family dies. Forcing a fresh login is the
            # intended cost.
            revoked = await self._tokens.revoke_family(stored.family_id)
            await self._session.commit()
            logger.warning(
                "refresh_reuse_detected",
                user_id=str(stored.user_id),
                family_id=str(stored.family_id),
                revoked_count=revoked,
            )
            raise AuthenticationError("Invalid refresh token.")

        if stored.expires_at <= datetime.now(UTC):
            raise AuthenticationError("Refresh token has expired.")

        user = await self._users.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid refresh token.")

        issued = await self._issue(user, family_id=stored.family_id, client=client)
        # The replacement is flushed by _issue, so its id exists to record here.
        replacement = await self._tokens.get_by_hash(hash_refresh_token(issued.refresh_token))
        await self._tokens.revoke(
            stored,
            replaced_by_id=replacement.id if replacement else None,
        )
        await self._session.commit()

        logger.info("token_refreshed", user_id=str(user.id))
        return issued

    # -------------------------------------------------------------- logout --
    async def logout(self, *, refresh_token: str) -> None:
        """Revoke the whole family, so no sibling token survives the logout."""
        stored = await self._tokens.get_by_hash(hash_refresh_token(refresh_token))
        if stored is None:
            # Logout is idempotent: an unknown token is already "logged out".
            return

        await self._tokens.revoke_family(stored.family_id)
        await self._session.commit()
        logger.info("logout", user_id=str(stored.user_id))

    # -------------------------------------------------------------- shared --
    async def _issue(
        self,
        user: User,
        *,
        family_id: uuid.UUID,
        client: ClientContext | None,
    ) -> IssuedTokens:
        """Mint an access/refresh pair. Does not commit."""
        settings = get_settings()
        access_token, expires_in = create_access_token(user.id)
        refresh_token = generate_refresh_token()

        self._tokens.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_refresh_token(refresh_token),
                family_id=family_id,
                expires_at=datetime.now(UTC) + settings.auth.refresh_token_ttl,
                user_agent=client.user_agent if client else None,
                ip=client.ip if client else None,
            )
        )
        await self._session.flush()

        return IssuedTokens(
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=refresh_token,
            user=user,
        )
