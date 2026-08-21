"""Request and response bodies for the auth endpoints."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

# 12 is a real floor rather than theatre. The 128 ceiling is an Argon2 denial
# of service guard: hashing cost grows with input, and nobody needs more.
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

Password = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Password
    display_name: str | None = Field(default=None, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Password


class TokenResponse(BaseModel):
    """The access token. The refresh token travels as an HttpOnly cookie."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 — a scheme name, not a credential.
    expires_in: int
