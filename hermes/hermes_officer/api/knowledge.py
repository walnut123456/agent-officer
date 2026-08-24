from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl
from sse_starlette import EventSourceResponse, ServerSentEvent

from hermes_officer.application.knowledge_service import KnowledgeService


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class CreateKnowledgeBaseBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    chunk_size: int = Field(default=800, ge=200, le=4_000, description="每个分块的 Token 硬上限")
    chunk_overlap: int = Field(default=80, ge=0, le=1_000, description="相邻同章节分块的 Token 重叠预算")


class AddWebSourceBody(BaseModel):
    url: HttpUrl


class KnowledgeQueryBody(BaseModel):
    question: str = Field(min_length=1, max_length=8_000)


def _service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge


def _json_view(value) -> dict:
    payload = asdict(value)
    for key, item in tuple(payload.items()):
        if hasattr(item, "isoformat"):
            payload[key] = item.isoformat()
    if hasattr(value, "preview_url"):
        payload["preview_url"] = value.preview_url
        payload["download_url"] = value.download_url
    return payload


@router.get("")
async def list_knowledge_bases(request: Request):
    return {"items": [_json_view(item) for item in await _service(request).list_knowledge_bases()]}


@router.post("", status_code=201)
async def create_knowledge_base(body: CreateKnowledgeBaseBody, request: Request):
    try:
        created = await _service(request).create_knowledge_base(
            body.name,
            body.description,
            chunk_size=body.chunk_size,
            chunk_overlap=body.chunk_overlap,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _json_view(created)


@router.delete("/{kb_id}")
async def delete_knowledge_base(kb_id: str, request: Request):
    try:
        count = await _service(request).delete_knowledge_base(kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"kb_id": kb_id, "deleted_document_count": count}


@router.get("/{kb_id}/documents")
async def list_documents(kb_id: str, request: Request):
    try:
        documents = await _service(request).list_documents(kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": [_json_view(item) for item in documents]}


@router.post("/{kb_id}/documents", status_code=201)
async def upload_document(kb_id: str, request: Request, file: UploadFile = File(...)):
    try:
        document = await _service(request).ingest_file(
            kb_id,
            file.filename or "document.txt",
            await file.read(),
            file.content_type or "application/octet-stream",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _json_view(document)


@router.post("/{kb_id}/web-sources", status_code=202)
async def add_web_source(kb_id: str, body: AddWebSourceBody, request: Request):
    try:
        document = await _service(request).ingest_url(kb_id, str(body.url))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _json_view(document)


@router.delete("/{kb_id}/documents/{document_id}")
async def delete_document(kb_id: str, document_id: str, request: Request):
    try:
        await _service(request).delete_document(kb_id, document_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True, "document_id": document_id}


@router.get("/documents/{document_id}/content")
async def get_document_content(document_id: str, request: Request):
    document = await _service(request).get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return {
        "document_id": document.document_id,
        "title": document.title,
        "status": document.status,
        "content_format": "markdown",
        "content": document.canonical_content,
        "error_message": document.error_message,
    }


async def _file_response(document_id: str, request: Request, *, download: bool):
    document = await _service(request).get_document(document_id)
    if document is None or document.source_type != "file" or not document.stored_path:
        raise HTTPException(status_code=404, detail="原始文件不存在")
    path = Path(document.stored_path).resolve()
    storage = _service(request).storage_path
    if storage not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="原始文件不存在")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path,
        filename=document.title,
        media_type=document.content_type,
        content_disposition_type=disposition,
    )


@router.get("/documents/{document_id}/preview")
async def preview_document(document_id: str, request: Request):
    return await _file_response(document_id, request, download=False)


@router.get("/documents/{document_id}/download")
async def download_document(document_id: str, request: Request):
    return await _file_response(document_id, request, download=True)


@router.post("/{kb_id}/query")
async def query_knowledge_base(kb_id: str, body: KnowledgeQueryBody, request: Request):
    service = _service(request)

    async def stream():
        try:
            async for chunk in service.stream_answer(kb_id, body.question):
                yield ServerSentEvent(data=json.dumps({"content": chunk}, ensure_ascii=False))
        except LookupError as exc:
            yield ServerSentEvent(data=json.dumps({"error": str(exc)}, ensure_ascii=False), event="error")
        except ValueError as exc:
            yield ServerSentEvent(data=json.dumps({"error": str(exc)}, ensure_ascii=False), event="error")
        yield ServerSentEvent(data="[DONE]")

    return EventSourceResponse(stream(), ping=15)
