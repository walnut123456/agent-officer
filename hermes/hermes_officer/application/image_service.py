from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from hermes_officer.infrastructure.database import Database, ImageReferenceRecord, ToolRunRecord
from hermes_officer.model.protocal import ImageGenerationRequest


MAX_REFERENCE_BYTES = 20 * 1024 * 1024
REFERENCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True, slots=True)
class ImageReferenceView:
    reference_id: str
    filename: str
    content_type: str
    file_size: int
    created_at: datetime

    @property
    def preview_url(self) -> str:
        return f"/api/images/references/{self.reference_id}/preview"


@dataclass(frozen=True, slots=True)
class ImageRunView:
    request_id: str
    prompt: str
    mode: str
    status: str
    output: dict[str, Any]
    error_message: str
    started_at: datetime
    finished_at: datetime | None


class ImageWorkspaceService:
    def __init__(self, database: Database, storage_path: Path) -> None:
        self.database = database
        self.storage_path = storage_path.resolve()
        self.reference_path = self.storage_path / "references"
        self.reference_path.mkdir(parents=True, exist_ok=True)

    async def save_reference(
        self,
        visitor_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> ImageReferenceView:
        safe_name = Path(filename).name.strip()
        extension = Path(safe_name).suffix.lower()
        if extension not in REFERENCE_EXTENSIONS:
            raise ValueError("参考图仅支持 JPG、PNG 和 WEBP")
        if not content or len(content) > MAX_REFERENCE_BYTES:
            raise ValueError("参考图不能为空且不能超过 20MB")
        reference_id = uuid4().hex
        target_dir = (self.reference_path / reference_id).resolve()
        if self.reference_path not in target_dir.parents:
            raise ValueError("非法的图片路径")
        target_dir.mkdir(parents=True, exist_ok=False)
        path = target_dir / safe_name
        path.write_bytes(content)
        record = ImageReferenceRecord(
            reference_id=reference_id,
            visitor_id=visitor_id,
            filename=safe_name,
            stored_path=str(path),
            content_type=content_type or "application/octet-stream",
            file_size=len(content),
        )
        async with self.database.session() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return self._reference_view(record)

    async def get_reference(self, visitor_id: str, reference_id: str) -> ImageReferenceRecord | None:
        async with self.database.session() as session:
            return await session.scalar(
                select(ImageReferenceRecord).where(
                    ImageReferenceRecord.reference_id == reference_id,
                    ImageReferenceRecord.visitor_id == visitor_id,
                )
            )

    async def list_references(self, visitor_id: str) -> list[ImageReferenceView]:
        async with self.database.session() as session:
            records = await session.scalars(
                select(ImageReferenceRecord)
                .where(ImageReferenceRecord.visitor_id == visitor_id)
                .order_by(ImageReferenceRecord.created_at.desc())
                .limit(30)
            )
            return [self._reference_view(item) for item in records]

    async def generate(
        self,
        visitor_id: str,
        prompt: str,
        *,
        mode: str | None = None,
        reference_ids: list[str] | None = None,
        mask_reference_ids: list[str] | None = None,
        size: str | None = None,
        count: int = 1,
    ) -> ImageRunView:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("图片提示词不能为空")
        references = await self._resolve_references(visitor_id, reference_ids or [])
        masks = await self._resolve_references(visitor_id, mask_reference_ids or [])
        resolved_mode = mode or ("edits" if references else "images")
        request_id = f"image-{uuid4().hex}"
        input_payload = {
            "visitor_id": visitor_id,
            "prompt": normalized_prompt,
            "mode": resolved_mode,
            "reference_ids": reference_ids or [],
            "mask_reference_ids": mask_reference_ids or [],
            "size": size,
            "count": count,
        }
        run = ToolRunRecord(
            request_id=request_id,
            tool_name="image_generation",
            input_payload=input_payload,
            status="RUNNING",
        )
        async with self.database.session() as session:
            session.add(run)
            await session.commit()
        try:
            from hermes_officer.tool.image_generation import generate_images

            result = await generate_images(ImageGenerationRequest(
                requestId=request_id,
                prompt=normalized_prompt,
                mode=resolved_mode,
                fileNames=[item.stored_path for item in references],
                maskFileNames=[item.stored_path for item in masks],
                size=size,
                n=count,
                stream=False,
            ))
            await self._finish_run(request_id, status="SUCCESS", output=result)
        except Exception as exc:
            await self._finish_run(request_id, status="FAILED", error_message=str(exc))
        completed = await self.get_run(request_id)
        assert completed is not None
        return completed

    async def get_run(self, request_id: str) -> ImageRunView | None:
        async with self.database.session() as session:
            record = await session.scalar(
                select(ToolRunRecord).where(
                    ToolRunRecord.request_id == request_id,
                    ToolRunRecord.tool_name == "image_generation",
                )
            )
            return self._run_view(record) if record else None

    async def list_history(self, visitor_id: str, limit: int = 50) -> list[ImageRunView]:
        async with self.database.session() as session:
            records = list(await session.scalars(
                select(ToolRunRecord)
                .where(ToolRunRecord.tool_name == "image_generation")
                .order_by(ToolRunRecord.started_at.desc())
                .limit(max(1, min(limit * 3, 200)))
            ))
        return [
            self._run_view(item) for item in records
            if (item.input_payload or {}).get("visitor_id") == visitor_id
        ][:limit]

    async def _resolve_references(self, visitor_id: str, reference_ids: list[str]) -> list[ImageReferenceRecord]:
        records: list[ImageReferenceRecord] = []
        for reference_id in reference_ids:
            record = await self.get_reference(visitor_id, reference_id)
            if record is None:
                raise LookupError(f"参考图不存在：{reference_id}")
            path = Path(record.stored_path).resolve()
            if self.reference_path not in path.parents or not path.is_file():
                raise LookupError(f"参考图文件不存在：{reference_id}")
            records.append(record)
        return records

    async def _finish_run(
        self,
        request_id: str,
        *,
        status: str,
        output: dict[str, Any] | None = None,
        error_message: str = "",
    ) -> None:
        async with self.database.session() as session:
            record = await session.scalar(select(ToolRunRecord).where(ToolRunRecord.request_id == request_id))
            if record:
                record.status = status
                record.output_payload = output or {}
                record.error_message = error_message or None
                record.finished_at = datetime.now(timezone.utc)
                await session.commit()

    @staticmethod
    def _reference_view(record: ImageReferenceRecord) -> ImageReferenceView:
        return ImageReferenceView(
            reference_id=record.reference_id,
            filename=record.filename,
            content_type=record.content_type,
            file_size=record.file_size,
            created_at=record.created_at,
        )

    @staticmethod
    def _run_view(record: ToolRunRecord) -> ImageRunView:
        payload = record.input_payload or {}
        return ImageRunView(
            request_id=record.request_id,
            prompt=str(payload.get("prompt", "")),
            mode=str(payload.get("mode", "images")),
            status=record.status,
            output=record.output_payload or {},
            error_message=record.error_message or "",
            started_at=record.started_at,
            finished_at=record.finished_at,
        )
