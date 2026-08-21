"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.middleware import AccessLogMiddleware, CorrelationIdMiddleware
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine

logger = structlog.stdlib.get_logger(__name__)


def custom_generate_unique_id(route: APIRoute) -> str:
    """Produce short, stable operation ids.

    FastAPI's default appends the path and method, which yields unusable names
    in the generated TypeScript client. ``tag_name`` reads far better.
    """
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


def _init_sentry(settings: Settings) -> None:
    if settings.sentry_dsn is None:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        release=settings.version,
        send_default_pii=False,
    )
    logger.info("sentry_initialised", environment=settings.environment)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    logger.info(
        "application_starting",
        environment=settings.environment,
        version=settings.version,
        database=settings.db.safe_url,
    )
    yield
    await dispose_engine()
    logger.info("application_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.use_json_logs)
    _init_sentry(settings)

    app = FastAPI(
        title=settings.project_name,
        version=settings.version,
        description="Stock trading utilities: watchlists, screeners, alerts, journal.",
        docs_url=settings.docs_url,
        redoc_url=None,
        openapi_url=settings.openapi_url,
        lifespan=lifespan,
        generate_unique_id_function=custom_generate_unique_id,
    )

    # Middleware runs bottom-up: correlation ids must be bound before the
    # access log fires, so it is added last.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
