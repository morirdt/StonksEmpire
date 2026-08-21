"""Shared dependency aliases.

Annotated aliases keep route signatures readable and give every route one
obvious way to obtain a collaborator.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.core.security import TokenError, decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.auth_service import ClientContext

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]

# auto_error=False so a missing header raises our own domain error and comes
# back as problem+json, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the bearer token to an active user.

    Inactive accounts are rejected here as well as at login, so deactivating a
    user takes effect within one access-token lifetime instead of at next login.
    """
    if credentials is None:
        raise AuthenticationError("Not authenticated.")

    try:
        user_id = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc

    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired token.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_client_context(request: Request) -> ClientContext:
    """Best-effort origin of the request, for the session list in a later phase."""
    return ClientContext(
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )


Client = Annotated[ClientContext, Depends(get_client_context)]
