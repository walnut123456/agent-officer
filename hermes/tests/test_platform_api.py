from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from hermes_officer.app import create_app
from hermes_officer.core.config import AppSettings


class PlatformApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hermes-platform-")
        database_path = Path(self.temp_dir.name) / "platform.db"
        settings = AppSettings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
            log_file_enabled=False,
            mcp_enabled=False,
            show_banner=False,
            admin_api_key="test-admin-key",
        )
        self.client_context = TestClient(create_app(settings, include_mcp=False))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_should_complete_visitor_conversation_and_streaming_slice(self) -> None:
        bootstrap = self.client.get("/api/agent/visitor/bootstrap")
        self.assertEqual(200, bootstrap.status_code)
        visitor_id = bootstrap.json()["data"]["visitorId"]
        self.assertTrue(visitor_id)

        naming = self.client.post(
            "/api/agent/visitor/naming",
            json={"username": "小明"},
        )
        self.assertEqual("小明", naming.json()["data"]["username"])

        session_id = "session-platform-001"
        created = self.client.post(
            "/api/agent/conversation/sessions",
            json={"sessionId": session_id, "title": "架构讨论"},
        )
        self.assertEqual(session_id, created.json()["data"]["sessionId"])

        streamed = self.client.post(
            f"/api/agent/conversation/sessions/{session_id}/messages",
            json={"content": "你好"},
        )
        self.assertEqual(200, streamed.status_code)
        self.assertIn("event: delta", streamed.text)
        self.assertIn("Hermes", streamed.text)

        detail = self.client.get(f"/api/agent/conversation/sessions/{session_id}")
        messages = detail.json()["data"]["messages"]
        self.assertEqual(["user", "assistant"], [item["role"] for item in messages])

        sessions = self.client.get("/api/agent/conversation/sessions").json()["data"]
        self.assertEqual(1, len(sessions))
        self.assertEqual("SUCCESS", sessions[0]["status"])

        memory = self.client.post(
            f"/api/agent/conversation/sessions/{session_id}/memory",
            json={"noteType": "run_summary", "content": "用户在讨论架构"},
        )
        self.assertEqual(200, memory.status_code)
        notes = self.client.get(
            f"/api/agent/conversation/sessions/{session_id}/memory"
        ).json()["data"]
        self.assertEqual("用户在讨论架构", notes[0]["content"])

    def test_should_protect_and_version_admin_resources(self) -> None:
        unauthorized = self.client.get("/api/admin/resources/agent")
        self.assertEqual(401, unauthorized.status_code)

        headers = {"X-Admin-Key": "test-admin-key"}
        roles = self.client.get("/api/agent/role-library/list").json()["data"]
        self.assertGreaterEqual(len(roles), 2)

        created = self.client.put(
            "/api/admin/resources/prompt/reviewer",
            headers=headers,
            json={
                "name": "代码审查提示词",
                "payload": {"content": "审查安全性与可维护性"},
            },
        )
        self.assertEqual(1, created.json()["data"]["version"])

        updated = self.client.put(
            "/api/admin/resources/prompt/reviewer",
            headers=headers,
            json={
                "name": "代码审查提示词",
                "payload": {"content": "优先审查安全边界"},
            },
        )
        self.assertEqual(2, updated.json()["data"]["version"])

        disabled = self.client.delete(
            "/api/admin/resources/prompt/reviewer",
            headers=headers,
        )
        self.assertTrue(disabled.json()["data"]["disabled"])


if __name__ == "__main__":
    unittest.main()
