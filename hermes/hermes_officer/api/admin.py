from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from hermes_officer.application.resource_service import RESOURCE_TYPES, ResourceService

router = APIRouter(prefix="/admin", tags=["admin"])


class ResourceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    payload: dict[str, Any] = Field(default_factory=dict)
    status: int = Field(default=1, ge=0, le=1)


def get_resources(request: Request) -> ResourceService:
    return request.app.state.resources


def require_admin_key(
    request: Request,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    expected = request.app.state.settings.admin_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理接口未启用，请配置 ADMIN_API_KEY",
        )
    if not x_admin_key or not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理凭证无效")


AdminGuard = Annotated[None, Depends(require_admin_key)]
Resources = Annotated[ResourceService, Depends(get_resources)]


@router.get("/resource-types")
async def list_resource_types(_: AdminGuard) -> dict[str, Any]:
    return {"data": sorted(RESOURCE_TYPES)}


@router.get("/resources/{resource_type}")
async def list_resources(
    resource_type: str,
    _: AdminGuard,
    resources: Resources,
    enabled_only: bool = False,
) -> dict[str, Any]:
    records = await resources.list(resource_type, enabled_only=enabled_only)
    return {"data": [_view(item) for item in records]}


@router.get("/resources/{resource_type}/{resource_id}")
async def get_resource(
    resource_type: str,
    resource_id: str,
    _: AdminGuard,
    resources: Resources,
) -> dict[str, Any]:
    record = await resources.get(resource_type, resource_id)
    if record is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return {"data": _view(record)}


@router.put("/resources/{resource_type}/{resource_id}")
async def upsert_resource(
    resource_type: str,
    resource_id: str,
    body: ResourceWrite,
    _: AdminGuard,
    resources: Resources,
) -> dict[str, Any]:
    record = await resources.upsert(
        resource_type,
        resource_id,
        name=body.name,
        description=body.description,
        payload=body.payload,
        status=body.status,
    )
    return {"data": _view(record)}


@router.delete("/resources/{resource_type}/{resource_id}")
async def disable_resource(
    resource_type: str,
    resource_id: str,
    _: AdminGuard,
    resources: Resources,
) -> dict[str, Any]:
    if not await resources.disable(resource_type, resource_id):
        raise HTTPException(status_code=404, detail="资源不存在")
    return {"data": {"disabled": True}}


def _view(record: Any) -> dict[str, Any]:
    return {
        "resourceType": record.resource_type,
        "resourceId": record.resource_id,
        "name": record.name,
        "description": record.description,
        "payload": record.payload,
        "status": record.status,
        "version": record.version,
        "createdAt": record.created_at.isoformat(),
        "updatedAt": record.updated_at.isoformat(),
    }
