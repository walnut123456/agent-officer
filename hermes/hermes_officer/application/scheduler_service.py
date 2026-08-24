from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from loguru import logger

from hermes_officer.application.conversation_service import ConversationService
from hermes_officer.application.resource_service import ResourceService
from hermes_officer.domain.agent import AgentStrategy


class AgentScheduler:
    """Database-configured interval scheduler with graceful lifecycle handling."""

    def __init__(
        self,
        resources: ResourceService,
        conversations: ConversationService,
        *,
        poll_seconds: int = 15,
    ) -> None:
        self.resources = resources
        self.conversations = conversations
        self.poll_seconds = max(5, poll_seconds)
        self._runner: asyncio.Task | None = None
        self._next_runs: dict[str, datetime] = {}
        self._active_runs: set[asyncio.Task] = set()

    async def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._loop(), name="hermes-agent-scheduler")

    async def stop(self) -> None:
        if self._runner:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        for task in tuple(self._active_runs):
            task.cancel()
        if self._active_runs:
            await asyncio.gather(*self._active_runs, return_exceptions=True)
        self._active_runs.clear()

    async def tick(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(timezone.utc)
        schedules = await self.resources.list("schedule", enabled_only=True)
        active_ids = {item.resource_id for item in schedules}
        self._next_runs = {key: value for key, value in self._next_runs.items() if key in active_ids}
        started: list[str] = []
        for schedule in schedules:
            payload = schedule.payload or {}
            interval = self._interval_seconds(payload)
            next_run = self._next_runs.setdefault(schedule.resource_id, current)
            if current < next_run:
                continue
            self._next_runs[schedule.resource_id] = current + timedelta(seconds=interval)
            task = asyncio.create_task(
                self._execute(schedule.resource_id, payload),
                name=f"schedule-{schedule.resource_id}",
            )
            self._active_runs.add(task)
            task.add_done_callback(self._active_runs.discard)
            started.append(schedule.resource_id)
        return started

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("scheduler tick failed")
            await asyncio.sleep(self.poll_seconds)

    async def _execute(self, schedule_id: str, payload: dict[str, Any]) -> None:
        query = str(payload.get("query") or "").strip()
        if not query:
            logger.warning("schedule {} skipped: query is empty", schedule_id)
            return
        visitor_id = str(payload.get("visitor_id") or f"schedule-{schedule_id}")[:64]
        session_id = str(payload.get("session_id") or f"schedule-{schedule_id}-{uuid4().hex[:12]}")[:64]
        try:
            strategy = AgentStrategy(str(payload.get("strategy") or AgentStrategy.AUTO.value))
            await self.conversations.bootstrap_visitor(visitor_id, str(payload.get("username") or "定时任务"))
            await self.conversations.create_conversation(visitor_id, session_id, str(payload.get("title") or schedule_id))
            async for _ in self.conversations.stream_agent_reply(
                visitor_id,
                session_id,
                query,
                strategy=strategy,
                knowledge_base_id=str(payload.get("knowledge_base_id") or ""),
                workflow=tuple(payload.get("workflow") or ()),
                output_format=str(payload.get("output_format") or "chat"),
            ):
                pass
            logger.info("schedule {} completed, session={}", schedule_id, session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("schedule {} failed", schedule_id)

    @staticmethod
    def _interval_seconds(payload: dict[str, Any]) -> int:
        try:
            interval = int(payload.get("interval_seconds") or 3600)
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule.interval_seconds 必须是整数") from exc
        if interval < 60 or interval > 31 * 24 * 3600:
            raise ValueError("schedule.interval_seconds 必须在 60 秒到 31 天之间")
        return interval
