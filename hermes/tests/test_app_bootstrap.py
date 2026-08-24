from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from hermes_officer.app import create_app
from hermes_officer.core.config import AppSettings


class AppBootstrapTest(unittest.TestCase):
    def _client(self) -> TestClient:
        temp_dir = tempfile.TemporaryDirectory(prefix="hermes-app-test-")
        self.addCleanup(temp_dir.cleanup)
        settings = AppSettings(
            environment="test",
            log_path=Path(temp_dir.name) / "server.log",
            log_file_enabled=False,
            docs_enabled=False,
            mcp_enabled=False,
            show_banner=False,
        )
        return TestClient(create_app(settings, include_api=False, include_mcp=False))

    def test_health_should_expose_service_metadata_and_request_id(self):
        with self._client() as client:
            response = client.get("/health/live", headers={"X-Request-ID": "trace-123"})

        self.assertEqual(200, response.status_code)
        self.assertEqual("ok", response.json()["status"])
        self.assertEqual("trace-123", response.headers["x-request-id"])
        self.assertIn("x-process-time", response.headers)

    def test_wildcard_cors_should_disable_credentials(self):
        settings = AppSettings(cors_origins=("*",), cors_allow_credentials=True)
        self.assertFalse(settings.effective_cors_allow_credentials)

    def test_openapi_should_build_when_mcp_routes_are_enabled(self):
        settings = AppSettings(
            environment="test",
            docs_enabled=True,
            mcp_enabled=True,
            log_file_enabled=False,
            show_banner=False,
        )
        app = create_app(settings, include_api=False, include_mcp=True)

        schema = app.openapi()

        self.assertEqual("3.1.0", schema["openapi"])
        self.assertNotIn("/mcp", schema["paths"])


if __name__ == "__main__":
    unittest.main()
