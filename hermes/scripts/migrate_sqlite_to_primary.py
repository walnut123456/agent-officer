from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from dotenv import load_dotenv
from sqlalchemy import inspect, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from hermes_officer.core.config import AppSettings, PROJECT_ROOT
from hermes_officer.infrastructure.database import Base


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Hermes SQLite records to the configured primary database")
    parser.add_argument(
        "--source",
        default=f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'data' / 'hermes.db').resolve().as_posix()}",
    )
    parser.add_argument("--reset-target", action="store_true", help="Drop every table in the target database first")
    parser.add_argument("--confirm-database", default="", help="Must exactly match the target database when resetting")
    return parser.parse_args()


async def reflected_table_names(connection: AsyncConnection) -> list[str]:
    return await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names())


async def reset_target(connection: AsyncConnection, expected_database: str) -> list[str]:
    target_database = make_url(str(connection.engine.url)).database or ""
    if not expected_database or expected_database != target_database:
        raise RuntimeError("--confirm-database 必须与目标数据库名完全一致")
    tables = await reflected_table_names(connection)
    await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    try:
        preparer = connection.dialect.identifier_preparer
        for table_name in tables:
            await connection.execute(text(f"DROP TABLE IF EXISTS {preparer.quote(table_name)}"))
    finally:
        await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    return tables


async def migrate(source_url: str, target_url: str, *, reset: bool, confirm_database: str) -> dict[str, int]:
    if make_url(source_url).render_as_string(hide_password=True) == make_url(target_url).render_as_string(hide_password=True):
        raise RuntimeError("源数据库与目标数据库不能相同")
    source_engine = create_async_engine(source_url, pool_pre_ping=True)
    target_engine = create_async_engine(target_url, pool_pre_ping=False, pool_recycle=1800)
    counts: dict[str, int] = {}
    try:
        async with target_engine.begin() as target_connection:
            existing = await reflected_table_names(target_connection)
            if reset:
                await reset_target(target_connection, confirm_database)
            elif existing:
                raise RuntimeError("目标数据库不是空库；如已完成备份，请显式使用 --reset-target")
            await target_connection.run_sync(Base.metadata.create_all)

        async with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            source_tables = set(await reflected_table_names(source_connection))
            for table in Base.metadata.sorted_tables:
                if table.name not in source_tables:
                    counts[table.name] = 0
                    continue
                rows = list((await source_connection.execute(select(table))).mappings())
                counts[table.name] = len(rows)
                if rows:
                    await target_connection.execute(insert(table), [dict(row) for row in rows])
        return counts
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


async def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    settings = AppSettings.from_env()
    target = make_url(settings.database_url)
    if target.get_backend_name() != "mysql":
        raise RuntimeError("当前 DATABASE_URL/MYSQL_* 未指向 MySQL")
    if not Path(make_url(args.source).database or "").exists():
        raise RuntimeError("SQLite 源数据库不存在")
    counts = await migrate(
        args.source,
        settings.database_url,
        reset=args.reset_target,
        confirm_database=args.confirm_database,
    )
    print("Migration completed:")
    for table_name, count in counts.items():
        print(f"  {table_name}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
