from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_officer.application.resource_service import ResourceService
from hermes_officer.application.scheduler_service import AgentScheduler
from hermes_officer.domain.agent import AgentEvent, AgentEventType
from hermes_officer.infrastructure.database import Database


class FakeConversations:
    def __init__(self) -> None:
        self.calls = []

    async def bootstrap_visitor(self, visitor_id, username=None):
        self.calls.append(("visitor", visitor_id, username))

    async def create_conversation(self, visitor_id, session_id, title="新对话"):
        self.calls.append(("conversation", visitor_id, session_id, title))

    async def stream_agent_reply(self, visitor_id, session_id, content, **kwargs):
        self.calls.append(("run", visitor_id, session_id, content, kwargs))
        yield AgentEvent(AgentEventType.RESULT, "done", is_final=True)


class SchedulerServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-scheduler-")
        path = Path(self.temp.name) / "scheduler.db"
        self.database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
        await self.database.initialize()
        self.resources = ResourceService(self.database)
        self.conversations = FakeConversations()
        self.scheduler = AgentScheduler(self.resources, self.conversations)

    async def asyncTearDown(self) -> None:
        await self.scheduler.stop()
        await self.database.dispose()
        self.temp.cleanup()

    async def test_tick_should_start_due_schedule_only_once_per_interval(self) -> None:
        await self.resources.upsert(
            "schedule",
            "daily-summary",
            name="日报",
            payload={"interval_seconds": 60, "query": "生成日报", "strategy": "react"},
        )
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(["daily-summary"], await self.scheduler.tick(now))
        await asyncio.gather(*tuple(self.scheduler._active_runs))
        self.assertEqual([], await self.scheduler.tick(now))
        self.assertTrue(any(item[0] == "run" and item[3] == "生成日报" for item in self.conversations.calls))
