from __future__ import annotations

import json
from typing import Any, Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from hermes_officer.application.conversation_service import ConversationService
from hermes_officer.application.resource_service import ResourceService
from hermes_officer.domain.agent import AgentEventType, AgentStrategy

router = APIRouter(prefix="/agent", tags=["platform"])


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(item.title() for item in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class Envelope(ApiModel):
    code: str = "0000"
    info: str = "success"
    data: Any = None


class VisitorView(ApiModel):
    visitor_id: str
    username: str | None
    named: bool


class NamingRequest(ApiModel):
    username: str = Field(min_length=2, max_length=32)


class ConversationCreateRequest(ApiModel):
    session_id: str = Field(min_length=8, max_length=64)
    title: str = Field(default="新对话", max_length=128)


class MessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=100_000)
    strategy: AgentStrategy = AgentStrategy.AUTO
    knowledge_base_id: str = Field(default="", max_length=64)
    output_format: str = Field(default="chat", pattern="^(chat|html|docs|ppt|table)$")
    workflow_id: str = Field(default="", max_length=64)


class MemoryNoteRequest(ApiModel):
    note_type: str
    content: str = Field(min_length=1, max_length=100_000)
    request_id: str = Field(default="", max_length=64)


def get_service(request: Request) -> ConversationService:
    return request.app.state.conversations


def get_resources(request: Request) -> ResourceService:
    return request.app.state.resources


def resolve_visitor_id(
    request: Request,
    x_visitor_id: Annotated[str | None, Header(alias="X-Visitor-Id")] = None,
) -> str:
    return x_visitor_id or request.cookies.get("hermes_visitor_id") or uuid4().hex


VisitorId = Annotated[str, Depends(resolve_visitor_id)]
Service = Annotated[ConversationService, Depends(get_service)]
Resources = Annotated[ResourceService, Depends(get_resources)]


@router.get("/visitor/bootstrap", response_model=Envelope)
async def bootstrap_visitor(
    response: Response,
    visitor_id: VisitorId,
    service: Service,
    x_username: Annotated[str | None, Header(alias="X-Username")] = None,
) -> Envelope:
    profile = await service.bootstrap_visitor(visitor_id, x_username)
    response.set_cookie(
        "hermes_visitor_id",
        profile.visitor_id,
        httponly=True,
        samesite="lax",
        max_age=365 * 24 * 60 * 60,
    )
    response.headers["X-Visitor-Id"] = profile.visitor_id
    return Envelope(data=VisitorView(
        visitor_id=profile.visitor_id,
        username=profile.username,
        named=profile.named,
    ).model_dump(by_alias=True))


@router.post("/visitor/naming", response_model=Envelope)
async def name_visitor(
    body: NamingRequest,
    response: Response,
    visitor_id: VisitorId,
    service: Service,
) -> Envelope:
    profile = await service.name_visitor(visitor_id, body.username)
    response.set_cookie(
        "hermes_visitor_id",
        profile.visitor_id,
        httponly=True,
        samesite="lax",
        max_age=365 * 24 * 60 * 60,
    )
    return Envelope(data=VisitorView(
        visitor_id=profile.visitor_id,
        username=profile.username,
        named=profile.named,
    ).model_dump(by_alias=True))


@router.get("/role-library/list", response_model=Envelope)
async def list_roles(resources: Resources) -> Envelope:
    records = await resources.list("agent", enabled_only=True)
    return Envelope(data=[
        {
            "agentId": item.resource_id,
            "agentName": item.name,
            "description": item.description,
            "defaultRole": item.resource_id == "default-assistant",
            "available": True,
        }
        for item in records
    ])


@router.post("/conversation/sessions", response_model=Envelope)
async def create_conversation(
    body: ConversationCreateRequest,
    visitor_id: VisitorId,
    service: Service,
) -> Envelope:
    result = await service.create_conversation(visitor_id, body.session_id, body.title)
    return Envelope(data=_conversation_view(result))


@router.get("/conversation/sessions", response_model=Envelope)
async def list_conversations(
    visitor_id: VisitorId,
    service: Service,
    limit: int = 20,
) -> Envelope:
    items = await service.list_conversations(visitor_id, limit)
    return Envelope(data=[_conversation_view(item) for item in items])


@router.get("/conversation/sessions/{session_id}", response_model=Envelope)
async def conversation_detail(
    session_id: str,
    visitor_id: VisitorId,
    service: Service,
) -> Envelope:
    messages = await service.history(visitor_id, session_id)
    return Envelope(data={
        "sessionId": session_id,
        "messages": [
            {
                "role": item.role,
                "content": item.content,
                "createdAt": item.created_at.isoformat(),
            }
            for item in messages
        ],
    })


@router.post("/conversation/sessions/{session_id}/messages")
async def stream_message(
    session_id: str,
    body: MessageRequest,
    visitor_id: VisitorId,
    service: Service,
    resources: Resources,
) -> EventSourceResponse:
    workflow: tuple[dict, ...] = ()
    if body.workflow_id:
        record = await resources.get("flow", body.workflow_id)
        if record and record.status:
            workflow = tuple((record.payload or {}).get("nodes") or ())

    async def events():
        yield {"event": "start", "data": json.dumps({"sessionId": session_id})}
        async for item in service.stream_agent_reply(
            visitor_id,
            session_id,
            body.content,
            strategy=body.strategy,
            knowledge_base_id=body.knowledge_base_id,
            workflow=workflow,
            output_format=body.output_format,
        ):
            payload = {
                "messageType": item.event_type.value,
                "content": item.content,
                "data": item.data,
                "isFinal": item.is_final,
            }
            event_name = "delta" if item.event_type in {AgentEventType.AGENT_STREAM, AgentEventType.RESULT} else "agent_event"
            yield {"event": event_name, "data": json.dumps(payload, ensure_ascii=False)}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(events())


@router.get("/conversation/sessions/{session_id}/memory", response_model=Envelope)
async def list_memory(
    session_id: str,
    visitor_id: VisitorId,
    service: Service,
    resources: Resources,
) -> Envelope:
    await service.history(visitor_id, session_id)
    notes = await resources.list_memory_notes(visitor_id, session_id)
    return Envelope(data=[
        {
            "noteType": item.note_type,
            "content": item.content,
            "requestId": item.request_id,
            "createdAt": item.created_at.isoformat(),
        }
        for item in notes
    ])


@router.post("/conversation/sessions/{session_id}/memory", response_model=Envelope)
async def add_memory(
    session_id: str,
    body: MemoryNoteRequest,
    visitor_id: VisitorId,
    service: Service,
    resources: Resources,
) -> Envelope:
    await service.history(visitor_id, session_id)
    note = await resources.add_memory_note(
        visitor_id,
        session_id,
        body.note_type,
        body.content,
        body.request_id,
    )
    return Envelope(data={"id": note.id, "createdAt": note.created_at.isoformat()})


def _conversation_view(item: Any) -> dict[str, Any]:
    return {
        "sessionId": item.session_id,
        "title": item.title,
        "status": item.status,
        "latestQueryText": item.latest_query_text,
        "runCount": item.run_count,
        "startedAt": item.started_at.isoformat(),
        "lastActiveAt": item.last_active_at.isoformat(),
    }
