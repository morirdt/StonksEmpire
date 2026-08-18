"""Metadata about the running API — the first end-to-end route."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import AppSettings

router = APIRouter(prefix="/meta", tags=["meta"])


class MetaResponse(BaseModel):
    name: str
    version: str
    environment: str
    api_version: str


@router.get("", response_model=MetaResponse, summary="API metadata")
async def get_meta(settings: AppSettings) -> MetaResponse:
    return MetaResponse(
        name=settings.project_name,
        version=settings.version,
        environment=settings.environment,
        api_version="v1",
    )
