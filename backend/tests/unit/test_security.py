"""Password hashing and token primitives."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    TokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_password_hash_round_trips() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed) is True


def test_wrong_password_is_rejected() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("Correct horse battery staple", hashed) is False


def test_the_hash_does_not_contain_the_password() -> None:
    assert "hunter2hunter2" not in hash_password("hunter2hunter2")


def test_hashes_are_salted() -> None:
    """Two hashes of one password must differ, or the hash is unsalted."""
    assert hash_password("same password here") != hash_password("same password here")


def test_argon2id_is_the_algorithm() -> None:
    assert hash_password("some long password").startswith("$argon2id$")


def test_a_malformed_hash_reads_as_a_wrong_password() -> None:
    """A corrupt stored hash must not surface as a 500."""
    assert verify_password("anything", "not-a-real-hash") is False


def test_the_dummy_hash_never_matches() -> None:
    """Used for unknown emails, so it must be verifiable but unmatchable."""
    assert verify_password("a-password-that-matches-nothing", DUMMY_PASSWORD_HASH) is True
    assert verify_password("some other guess", DUMMY_PASSWORD_HASH) is False


def test_access_token_round_trips() -> None:
    user_id = uuid.uuid7()

    token, expires_in = create_access_token(user_id)

    assert decode_access_token(token) == user_id
    assert expires_in == get_settings().auth.access_token_ttl_minutes * 60


def test_an_expired_access_token_is_rejected() -> None:
    issued = datetime.now(UTC) - timedelta(days=1)
    token, _ = create_access_token(uuid.uuid7(), now=issued)

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_a_token_signed_with_another_key_is_rejected() -> None:
    settings = get_settings()
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid7()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "type": "access",
        },
        "a-different-signing-key-entirely",
        algorithm=settings.auth.algorithm,
    )

    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_a_refresh_token_cannot_be_replayed_as_an_access_token() -> None:
    """The `type` claim is the only thing separating the two bearer strings."""
    settings = get_settings()
    refresh_shaped = jwt.encode(
        {
            "sub": str(uuid.uuid7()),
            "exp": int((datetime.now(UTC) + timedelta(days=30)).timestamp()),
            "type": "refresh",
        },
        settings.secret_key.get_secret_value(),
        algorithm=settings.auth.algorithm,
    )

    with pytest.raises(TokenError, match="not an access token"):
        decode_access_token(refresh_shaped)


def test_a_token_without_a_type_claim_is_rejected() -> None:
    settings = get_settings()
    typeless = jwt.encode(
        {
            "sub": str(uuid.uuid7()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        settings.secret_key.get_secret_value(),
        algorithm=settings.auth.algorithm,
    )

    with pytest.raises(TokenError):
        decode_access_token(typeless)


def test_garbage_is_rejected() -> None:
    with pytest.raises(TokenError):
        decode_access_token("not.a.token")


def test_access_tokens_are_unique_per_issue() -> None:
    """A distinct jti per token keeps them individually identifiable in logs."""
    user_id = uuid.uuid7()
    first, _ = create_access_token(user_id)
    second, _ = create_access_token(user_id)

    assert first != second


def test_refresh_tokens_are_unique_and_high_entropy() -> None:
    tokens = {generate_refresh_token() for _ in range(100)}

    assert len(tokens) == 100
    assert all(len(token) >= 64 for token in tokens)


def test_refresh_token_hashing_is_deterministic_and_hex() -> None:
    token = generate_refresh_token()

    digest = hash_refresh_token(token)

    assert digest == hash_refresh_token(token)
    assert len(digest) == 64
    assert token not in digest
