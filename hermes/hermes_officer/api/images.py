from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from hermes_officer.application.image_service import ImageWorkspaceService


router = APIRouter(prefix="/images", tags=["images"])


class GenerateImageBody(BaseModel):
    visitor_id: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=8_000)
    mode: str | None = Field(default=None, pattern="^(images|edits)$")
    reference_ids: list[str] = Field(default_factory=list, max_length=10)
    mask_reference_ids: list[str] = Field(default_factory=list, max_length=10)
    size: str | None = None
    count: int = Field(default=1, ge=1, le=10)


def _service(request: Request) -> ImageWorkspaceService:
    return request.app.state.images


def _serialize(value) -> dict:
    payload = asdict(value)
    for key, item in tuple(payload.items()):
        if hasattr(item, "isoformat"):
            payload[key] = item.isoformat()
    if hasattr(value, "preview_url"):
        payload["preview_url"] = value.preview_url
    return payload


@router.post("/references", status_code=201)
async def upload_reference(
    request: Request,
    file: UploadFile = File(...),
    x_visitor_id: str = Header(alias="X-Visitor-ID"),
):
    try:
        reference = await _service(request).save_reference(
            x_visitor_id,
            file.filename or "reference.png",
            await file.read(),
            file.content_type or "application/octet-stream",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(reference)


@router.get("/references")
async def list_references(request: Request, x_visitor_id: str = Header(alias="X-Visitor-ID")):
    return {"items": [_serialize(item) for item in await _service(request).list_references(x_visitor_id)]}


@router.get("/references/{reference_id}/preview")
async def preview_reference(reference_id: str, request: Request, visitor_id: str = Query(min_length=1)):
    reference = await _service(request).get_reference(visitor_id, reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="参考图不存在")
    path = Path(reference.stored_path).resolve()
    if _service(request).reference_path not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="参考图文件不存在")
    return FileResponse(path, filename=reference.filename, media_type=reference.content_type, content_disposition_type="inline")


@router.post("/generate", status_code=202)
async def generate_image(body: GenerateImageBody, request: Request):
    try:
        run = await _service(request).generate(
            body.visitor_id,
            body.prompt,
            mode=body.mode,
            reference_ids=body.reference_ids,
            mask_reference_ids=body.mask_reference_ids,
            size=body.size,
            count=body.count,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(run)


@router.get("/history")
async def image_history(request: Request, x_visitor_id: str = Header(alias="X-Visitor-ID"), limit: int = 50):
    history = await _service(request).list_history(x_visitor_id, limit)
    return {"items": [_serialize(item) for item in history]}
