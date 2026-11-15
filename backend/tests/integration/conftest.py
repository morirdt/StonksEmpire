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
import contextlib
import pathlib
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.api.health as health_module
from alembic import command
from app.api.v1.routes import auth as auth_routes
from app.core.config import get_settings
from app.core.market_calendar import MARKET_TZ
from app.core.redis import REDIS_UNAVAILABLE, get_redis
from app.db.session import get_db
from app.main import create_app
from app.models.symbol import Symbol

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
async def _clean_redis() -> AsyncGenerator[None]:
    """Empty the test Redis database before each test.

    Postgres state is rolled back per test, but Redis has no transaction to roll
    back, so it is flushed instead. That covers the login limiter, the provider
    bucket, and the breaker in one call. It is database 15, set in
    ``tests/conftest.py``, so the dev stack's data is never touched. The
    in-process fallback buckets are reset as well.
    """
    # os.environ outranks conftest's defaults, so a shell that exports the dev
    # REDIS__URL would aim this FLUSHDB at real data. Refuse rather than guess.
    assert get_settings().is_testing, "refusing to FLUSHDB outside a test environment"
    with contextlib.suppress(*REDIS_UNAVAILABLE):
        await get_redis().flushdb()
    await auth_routes._login_limiter.reset()
    await auth_routes._register_limiter.reset()
    yield


@pytest.fixture
async def db_connection() -> AsyncGenerator[AsyncConnection]:
    """One connection per test, inside a transaction that is rolled back."""
    engine = create_async_engine(get_settings().db.url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            if transaction.is_active:
                await transaction.rollback()
    await engine.dispose()


@pytest.fixture
def session_factory(db_connection: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """What a job takes in place of the app's ``SessionFactory``.

    Every session it opens joins the test's transaction through a savepoint, so
    a job's commits are rolled back with everything else, and the test sees
    what the job wrote.
    """
    return async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        # Service code commits; this turns that into a savepoint release so the
        # outer rollback still undoes everything.
        join_transaction_mode="create_savepoint",
    )


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    session = session_factory()
    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
def database_check_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """For ``/ready`` tests about some other check.

    The real database check runs on the app's pooled engine, not the test's
    connection. Its pooled connections then outlive the test's event loop, and
    asyncpg warns when they are collected. The database check has its own tests
    in ``tests/unit/test_health.py``.
    """

    async def ok() -> bool:
        return True

    monkeypatch.setattr(health_module, "check_database", ok)


class FrozenClock:
    """A clock for job tests. Set it in ET, because that is how the rules are stated."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def set_et(self, *args: int) -> None:
        self.at = datetime(*args, tzinfo=MARKET_TZ).astimezone(UTC)  # type: ignore[misc]

    def advance(self, **kwargs: float) -> None:
        self.at += timedelta(**kwargs)


@pytest.fixture
def clock() -> FrozenClock:
    """Wednesday 18 November 2026, 11:00 ET: an ordinary session, mid-morning."""
    frozen = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    frozen.set_et(2026, 11, 18, 11, 0)
    return frozen


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


#: Enough of a universe for watchlists to point at. Deliberately tiny — these
#: tests are about scoping and CRUD, not about the size of the seed.
SEED_TICKERS = (
    ("AAPL", "Apple Inc.", "NASDAQ", "common_stock"),
    ("MSFT", "Microsoft Corporation", "NASDAQ", "common_stock"),
    ("SPY", "SPDR S&P 500 ETF Trust", "NYSEARCA", "etf"),
)


@pytest.fixture
async def seeded_symbols(db_session: AsyncSession) -> list[Symbol]:
    """A handful of symbols, inside the test's rolled-back transaction."""
    symbols = [
        Symbol(
            ticker=ticker,
            name=name,
            exchange=exchange,
            asset_type=asset_type,
            currency="USD",
            is_active=True,
        )
        for ticker, name, exchange, asset_type in SEED_TICKERS
    ]
    db_session.add_all(symbols)
    await db_session.commit()
    return symbols
