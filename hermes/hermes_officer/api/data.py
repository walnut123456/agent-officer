from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from hermes_officer.application.data_service import DataWorkspaceService


router = APIRouter(prefix="/data", tags=["data"])


class DataQueryBody(BaseModel):
    visitor_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=8_000)


def _service(request: Request) -> DataWorkspaceService:
    return request.app.state.data_workspace


def _serialize(value) -> dict:
    payload = asdict(value)
    for key, item in tuple(payload.items()):
        if hasattr(item, "isoformat"):
            payload[key] = item.isoformat()
    return payload


@router.post("/datasets", status_code=201)
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    x_visitor_id: str = Header(alias="X-Visitor-ID"),
):
    try:
        dataset = await _service(request).upload(x_visitor_id, file.filename or "dataset.csv", await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(dataset)


@router.get("/datasets")
async def list_datasets(request: Request, x_visitor_id: str = Header(alias="X-Visitor-ID")):
    return {"items": [_serialize(item) for item in await _service(request).list_datasets(x_visitor_id)]}


@router.post("/datasets/{dataset_id}/query")
async def query_dataset(dataset_id: str, body: DataQueryBody, request: Request):
    try:
        result = await _service(request).query(body.visitor_id, dataset_id, body.question)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(result)
