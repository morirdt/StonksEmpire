"""Database-backed fixtures.

The shape every later phase inherits:

  * once per session — make sure the test database exists and is migrated;
  * once per test — open a connection, start a transaction, bind the session to
    it, and roll back afterwards.

Rolling back beats truncating: it is faster, it cannot miss a table somebody
added later, and it leaves the schema untouched so migrations run exactly once.
The session joins the outer transaction with a savepoint, so that service code
calling ``commit()`` — which it must, since ``get_db`` does not — still ends up
inside something we can throw away.
"""

from __future__ import annotations

import asyncio
import pathlib
import uuid
from collections.abc import AsyncGenerator, Generator

import asyncpg
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.api.v1.routes import auth as auth_routes
from app.core.config import get_settings
from app.db.session import get_db
from app.main import create_app

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]


async def _ensure_database_exists() -> None:
    """Create the test database if it is not already there.

    Connects to the maintenance database, because you cannot CREATE DATABASE
    from inside the database you are creating.
    """
    db = get_settings().db
    connection = await asyncpg.connect(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password.get_secret_value(),
        database="postgres",
    )
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db.name)
        if not exists:
            # Identifier cannot be parameterised; db.name comes from config,
            # never from a request.
            await connection.execute(f'CREATE DATABASE "{db.name}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Generator[None]:
    """Bring the test database up to head exactly once per test session."""
    asyncio.run(_ensure_database_exists())

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limiters() -> Generator[None]:
    """The limiters are module-level, so state would leak between tests."""
    auth_routes._login_limiter.reset()
    auth_routes._register_limiter.reset()
    yield


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine(get_settings().db.url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            # Service code commits; this turns that into a savepoint release so
            # the outer rollback below still undoes everything.
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """An HTTP client whose routes share the test's rolled-back session."""
    app = create_app()

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    # https, not http: the refresh cookie is Secure in every environment except
    # `local`, and a cookie jar will not return a Secure cookie over plain HTTP.
    # Speaking https here exercises the real production cookie flags.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as http:
        yield http

    app.dependency_overrides.clear()


def unique_email(prefix: str = "user") -> str:
    """A fresh address per call, so tests cannot collide on the unique index."""
    return f"{prefix}-{uuid.uuid7().hex[:12]}@example.com"


VALID_PASSWORD = "a-sufficiently-long-password"


@pytest.fixture
async def registered_user(client: AsyncClient) -> dict[str, str]:
    """A registered account, plus the credentials used to create it."""
    email = unique_email()
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": VALID_PASSWORD, "display_name": "Test User"},
    )
    assert response.status_code == 201, response.text
    return {
        "email": email,
        "password": VALID_PASSWORD,
        "access_token": response.json()["access_token"],
    }


@pytest.fixture
async def auth_client(
    client: AsyncClient,
    registered_user: dict[str, str],
) -> AsyncClient:
    """A client already carrying a bearer token for ``registered_user``."""
    client.headers["Authorization"] = f"Bearer {registered_user['access_token']}"
    return client
