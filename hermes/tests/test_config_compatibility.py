from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from hermes_officer.core.config import AppSettings


class ConfigCompatibilityTest(unittest.TestCase):
    def test_explicit_chat_model_takes_precedence(self) -> None:
        with patch.dict(
            os.environ,
            {"CHAT_MODEL": "anthropic/claude-sonnet", "DEFAULT_MODEL": "legacy-model"},
            clear=True,
        ):
            self.assertEqual(AppSettings.from_env().chat_model, "anthropic/claude-sonnet")

    def test_legacy_openai_compatible_model_is_reused(self) -> None:
        with patch.dict(
            os.environ,
            {"DEFAULT_MODEL": "glm-5.1", "OPENAI_BASE_URL": "https://example.invalid/v1"},
            clear=True,
        ):
            self.assertEqual(AppSettings.from_env().chat_model, "openai/glm-5.1")

    def test_legacy_variable_reference_is_resolved(self) -> None:
        with patch.dict(
            os.environ,
            {"DEFAULT_MODEL": "glm-5.1", "LLM_MODEL_NAME": "${DEFAULT_MODEL}"},
            clear=True,
        ):
            self.assertEqual(AppSettings.from_env().chat_model, "glm-5.1")

    def test_relative_persistence_paths_are_resolved_from_project_root(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite+aiosqlite:///data/hermes.db",
                "KNOWLEDGE_STORAGE_PATH": "data/knowledge",
                "IMAGE_STORAGE_PATH": "data/images",
                "DATASET_STORAGE_PATH": "data/datasets",
            },
            clear=True,
        ):
            settings = AppSettings.from_env()

        database_path = Path(settings.database_url.split("///", 1)[1])
        self.assertTrue(database_path.is_absolute())
        self.assertTrue(settings.knowledge_storage_path.is_absolute())
        self.assertTrue(settings.image_storage_path.is_absolute())
        self.assertTrue(settings.dataset_storage_path.is_absolute())

    def test_mysql_components_build_async_database_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MYSQL_HOST": "db.internal",
                "MYSQL_PORT": "3307",
                "MYSQL_USER": "hermes user",
                "MYSQL_PASSWORD": "p@ss:/word",
                "MYSQL_DATABASE": "hermes_db",
            },
            clear=True,
        ):
            settings = AppSettings.from_env()

        self.assertEqual(
            settings.database_url,
            "mysql+aiomysql://hermes+user:p%40ss%3A%2Fword@db.internal:3307/hermes_db?charset=utf8mb4",
        )


if __name__ == "__main__":
    unittest.main()
