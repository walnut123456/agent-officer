from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text

from hermes_officer.infrastructure.database import Database


class DatabasePersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_enables_foreign_keys_and_wal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "persistence.db"
            database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
            try:
                await database.initialize()
                async with database.session() as session:
                    foreign_keys = (await session.execute(text("PRAGMA foreign_keys"))).scalar_one()
                    journal_mode = (await session.execute(text("PRAGMA journal_mode"))).scalar_one()
                self.assertEqual(foreign_keys, 1)
                self.assertEqual(str(journal_mode).lower(), "wal")
            finally:
                await database.dispose()


if __name__ == "__main__":
    unittest.main()
