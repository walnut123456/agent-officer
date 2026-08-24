# -*- coding: utf-8 -*-
import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from hermes_officer.api.file_manage import router
from hermes_officer.db.file_table_op import FileDB, FileInfoOp, configure_file_database
from hermes_officer.infrastructure.database import Database


class FileManageApiTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router, prefix="/v1/file_tool")
        self.client = TestClient(app)

    def test_should_preview_file_when_url_contains_nested_path_segments(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as temp_file:
            temp_file.write("# 测试文件\n")
            file_path = temp_file.name

        try:
            file_info = SimpleNamespace(file_path=file_path)
            with patch(
                "hermes_officer.api.file_manage.FileInfoOp.get_by_file_id",
                new=AsyncMock(side_effect=[file_info]),
            ) as get_by_file_id:
                response = self.client.get(
                    "/v1/file_tool/preview/session-001/colbymchenry/demo.md"
                )

            self.assertEqual(200, response.status_code)
            self.assertEqual("# 测试文件", response.text.strip())
            get_by_file_id.assert_awaited_once()
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    def test_should_fallback_to_legacy_file_id_for_nested_path_segments(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as temp_file:
            temp_file.write("# 历史文件\n")
            file_path = temp_file.name

        try:
            file_info = SimpleNamespace(file_path=file_path)
            with patch(
                "hermes_officer.api.file_manage.FileInfoOp.get_by_file_id",
                new=AsyncMock(side_effect=[None, file_info]),
            ) as get_by_file_id:
                response = self.client.get(
                    "/v1/file_tool/preview/session-002/colbymchenry/legacy.md"
                )

            self.assertEqual(200, response.status_code)
            self.assertEqual("# 历史文件", response.text.strip())
            self.assertEqual(2, get_by_file_id.await_count)
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    def test_should_store_binary_upload_under_session_directory(self):
        with tempfile.TemporaryDirectory(prefix="file-manage-local-") as temp_dir:
            original_work_dir = FileDB._work_dir
            FileDB._work_dir = temp_dir
            upload_file = UploadFile(filename="poster.png", file=io.BytesIO(b"fake-image-bytes"))

            try:
                with patch.object(
                    FileInfoOp,
                    "add",
                    new=AsyncMock(side_effect=lambda file_info: file_info),
                ):
                    file_info = asyncio.run(
                        FileInfoOp.add_by_file(
                            file=upload_file,
                            file_id="file-id-001",
                            request_id="session-1779798194080-9667",
                        )
                    )

                saved_path = Path(file_info.file_path)
                self.assertTrue(saved_path.exists())
                self.assertEqual("session-1779798194080-9667", saved_path.parent.name)
                self.assertEqual("poster.png", saved_path.name)
            finally:
                FileDB._work_dir = original_work_dir


class FileInfoPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_store_file_metadata_in_primary_database(self):
        with tempfile.TemporaryDirectory(prefix="file-metadata-primary-") as temp_dir:
            original_work_dir = FileDB._work_dir
            FileDB._work_dir = str(Path(temp_dir) / "files")
            database = Database(
                f"sqlite+aiosqlite:///{(Path(temp_dir) / 'primary.db').as_posix()}"
            )
            configure_file_database(database)
            try:
                await database.initialize()
                created = await FileInfoOp.add_by_content(
                    filename="result.txt",
                    content="persisted metadata",
                    file_id="file-primary-001",
                    request_id="request-primary-001",
                )
                loaded = await FileInfoOp.get_by_file_id("file-primary-001")

                self.assertEqual(created.id, loaded.id)
                self.assertEqual("result.txt", loaded.filename)
                self.assertEqual("request-primary-001", loaded.request_id)
                self.assertTrue(Path(loaded.file_path).is_file())
            finally:
                await database.dispose()
                FileDB._work_dir = original_work_dir
