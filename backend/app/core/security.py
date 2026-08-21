"""Password hashing and token primitives.

Pure functions only — no database access, no FastAPI imports. Everything here
is cheap to unit test in isolation, which matters because these are the pieces
most likely to be subtly wrong.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, Final

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

_password_hash = PasswordHash.recommended()

# S105 reads any string near a token-ish name as a credential; this is a JWT
# claim *value*, and the whole point is that it is a public constant.
ACCESS_TOKEN_TYPE: Final = "access"  # noqa: S105

# 48 bytes of entropy, url-safe. Long enough that brute force is irrelevant,
# which is why the stored form is a plain SHA-256 rather than Argon2.
REFRESH_TOKEN_BYTES: Final = 48

# Verified against when an email is unknown, so that a login attempt costs the
# same whether or not the account exists. Without this, response time alone
# reveals which addresses are registered.
DUMMY_PASSWORD_HASH: Final = _password_hash.hash("a-password-that-matches-nothing")


class TokenError(Exception):
    """A token was absent, malformed, expired, or of the wrong type."""


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Check a password against a hash, returning False rather than raising."""
    try:
        return _password_hash.verify(password, hashed)
    except Exception:
        # A malformed or truncated hash in the database must read as "wrong
        # password", never as a 500 that tells the caller something is odd.
        return False


def create_access_token(
    subject: uuid.UUID,
    *,
    now: datetime | None = None,
) -> tuple[str, int]:
    """Return a signed access token and its lifetime in seconds.

    ``now`` is injectable so expiry behaviour can be tested without sleeping.
    """
    settings = get_settings()
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + settings.auth.access_token_ttl

    claims: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid.uuid7()),
        "type": ACCESS_TOKEN_TYPE,
    }
    token = jwt.encode(
        claims,
        settings.secret_key.get_secret_value(),
        algorithm=settings.auth.algorithm,
    )
    return token, int(settings.auth.access_token_ttl.total_seconds())


def decode_access_token(token: str) -> uuid.UUID:
    """Return the subject of a valid access token.

    Rejects any token whose ``type`` is not ``access``, so a refresh token can
    never be replayed as an access token even though both are bearer strings.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.auth.algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if claims.get("type") != ACCESS_TOKEN_TYPE:
        raise TokenError("Token is not an access token.")

    try:
        return uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise TokenError("Token subject is not a valid id.") from exc


def generate_refresh_token() -> str:
    """A new opaque refresh token. Never a JWT — it must be revocable."""
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """The stored form of a refresh token.

    SHA-256, not Argon2: the token is high-entropy random data, so there is
    nothing to brute-force, and lookup has to be a fast indexed equality match.
    Argon2 is for low-entropy human input.
    """
    return hashlib.sha256(token.encode()).hexdigest()
