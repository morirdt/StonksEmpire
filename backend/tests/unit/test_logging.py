from __future__ import annotations

from app.core.logging import REDACTED, redact_sensitive


def test_sensitive_values_are_redacted() -> None:
    event = {
        "event": "login",
        "password": "hunter2",
        "access_token": "eyJ...",
        "Authorization": "Bearer abc",
        "user_id": 7,
    }

    result = redact_sensitive(None, "info", event)  # type: ignore[arg-type]

    assert result["password"] == REDACTED
    assert result["access_token"] == REDACTED
    assert result["Authorization"] == REDACTED
    assert result["user_id"] == 7
    assert result["event"] == "login"
