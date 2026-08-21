"""Liveness and readiness probes.

Deliberately unversioned and mounted outside ``/api`` — orchestrators should
not have to track the API version to know whether the process is alive.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.session import check_database
from app.integrations.market_data import market_data_is_healthy

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, bool]
    #: Checks that are failing but are not fatal. ``status`` stays ``ready``.
    degraded: list[str] = []


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Is the process up? Never touches a dependency."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.version,
        environment=settings.environment,
    )


#: Dependencies the app genuinely cannot serve without. Everything else is
#: reported by name and allowed to fail without taking the process out of
#: rotation — a market data provider having a bad afternoon must not look
#: identical to a lost database connection.
_REQUIRED_CHECKS = ("database",)


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(response: Response) -> ReadinessResponse:
    """Can the process serve traffic?

    Note that a ``false`` in ``checks`` does not necessarily mean ``not_ready``:
    only the checks in ``_REQUIRED_CHECKS`` decide the status. The rest are
    surfaced so an operator can see a degraded dependency without a pager going
    off for it.
    """
    checks = {
        "database": await check_database(),
        "market_data": market_data_is_healthy(),
    }
    ok = all(checks[name] for name in _REQUIRED_CHECKS)
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ok else "not_ready",
        checks=checks,
        degraded=sorted(name for name, passed in checks.items() if not passed and ok),
    )
