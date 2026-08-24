from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from hermes_officer.infrastructure.database import Database, MemoryNoteRecord, ResourceRecord


RESOURCE_TYPES = frozenset({
    "agent",
    "client",
    "model",
    "provider",
    "advisor",
    "prompt",
    "knowledge_base",
    "mcp_server",
    "flow",
    "schedule",
    "data_model",
})


class ResourceService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def seed_defaults(self) -> None:
        defaults = (
            ("default-assistant", "默认助手", "fix"),
            ("reasoning-assistant", "推理助手", "react"),
        )
        for resource_id, name, strategy in defaults:
            if await self.get("agent", resource_id) is None:
                await self.upsert(
                    "agent",
                    resource_id,
                    name=name,
                    description="Hermes 内置智能体",
                    payload={"strategy": strategy, "channel": "web"},
                )

    async def upsert(
        self,
        resource_type: str,
        resource_id: str,
        *,
        name: str,
        description: str | None = None,
        payload: dict[str, Any] | None = None,
        status: int = 1,
    ) -> ResourceRecord:
        normalized_type = self._validate_type(resource_type)
        normalized_id = resource_id.strip()
        normalized_name = name.strip()
        if not normalized_id or len(normalized_id) > 64:
            raise ValueError("资源 ID 长度必须在 1 到 64 个字符之间")
        if not normalized_name or len(normalized_name) > 128:
            raise ValueError("资源名称长度必须在 1 到 128 个字符之间")
        async with self.database.session() as session:
            record = await session.scalar(
                select(ResourceRecord).where(
                    ResourceRecord.resource_type == normalized_type,
                    ResourceRecord.resource_id == normalized_id,
                )
            )
            if record is None:
                record = ResourceRecord(
                    resource_type=normalized_type,
                    resource_id=normalized_id,
                    name=normalized_name,
                )
                session.add(record)
            else:
                record.version += 1
            record.name = normalized_name
            record.description = description
            record.payload = payload or {}
            record.status = status
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(record)
            return record

    async def get(self, resource_type: str, resource_id: str) -> ResourceRecord | None:
        normalized_type = self._validate_type(resource_type)
        async with self.database.session() as session:
            return await session.scalar(
                select(ResourceRecord).where(
                    ResourceRecord.resource_type == normalized_type,
                    ResourceRecord.resource_id == resource_id,
                )
            )

    async def list(self, resource_type: str, *, enabled_only: bool = False) -> list[ResourceRecord]:
        normalized_type = self._validate_type(resource_type)
        statement = select(ResourceRecord).where(ResourceRecord.resource_type == normalized_type)
        if enabled_only:
            statement = statement.where(ResourceRecord.status == 1)
        async with self.database.session() as session:
            rows = await session.scalars(statement.order_by(ResourceRecord.updated_at.desc()))
            return list(rows)

    async def disable(self, resource_type: str, resource_id: str) -> bool:
        record = await self.get(resource_type, resource_id)
        if record is None:
            return False
        return bool((await self.upsert(
            resource_type,
            resource_id,
            name=record.name,
            description=record.description,
            payload=record.payload,
            status=0,
        )).status == 0)

    async def add_memory_note(
        self,
        visitor_id: str,
        session_id: str,
        note_type: str,
        content: str,
        request_id: str = "",
    ) -> MemoryNoteRecord:
        if note_type not in {"compaction_summary", "run_summary"}:
            raise ValueError("不支持的记忆类型")
        async with self.database.session() as session:
            record = MemoryNoteRecord(
                visitor_id=visitor_id,
                session_id=session_id,
                request_id=request_id,
                note_type=note_type,
                content=content,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_memory_notes(self, visitor_id: str, session_id: str) -> list[MemoryNoteRecord]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(MemoryNoteRecord)
                .where(
                    MemoryNoteRecord.visitor_id == visitor_id,
                    MemoryNoteRecord.session_id == session_id,
                )
                .order_by(MemoryNoteRecord.created_at.desc())
            )
            return list(rows)

    @staticmethod
    def _validate_type(resource_type: str) -> str:
        normalized = resource_type.strip().lower()
        if normalized not in RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型：{resource_type}")
        return normalized
