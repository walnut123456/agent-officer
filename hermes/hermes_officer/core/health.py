from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


def _status(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": settings.name,
        "version": settings.version,
        "environment": settings.environment,
    }


@router.get("/health/live")
async def liveness(request: Request) -> dict[str, str]:
    return _status(request)


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, str]:
    database = getattr(request.app.state, "database", None)
    if database is not None:
        await database.healthcheck()
    knowledge = getattr(request.app.state, "knowledge", None)
    if (
        knowledge is not None
        and getattr(knowledge, "hybrid_required", False)
        and getattr(knowledge, "hybrid_retriever", None) is not None
    ):
        await knowledge.hybrid_retriever.healthcheck()
    return _status(request)


@router.get("/web/health", include_in_schema=False)
async def legacy_health(request: Request) -> dict[str, str]:
    """Compatibility endpoint retained while old deployments are replaced."""
    return _status(request)
