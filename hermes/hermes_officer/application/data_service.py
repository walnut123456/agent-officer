from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import sqlparse
from sqlalchemy import select

from hermes_officer.infrastructure.database import Database, DatasetRecord


MAX_DATASET_BYTES = 50 * 1024 * 1024
MAX_DATASET_ROWS = 200_000
BLOCKED_SQL = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace)\b", re.I)


@dataclass(frozen=True, slots=True)
class DatasetView:
    dataset_id: str
    name: str
    file_ext: str
    file_size: int
    row_count: int
    columns: list[dict[str, str]]
    preview: list[dict[str, Any]]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DataQueryResult:
    dataset_id: str
    question: str
    sql: str
    summary: str
    columns: list[str]
    rows: list[dict[str, Any]]
    chart: dict[str, Any]


class DataWorkspaceService:
    def __init__(self, database: Database, storage_path: Path, *, chat_model: str | None = None) -> None:
        self.database = database
        self.storage_path = storage_path.resolve()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.chat_model = chat_model

    async def upload(self, visitor_id: str, filename: str, content: bytes) -> DatasetView:
        safe_name = Path(filename).name.strip()
        extension = Path(safe_name).suffix.lower()
        if extension not in {".csv", ".xlsx"}:
            raise ValueError("数据集仅支持 CSV 和 XLSX")
        if not content or len(content) > MAX_DATASET_BYTES:
            raise ValueError("数据文件不能为空且不能超过 50MB")
        dataset_id = uuid4().hex
        target_dir = (self.storage_path / visitor_id / dataset_id).resolve()
        if self.storage_path not in target_dir.parents:
            raise ValueError("非法的数据集路径")
        target_dir.mkdir(parents=True, exist_ok=False)
        path = target_dir / safe_name
        path.write_bytes(content)
        try:
            frame = await self._read_frame(path)
            if len(frame) > MAX_DATASET_ROWS:
                raise ValueError(f"数据集最多支持 {MAX_DATASET_ROWS:,} 行")
            frame.columns = [str(item).strip() or f"column_{index + 1}" for index, item in enumerate(frame.columns)]
            columns = [{"name": str(name), "dtype": str(dtype)} for name, dtype in frame.dtypes.items()]
            preview = self._records(frame.head(20))
        except Exception:
            path.unlink(missing_ok=True)
            target_dir.rmdir()
            raise
        record = DatasetRecord(
            dataset_id=dataset_id,
            visitor_id=visitor_id,
            name=safe_name,
            stored_path=str(path),
            file_ext=extension,
            file_size=len(content),
            row_count=len(frame),
            columns=columns,
            preview=preview,
        )
        async with self.database.session() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return self._view(record)

    async def list_datasets(self, visitor_id: str) -> list[DatasetView]:
        async with self.database.session() as session:
            records = await session.scalars(
                select(DatasetRecord)
                .where(DatasetRecord.visitor_id == visitor_id)
                .order_by(DatasetRecord.created_at.desc())
            )
            return [self._view(item) for item in records]

    async def get_dataset(self, visitor_id: str, dataset_id: str) -> DatasetRecord | None:
        async with self.database.session() as session:
            return await session.scalar(
                select(DatasetRecord).where(
                    DatasetRecord.dataset_id == dataset_id,
                    DatasetRecord.visitor_id == visitor_id,
                )
            )

    async def query(self, visitor_id: str, dataset_id: str, question: str) -> DataQueryResult:
        record = await self.get_dataset(visitor_id, dataset_id)
        if record is None:
            raise LookupError("数据集不存在")
        normalized = question.strip()
        if not normalized:
            raise ValueError("分析问题不能为空")
        path = Path(record.stored_path).resolve()
        if self.storage_path not in path.parents or not path.is_file():
            raise LookupError("数据集文件不存在")
        frame = await self._read_frame(path)
        frame.columns = [str(item).strip() or f"column_{index + 1}" for index, item in enumerate(frame.columns)]
        if self.chat_model:
            sql, summary = await self._generate_sql(normalized, frame)
            result_frame = self._execute_sql(frame, sql)
        else:
            sql, summary, result_frame = self._local_analysis(normalized, frame)
        result_frame = result_frame.head(200)
        return DataQueryResult(
            dataset_id=dataset_id,
            question=normalized,
            sql=sql,
            summary=summary,
            columns=[str(item) for item in result_frame.columns],
            rows=self._records(result_frame),
            chart=self._chart_spec(result_frame),
        )

    async def _generate_sql(self, question: str, frame: pd.DataFrame) -> tuple[str, str]:
        from litellm import acompletion

        schema = ", ".join(f'"{name}" {dtype}' for name, dtype in frame.dtypes.items())
        sample = self._records(frame.head(5))
        response = await acompletion(
            model=self.chat_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 SQLite 数据分析器。表名固定为 dataset。只生成一条只读 SELECT。"
                        "返回 JSON：{\"sql\": string, \"summary\": string}。不得编造字段。"
                    ),
                },
                {"role": "user", "content": f"字段：{schema}\n样例：{json.dumps(sample, ensure_ascii=False)}\n问题：{question}"},
            ],
            stream=False,
        )
        raw = response.choices[0].message.content or ""
        match = re.search(r"\{[\s\S]*\}", raw)
        try:
            payload = json.loads(match.group(0) if match else raw)
            sql = str(payload.get("sql") or "")
            summary = str(payload.get("summary") or "已按问题完成查询")
        except (AttributeError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("模型未返回有效 SQL") from exc
        self._validate_sql(sql)
        return sql, summary

    @staticmethod
    def _execute_sql(frame: pd.DataFrame, sql: str) -> pd.DataFrame:
        DataWorkspaceService._validate_sql(sql)
        with sqlite3.connect(":memory:") as connection:
            frame.to_sql("dataset", connection, index=False)
            return pd.read_sql_query(sql, connection)

    @staticmethod
    def _local_analysis(question: str, frame: pd.DataFrame) -> tuple[str, str, pd.DataFrame]:
        lowered = question.lower()
        if any(token in lowered for token in ("字段", "列名", "columns", "schema")):
            result = pd.DataFrame([
                {"字段": name, "类型": str(dtype), "非空数": int(frame[name].notna().sum())}
                for name, dtype in frame.dtypes.items()
            ])
            return "LOCAL SCHEMA", f"数据集共有 {len(frame):,} 行、{len(frame.columns)} 个字段。", result
        numeric = frame.select_dtypes(include="number")
        if not numeric.empty:
            result = numeric.describe().transpose().reset_index(names="字段")
            return "LOCAL DESCRIBE", f"已对 {len(numeric.columns)} 个数值字段完成描述性统计。", result
        return "LOCAL PREVIEW", f"数据集共有 {len(frame):,} 行，当前没有可统计的数值字段。", frame.head(100)

    @staticmethod
    def _validate_sql(sql: str) -> None:
        statements = [item for item in sqlparse.parse(sql) if str(item).strip()]
        if len(statements) != 1 or statements[0].get_type() != "SELECT" or BLOCKED_SQL.search(sql):
            raise ValueError("仅允许单条只读 SELECT 查询")
        if ";" in sql.strip().rstrip(";"):
            raise ValueError("SQL 不能包含多条语句")

    @staticmethod
    async def _read_frame(path: Path) -> pd.DataFrame:
        from asyncio import to_thread

        if path.suffix.lower() == ".csv":
            def read_csv() -> pd.DataFrame:
                try:
                    return pd.read_csv(path, encoding="utf-8-sig")
                except UnicodeDecodeError:
                    return pd.read_csv(path, encoding="gb18030")
            return await to_thread(read_csv)
        return await to_thread(pd.read_excel, path)

    @staticmethod
    def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        return json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))

    @staticmethod
    def _chart_spec(frame: pd.DataFrame) -> dict[str, Any]:
        if frame.empty or len(frame.columns) < 2:
            return {}
        numeric_columns = list(frame.select_dtypes(include="number").columns)
        if not numeric_columns:
            return {}
        x_column = next((item for item in frame.columns if item not in numeric_columns), frame.columns[0])
        y_column = numeric_columns[0]
        limited = frame[[x_column, y_column]].head(30)
        return {
            "type": "bar",
            "x_field": str(x_column),
            "y_field": str(y_column),
            "x": [str(item) for item in limited[x_column].tolist()],
            "y": [None if pd.isna(item) else float(item) for item in limited[y_column].tolist()],
        }

    @staticmethod
    def _view(record: DatasetRecord) -> DatasetView:
        return DatasetView(
            dataset_id=record.dataset_id,
            name=record.name,
            file_ext=record.file_ext,
            file_size=record.file_size,
            row_count=record.row_count,
            columns=record.columns or [],
            preview=record.preview or [],
            created_at=record.created_at,
        )
