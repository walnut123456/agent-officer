from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger

from hermes_officer.model.context import RequestIdCtx


def _payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "success": False,
        "code": code,
        "message": message,
        "requestId": RequestIdCtx.request_id,
    }
    if details is not None:
        body["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_payload("invalid_argument", str(exc)),
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(_: Request, exc: PermissionError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content=_payload("forbidden", str(exc)),
        )

    @app.exception_handler(LookupError)
    async def lookup_error_handler(_: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_payload("not_found", str(exc)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload("validation_error", "请求参数不合法", exc.errors()),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
        details = None if isinstance(exc.detail, str) else exc.detail
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload("http_error", message, details),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _: Exception) -> JSONResponse:
        logger.exception(
            "unhandled request error request_id={} method={} path={}",
            RequestIdCtx.request_id,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content=_payload("internal_error", "服务暂时不可用，请稍后重试"),
        )
