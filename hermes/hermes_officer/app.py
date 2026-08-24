from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request

from hermes_officer.core.config import AppSettings
from hermes_officer.core.errors import register_exception_handlers
from hermes_officer.core.health import router as health_router
from hermes_officer.core.logging import configure_logging
from hermes_officer.core.middleware import RequestContextMiddleware
from hermes_officer.application.conversation_service import (
    ConversationService,
    DevelopmentResponder,
    LiteLLMResponder,
)
from hermes_officer.infrastructure.database import Database
from hermes_officer.application.resource_service import ResourceService
from hermes_officer.application.knowledge_service import KnowledgeService
from hermes_officer.application.hybrid_retrieval import build_hybrid_retriever
from hermes_officer.application.image_service import ImageWorkspaceService
from hermes_officer.application.agent_runtime import (
    AgentRuntime,
    DevelopmentAgentModel,
    LiteLLMAgentModel,
    ToolRegistry,
)
from hermes_officer.application.tool_catalog import build_tool_registry
from hermes_officer.application.data_service import DataWorkspaceService
from hermes_officer.application.scheduler_service import AgentScheduler
from hermes_officer.db.file_table_op import configure_file_database


def _register_mcp(app: FastAPI) -> None:
    from hermes_officer.mcp.server import session_manager, sse_transport, server as mcp_server

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def mcp_endpoint(request: Request):
        await session_manager.handle_request(request.scope, request.receive, request._send)

    @app.get("/mcp/sse", include_in_schema=False)
    async def mcp_sse(request: Request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send,
        ) as streams:
            await mcp_server.run(
                streams[0], streams[1], mcp_server.create_initialization_options(),
            )

    @app.post("/mcp/messages/", include_in_schema=False)
    async def mcp_sse_messages(request: Request):
        return await sse_transport.handle_post_message(
            request.scope, request.receive, request._send,
        )


def create_app(
    settings: AppSettings | None = None,
    *,
    include_api: bool = True,
    include_mcp: bool | None = None,
) -> FastAPI:
    load_dotenv()
    settings = settings or AppSettings.from_env()
    settings.validate_for_startup()
    include_mcp = settings.mcp_enabled if include_mcp is None else include_mcp
    configure_logging(settings)
    database = Database(settings.database_url)
    configure_file_database(database)
    resources = ResourceService(database)
    hybrid_retriever = build_hybrid_retriever(settings)
    knowledge = KnowledgeService(
        database,
        settings.knowledge_storage_path,
        chat_model=settings.chat_model,
        hybrid_retriever=hybrid_retriever,
        hybrid_required=settings.knowledge_hybrid_required,
    )
    images = ImageWorkspaceService(database, settings.image_storage_path)
    data_workspace = DataWorkspaceService(
        database,
        settings.dataset_storage_path,
        chat_model=settings.chat_model,
    )
    agent_model = LiteLLMAgentModel(settings.chat_model) if settings.chat_model else DevelopmentAgentModel()
    tool_registry = build_tool_registry(ToolRegistry(database), knowledge, images, data_workspace)
    agent_runtime = AgentRuntime(agent_model, tool_registry)
    responder = LiteLLMResponder(settings.chat_model) if settings.chat_model else DevelopmentResponder()
    conversations = ConversationService(database, responder, agent_runtime=agent_runtime)
    scheduler = AgentScheduler(resources, conversations)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        await resources.seed_defaults()
        if settings.scheduler_enabled:
            await scheduler.start()
        try:
            if include_mcp:
                from hermes_officer.mcp.server import session_manager

                async with session_manager.run():
                    yield
            else:
                yield
        finally:
            await scheduler.stop()
            await knowledge.close()
            await database.dispose()

    app = FastAPI(
        title=settings.name,
        version=settings.version,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.conversations = conversations
    app.state.resources = resources
    app.state.knowledge = knowledge
    app.state.images = images
    app.state.data_workspace = data_workspace
    app.state.agent_runtime = agent_runtime
    app.state.scheduler = scheduler

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=settings.effective_cors_allow_credentials,
    )
    register_exception_handlers(app)
    app.include_router(health_router)

    if include_api:
        from hermes_officer.api import api_router
        from hermes_officer.api.admin import router as admin_router
        from hermes_officer.api.platform import router as platform_router
        from hermes_officer.api.knowledge import router as knowledge_router
        from hermes_officer.api.images import router as images_router
        from hermes_officer.api.data import router as data_router

        app.include_router(api_router)
        app.include_router(platform_router, prefix="/api")
        app.include_router(knowledge_router, prefix="/api")
        app.include_router(images_router, prefix="/api")
        app.include_router(data_router, prefix="/api")
        app.include_router(admin_router, prefix="/api")
    if include_mcp:
        _register_mcp(app)

    return app
