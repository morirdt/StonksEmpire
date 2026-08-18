"""Async engine, session factory, and the request-scoped session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.db.url,
    echo=_settings.db.echo,
    pool_size=_settings.db.pool_size,
    max_overflow=_settings.db.max_overflow,
    pool_pre_ping=_settings.db.pool_pre_ping,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Yield a session per request, rolling back if the handler raised.

    Services own their own ``commit()`` calls — the dependency deliberately
    does not commit for them, so a transaction boundary is always explicit.
    """
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database() -> bool:
    """Cheap liveness probe used by the readiness endpoint."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_engine() -> None:
    await engine.dispose()
