from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from hermes_officer.app import create_app
from hermes_officer.core.config import AppSettings


class DataWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-data-")
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
        self.headers = {"X-Visitor-ID": "data-user"}

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_should_upload_preview_and_analyze_csv_locally(self) -> None:
        response = self.client.post(
            "/api/data/datasets",
            headers=self.headers,
            files={"file": ("sales.csv", "region,sales\nNorth,12\nSouth,20\n", "text/csv")},
        )
        self.assertEqual(201, response.status_code)
        dataset = response.json()
        self.assertEqual(2, dataset["row_count"])
        dataset_id = dataset["dataset_id"]

        result = self.client.post(
            f"/api/data/datasets/{dataset_id}/query",
            json={"visitor_id": "data-user", "question": "请做数据分析"},
        )
        self.assertEqual(200, result.status_code)
        payload = result.json()
        self.assertEqual("LOCAL DESCRIBE", payload["sql"])
        self.assertTrue(payload["rows"])
        self.assertIn("summary", payload)
