from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.config import DatabaseSettings, MarketDataSettings, Settings


def _settings(**overrides: object) -> Settings:
    """Build Settings in isolation from the developer's .env.

    `_env_file=None` disables dotenv loading, so these tests exercise the
    declared defaults rather than whatever happens to be on this machine.
    """
    defaults: dict[str, object] = {
        "secret_key": "x" * 48,
        "environment": "local",
        "_env_file": None,
    }
    return Settings(**(defaults | overrides))  # type: ignore[arg-type]


def test_database_url_uses_asyncpg() -> None:
    db = DatabaseSettings(host="db", port=5432, user="u", name="n")
    assert db.url.startswith("postgresql+asyncpg://")


def test_safe_url_masks_the_password() -> None:
    db = DatabaseSettings(password="hunter2")  # type: ignore[arg-type]

    assert "hunter2" not in db.safe_url
    assert "***" in db.safe_url
    assert "hunter2" in db.url  # the real URL still works


def test_docs_are_disabled_in_production() -> None:
    prod = _settings(environment="prod", db=DatabaseSettings(password="pw"))  # type: ignore[arg-type]
    assert prod.docs_url is None
    assert prod.openapi_url is None
    assert _settings().docs_url == "/docs"


def test_production_rejects_a_short_secret_key() -> None:
    with pytest.raises(PydanticValidationError, match="at least 32 characters"):
        _settings(
            environment="prod",
            secret_key="too-short",
            db=DatabaseSettings(password="pw"),  # type: ignore[arg-type]
        )


def test_production_requires_a_database_password() -> None:
    # Passed explicitly rather than relying on the ambient environment, which
    # the test conftest populates with a working DB__PASSWORD.
    with pytest.raises(PydanticValidationError, match="DB__PASSWORD"):
        _settings(environment="prod", db=DatabaseSettings(password=""))  # type: ignore[arg-type]


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(PydanticValidationError, match="DEBUG must be false"):
        _settings(
            environment="prod",
            debug=True,
            db=DatabaseSettings(password="pw"),  # type: ignore[arg-type]
        )


def test_local_environment_stays_permissive() -> None:
    local = _settings(debug=True)
    assert local.debug is True
    assert local.is_prod is False


def test_json_logging_defaults_to_production_only() -> None:
    assert _settings(environment="local").use_json_logs is False
    assert (
        _settings(
            environment="prod",
            db=DatabaseSettings(password="pw"),  # type: ignore[arg-type]
        ).use_json_logs
        is True
    )
    assert _settings(environment="local", log_json=True).use_json_logs is True


def test_market_data_defaults_to_the_fake_provider() -> None:
    """A fresh clone must come up working without anyone holding an API key."""
    assert _settings().market_data.provider == "fake"


def test_fake_provider_needs_no_credentials() -> None:
    MarketDataSettings(provider="fake")


@pytest.mark.parametrize("provider", ["finnhub", "tiingo"])
def test_selecting_a_real_provider_without_its_key_fails_at_startup(provider: str) -> None:
    """Fail on boot, not on the first request that happens to need the key."""
    with pytest.raises(PydanticValidationError, match="API_KEY"):
        MarketDataSettings(provider=provider)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("provider", "field"),
    [("finnhub", "finnhub_api_key"), ("tiingo", "tiingo_api_key")],
)
def test_a_real_provider_with_its_key_validates(provider: str, field: str) -> None:
    settings = MarketDataSettings(**{"provider": provider, field: "secret"})  # type: ignore[arg-type]

    assert settings.provider == provider


def test_the_other_providers_key_does_not_satisfy_the_check() -> None:
    with pytest.raises(PydanticValidationError, match="TIINGO_API_KEY"):
        MarketDataSettings(provider="tiingo", finnhub_api_key="secret")  # type: ignore[arg-type]


def test_quote_ttl_is_exposed_as_a_timedelta() -> None:
    assert MarketDataSettings(quote_ttl_seconds=90).quote_ttl.total_seconds() == 90
