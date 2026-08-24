from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from hermes_officer.app import create_app
from hermes_officer.application.knowledge_service import SafeWebFetcher
from hermes_officer.core.config import AppSettings


class KnowledgeWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-knowledge-")
        root = Path(self.temp.name)
        settings = AppSettings(
            database_url=f"sqlite+aiosqlite:///{(root / 'app.db').as_posix()}",
            knowledge_storage_path=root / "knowledge",
            image_storage_path=root / "images",
            dataset_storage_path=root / "datasets",
            log_file_enabled=False,
            mcp_enabled=False,
            ui_enabled=False,
            show_banner=False,
        )
        self.context = TestClient(create_app(settings, include_mcp=False))
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_should_manage_sources_and_answer_without_external_vector_database(self) -> None:
        created = self.client.post("/api/knowledge", json={"name": "产品资料"})
        self.assertEqual(201, created.status_code)
        kb_id = created.json()["kb_id"]

        uploaded = self.client.post(
            f"/api/knowledge/{kb_id}/documents",
            files={"file": ("guide.txt", "Hermes 支持 Python 知识库检索与来源引用。", "text/plain")},
        )
        self.assertEqual(201, uploaded.status_code)
        self.assertEqual("READY", uploaded.json()["status"])
        document_id = uploaded.json()["document_id"]

        answer = self.client.post(
            f"/api/knowledge/{kb_id}/query",
            json={"question": "支持什么检索能力"},
        )
        self.assertEqual(200, answer.status_code)
        self.assertIn("guide.txt", answer.text)
        self.assertIn("[DONE]", answer.text)

        content = self.client.get(f"/api/knowledge/documents/{document_id}/content")
        self.assertEqual(200, content.status_code)
        self.assertIn("来源引用", content.json()["content"])
        preview = self.client.get(f"/api/knowledge/documents/{document_id}/preview")
        self.assertEqual(200, preview.status_code)

    def test_should_reject_private_network_web_sources(self) -> None:
        with self.assertRaisesRegex(ValueError, "本机或内网"):
            asyncio.run(SafeWebFetcher._validate_target("http://127.0.0.1/private"))
