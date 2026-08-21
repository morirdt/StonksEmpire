"""Registration, login, token rotation, and logout.

Routes stay thin: parse, call one service method, shape the response. The
refresh cookie is set and cleared here because it is a transport concern, not
something the service should know about.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps import Client, CurrentUser, DbSession
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, RateLimitError
from app.core.rate_limit import TokenBucketLimiter
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserResponse
from app.services.auth_service import AuthService, IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])

# The cookie name, not a secret — the value never appears in source.
REFRESH_COOKIE_NAME = "refresh_token"

_settings = get_settings()

# Per-process buckets; see app/core/rate_limit for why that is acceptable here.
_login_limiter = TokenBucketLimiter(
    capacity=_settings.auth.login_attempts_per_minute,
    window_seconds=60,
)
_register_limiter = TokenBucketLimiter(
    capacity=_settings.auth.register_attempts_per_hour,
    window_seconds=3600,
)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        httponly=True,
        # Lax means a cross-site POST does not carry the cookie, which is what
        # lets the refresh endpoint go without a separate CSRF token.
        samesite="lax",
        # Plain HTTP is only ever used for local development.
        secure=settings.environment != "local",
        # Scoped to the auth routes, so it is not attached to every API call.
        path="/api/v1/auth",
        max_age=int(settings.auth.refresh_token_ttl.total_seconds()),
    )


def _token_response(response: Response, issued: IssuedTokens) -> TokenResponse:
    _set_refresh_cookie(response, issued.refresh_token)
    return TokenResponse(access_token=issued.access_token, expires_in=issued.expires_in)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and sign in",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: DbSession,
    client: Client,
) -> TokenResponse:
    if not _register_limiter.allow(_client_key(request)):
        raise RateLimitError("Too many registration attempts. Try again later.")

    issued = await AuthService(session).register(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        client=client,
    )
    return _token_response(response, issued)


@router.post("/login", response_model=TokenResponse, summary="Sign in")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    client: Client,
) -> TokenResponse:
    if not _login_limiter.allow(_client_key(request)):
        raise RateLimitError("Too many login attempts. Try again later.")

    issued = await AuthService(session).login(
        email=payload.email,
        password=payload.password,
        client=client,
    )
    return _token_response(response, issued)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate the refresh token")
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    client: Client,
) -> TokenResponse:
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        raise AuthenticationError("No refresh token supplied.")

    issued = await AuthService(session).refresh(refresh_token=token, client=client)
    return _token_response(response, issued)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out and revoke the session family",
)
async def logout(request: Request, response: Response, session: DbSession) -> None:
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if token:
        await AuthService(session).logout(refresh_token=token)

    # Cleared unconditionally, so a caller with a stale cookie still ends up
    # logged out rather than retrying against a token we have already dropped.
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/v1/auth")


@router.get("/me", response_model=UserResponse, summary="The signed-in user")
async def me(current_user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(current_user)
