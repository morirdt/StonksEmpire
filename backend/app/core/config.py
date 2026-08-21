"""Application configuration.

All settings are sourced from the environment (or a local ``.env``). Nested
groups use a double-underscore delimiter, e.g. ``DB__HOST=localhost``.
"""

from __future__ import annotations

from functools import lru_cache
from datetime import timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "ci", "prod"]

# config.py -> core -> app -> backend -> <repo root>
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class DatabaseSettings(BaseModel):
    """Postgres connection settings.

    Note the host port for local Docker Postgres defaults to 5433, not 5432:
    see ``docker-compose.yml`` for why.
    """

    host: str = "localhost"
    port: int = 5433
    user: str = "stonks"
    password: SecretStr = SecretStr("")
    name: str = "stonks_empire"

    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_pre_ping: bool = True

    @property
    def url(self) -> str:
        """SQLAlchemy async URL (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def safe_url(self) -> str:
        """Same URL with the password masked — safe to log."""
        return f"postgresql+asyncpg://{self.user}:***@{self.host}:{self.port}/{self.name}"


class AuthSettings(BaseModel):
    """Token lifetimes and signing algorithm.

    Access tokens are short-lived because they cannot be revoked; refresh
    tokens are long-lived because they can (see ``refresh_tokens``).
    """

    algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    # Rate limits, per client IP. In-process only until Redis arrives.
    login_attempts_per_minute: int = 10
    register_attempts_per_hour: int = 5

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(minutes=self.access_token_ttl_minutes)

    @property
    def refresh_token_ttl(self) -> timedelta:
        return timedelta(days=self.refresh_token_ttl_days)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Anchored to the repo, not the process CWD: the API runs from
        # backend/ while the single source-of-truth .env sits at the root.
        # A backend/.env, if present, wins — handy for one-off overrides.
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # --- app ---
    project_name: str = "Stonks Empire"
    version: str = "0.1.0"
    environment: Environment = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- security ---
    # No default on purpose: a missing SECRET_KEY must fail at startup rather
    # than silently fall back to a well-known value.
    secret_key: SecretStr

    # --- http ---
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- logging / observability ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool | None = None  # None => decided by environment
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = 0.0

    # --- nested groups ---
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @property
    def is_testing(self) -> bool:
        return self.environment in ("test", "ci")

    @property
    def use_json_logs(self) -> bool:
        return self.log_json if self.log_json is not None else self.is_prod

    @property
    def docs_url(self) -> str | None:
        """OpenAPI docs are not exposed in production."""
        return None if self.is_prod else "/docs"

    @property
    def openapi_url(self) -> str | None:
        return None if self.is_prod else "/openapi.json"

    @model_validator(mode="after")
    def _enforce_production_hardening(self) -> Self:
        if not self.is_prod:
            return self
        if len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters in production")
        if not self.db.password.get_secret_value():
            raise ValueError("DB__PASSWORD must be set in production")
        if self.debug:
            raise ValueError("DEBUG must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — the single entry point for configuration."""
    # Every field is either defaulted or supplied by the environment.
    return Settings()
