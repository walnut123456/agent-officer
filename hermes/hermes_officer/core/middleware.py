from __future__ import annotations

import time
import uuid

from loguru import logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hermes_officer.model.context import RequestIdCtx

REQUEST_ID_HEADER = "x-request-id"


class RequestContextMiddleware:
    """Add a correlation id and duration without buffering SSE responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        request_id = headers.get(REQUEST_ID_HEADER.encode(), b"").decode(errors="ignore").strip()
        request_id = request_id[:128] if request_id else uuid.uuid4().hex
        token = RequestIdCtx.set(request_id)
        started = time.perf_counter()

        async def send_with_context(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                response_headers.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-process-time", str(elapsed_ms).encode()),
                    ]
                )
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "request completed request_id={} method={} path={} duration_ms={}",
                request_id,
                scope.get("method"),
                scope.get("path"),
                elapsed_ms,
            )
            RequestIdCtx.reset(token)
