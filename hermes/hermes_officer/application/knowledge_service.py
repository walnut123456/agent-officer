from __future__ import annotations

import asyncio
import base64
import ipaddress
import math
import logging
import re
import shutil
import socket
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from sqlalchemy import delete, select

from hermes_officer.application.document_chunker import ChunkingConfig, HierarchicalDocumentChunker
from hermes_officer.application.hybrid_retrieval import QdrantHybridRetriever
from hermes_officer.infrastructure.database import (
    Database,
    KnowledgeBaseRecord,
    KnowledgeDocumentRecord,
)


logger = logging.getLogger(__name__)


MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_WEB_BYTES = 10 * 1024 * 1024
SUPPORTED_EXTENSIONS = {
    ".csv", ".docx", ".html", ".htm", ".jpeg", ".jpg", ".md", ".pdf",
    ".png", ".txt", ".webp", ".xlsx",
}
_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class KnowledgeBaseView:
    kb_id: str
    name: str
    description: str
    chunk_size: int
    chunk_overlap: int
    document_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentView:
    document_id: str
    kb_id: str
    title: str
    source_type: str
    source_url: str
    content_type: str
    file_ext: str
    file_size: int
    status: str
    error_message: str
    chunk_count: int
    created_at: datetime
    updated_at: datetime

    @property
    def preview_url(self) -> str:
        if self.source_type == "url":
            return self.source_url
        return f"/api/knowledge/documents/{self.document_id}/preview"

    @property
    def download_url(self) -> str:
        if self.source_type == "url":
            return self.source_url
        return f"/api/knowledge/documents/{self.document_id}/download"


@dataclass(frozen=True, slots=True)
class SearchHit:
    document_id: str
    title: str
    source_type: str
    source_url: str
    chunk_index: int
    content: str
    score: float
    section_path: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None


class SafeWebFetcher:
    """Small SSRF-aware fetcher used by knowledge-base URL ingestion."""

    def __init__(self, timeout_seconds: float = 30, max_redirects: int = 5) -> None:
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_redirects = max_redirects

    async def fetch(self, url: str) -> tuple[str, str]:
        current = url.strip()
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            for _ in range(self.max_redirects + 1):
                await self._validate_target(current)
                async with client.stream("GET", current, headers={"User-Agent": "Hermes-Officer/1.0"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("网页重定向缺少 Location")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "text/html").split(";", 1)[0]
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > MAX_WEB_BYTES:
                            raise ValueError("网页内容超过 10MB 限制")
                    encoding = response.encoding or "utf-8"
                    return bytes(payload).decode(encoding, errors="replace"), content_type
            raise ValueError("网页重定向次数过多")

    @staticmethod
    async def _validate_target(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("仅支持有效的 HTTP/HTTPS 网页地址")
        if parsed.username or parsed.password:
            raise ValueError("网页地址不能包含用户名或密码")

        def resolve() -> list[str]:
            return list({item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)})

        try:
            addresses = await asyncio.to_thread(resolve)
        except socket.gaierror as exc:
            raise ValueError("网页域名无法解析") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ValueError("出于安全原因，不能导入本机或内网地址")


class KnowledgeService:
    """Canonical knowledge service with BM25+dense hybrid retrieval."""

    def __init__(
        self,
        database: Database,
        storage_path: Path,
        *,
        chat_model: str | None = None,
        web_fetcher: SafeWebFetcher | None = None,
        hybrid_retriever: QdrantHybridRetriever | None = None,
        hybrid_required: bool = False,
    ) -> None:
        self.database = database
        self.storage_path = storage_path.resolve()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.chat_model = chat_model
        self.web_fetcher = web_fetcher or SafeWebFetcher()
        self.hybrid_retriever = hybrid_retriever
        self.hybrid_required = hybrid_required

    async def close(self) -> None:
        if self.hybrid_retriever is not None:
            await self.hybrid_retriever.close()

    async def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        *,
        chunk_size: int = 800,
        chunk_overlap: int = 80,
        kb_id: str | None = None,
    ) -> KnowledgeBaseView:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("知识库名称不能为空")
        if not 200 <= chunk_size <= 4_000:
            raise ValueError("chunk_size 必须在 200 到 4000 之间")
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        record = KnowledgeBaseRecord(
            kb_id=(kb_id or uuid4().hex).strip(),
            name=normalized_name[:128],
            description=description.strip()[:512],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        async with self.database.session() as session:
            if await session.scalar(select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.kb_id == record.kb_id)):
                raise ValueError("知识库 ID 已存在")
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return self._kb_view(record, 0)

    async def list_knowledge_bases(self) -> list[KnowledgeBaseView]:
        async with self.database.session() as session:
            records = list(await session.scalars(select(KnowledgeBaseRecord).order_by(KnowledgeBaseRecord.updated_at.desc())))
            documents = list(await session.scalars(select(KnowledgeDocumentRecord.kb_id)))
        counts = Counter(documents)
        return [self._kb_view(record, counts[record.kb_id]) for record in records]

    async def get_knowledge_base(self, kb_id: str) -> KnowledgeBaseView | None:
        async with self.database.session() as session:
            record = await session.scalar(select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.kb_id == kb_id))
            if record is None:
                return None
            documents = list(await session.scalars(select(KnowledgeDocumentRecord.document_id).where(KnowledgeDocumentRecord.kb_id == kb_id)))
        return self._kb_view(record, len(documents))

    async def delete_knowledge_base(self, kb_id: str) -> int:
        documents = await self.list_documents(kb_id)
        if self.hybrid_retriever is not None:
            await self._run_vector_operation(self.hybrid_retriever.delete_knowledge_base(kb_id))
        async with self.database.session() as session:
            record = await session.scalar(select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.kb_id == kb_id))
            if record is None:
                raise LookupError("知识库不存在")
            await session.delete(record)
            await session.commit()
        target = (self.storage_path / kb_id).resolve()
        if target != self.storage_path and self.storage_path in target.parents and target.exists():
            await asyncio.to_thread(shutil.rmtree, target)
        return len(documents)

    async def list_documents(self, kb_id: str) -> list[KnowledgeDocumentView]:
        await self._require_kb(kb_id)
        async with self.database.session() as session:
            rows = await session.scalars(
                select(KnowledgeDocumentRecord)
                .where(KnowledgeDocumentRecord.kb_id == kb_id)
                .order_by(KnowledgeDocumentRecord.updated_at.desc())
            )
            return [self._document_view(item) for item in rows]

    async def get_document(self, document_id: str) -> KnowledgeDocumentRecord | None:
        async with self.database.session() as session:
            return await session.scalar(
                select(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.document_id == document_id)
            )

    async def ingest_file(
        self,
        kb_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> KnowledgeDocumentView:
        kb = await self._require_kb(kb_id)
        safe_name = Path(filename).name.strip()
        extension = Path(safe_name).suffix.lower()
        if not safe_name or extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文件类型；支持：{', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        if not content:
            raise ValueError("文件不能为空")
        if len(content) > MAX_SOURCE_BYTES:
            raise ValueError("文件超过 50MB 限制")

        document_id = uuid4().hex
        document_dir = (self.storage_path / kb_id / document_id).resolve()
        self._ensure_storage_target(document_dir)
        document_dir.mkdir(parents=True, exist_ok=False)
        stored_path = document_dir / safe_name
        await asyncio.to_thread(stored_path.write_bytes, content)
        source_url = f"/api/knowledge/documents/{document_id}/preview"
        record = KnowledgeDocumentRecord(
            document_id=document_id,
            kb_id=kb_id,
            title=safe_name,
            source_type="file",
            source_url=source_url,
            stored_path=str(stored_path),
            content_type=content_type or "application/octet-stream",
            file_ext=extension,
            file_size=len(content),
            status="PROCESSING",
        )
        async with self.database.session() as session:
            session.add(record)
            await session.commit()

        try:
            canonical = await self._extract_source(stored_path, content_type)
            await self._mark_ready(record.document_id, canonical, kb.chunk_size, kb.chunk_overlap)
        except Exception as exc:
            await self._mark_failed(record.document_id, str(exc))
        final = await self.get_document(document_id)
        assert final is not None
        return self._document_view(final)

    async def ingest_url(self, kb_id: str, url: str) -> KnowledgeDocumentView:
        kb = await self._require_kb(kb_id)
        document_id = uuid4().hex
        parsed = urlparse(url.strip())
        title = parsed.hostname or "网页资料"
        record = KnowledgeDocumentRecord(
            document_id=document_id,
            kb_id=kb_id,
            title=title[:255],
            source_type="url",
            source_url=url.strip(),
            content_type="text/html",
            file_ext=".html",
            status="PROCESSING",
        )
        async with self.database.session() as session:
            session.add(record)
            await session.commit()
        try:
            html, content_type = await self.web_fetcher.fetch(url)
            canonical, extracted_title = await asyncio.to_thread(self._extract_html, html)
            await self._mark_ready(document_id, canonical, kb.chunk_size, kb.chunk_overlap, title=extracted_title or title)
            async with self.database.session() as session:
                current = await session.scalar(select(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.document_id == document_id))
                if current:
                    current.content_type = content_type
                    current.file_size = len(html.encode("utf-8"))
                    await session.commit()
        except Exception as exc:
            await self._mark_failed(document_id, str(exc))
        final = await self.get_document(document_id)
        assert final is not None
        return self._document_view(final)

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        if self.hybrid_retriever is not None:
            await self._run_vector_operation(self.hybrid_retriever.delete_document(document_id))
        async with self.database.session() as session:
            record = await session.scalar(
                select(KnowledgeDocumentRecord).where(
                    KnowledgeDocumentRecord.kb_id == kb_id,
                    KnowledgeDocumentRecord.document_id == document_id,
                )
            )
            if record is None:
                raise LookupError("资料不存在")
            stored_path = record.stored_path
            await session.delete(record)
            kb = await session.scalar(select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.kb_id == kb_id))
            if kb:
                kb.updated_at = _utc_now()
            await session.commit()
        if stored_path:
            target = Path(stored_path).resolve().parent
            if target != self.storage_path and self.storage_path in target.parents and target.exists():
                await asyncio.to_thread(shutil.rmtree, target)

    async def search(self, kb_id: str, query: str, *, limit: int = 6) -> list[SearchHit]:
        await self._require_kb(kb_id)
        normalized = query.strip()
        if not normalized:
            raise ValueError("检索问题不能为空")
        async with self.database.session() as session:
            documents = list(await session.scalars(
                select(KnowledgeDocumentRecord).where(
                    KnowledgeDocumentRecord.kb_id == kb_id,
                    KnowledgeDocumentRecord.status == "READY",
                )
            ))
        corpus: list[tuple[KnowledgeDocumentRecord, int, str, list[str], dict[str, Any]]] = []
        for document in documents:
            for index, chunk in enumerate(document.chunks or []):
                text = str(chunk.get("text", "") if isinstance(chunk, dict) else chunk)
                if text.strip():
                    metadata = chunk if isinstance(chunk, dict) else {}
                    corpus.append((document, index, text, self._tokenize(text), metadata))
        if not corpus:
            return []
        query_tokens = self._tokenize(normalized)
        if not query_tokens:
            return []
        doc_freq = Counter(
            token
            for token in set(query_tokens)
            for item in corpus
            if token in set(item[3])
        )
        average_length = sum(len(item[3]) for item in corpus) / len(corpus)
        lexical_scored: list[SearchHit] = []
        lexical_terms = {token for token in query_tokens if len(token) > 1}
        for document, index, text, tokens, metadata in corpus:
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                inverse_frequency = math.log(1 + (len(corpus) - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5))
                denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / max(average_length, 1))
                score += inverse_frequency * frequency * 2.5 / denominator
            matched_terms = {token for token in lexical_terms if frequencies[token]}
            coverage = len(matched_terms) / max(len(lexical_terms), 1)
            if score > 0 and coverage >= 0.20:
                lexical_scored.append(SearchHit(
                    document_id=document.document_id,
                    title=document.title,
                    source_type=document.source_type,
                    source_url=document.source_url,
                    chunk_index=index,
                    content=text,
                    score=score,
                    section_path=tuple(str(item) for item in metadata.get("section_path") or []),
                    page_start=metadata.get("page_start"),
                    page_end=metadata.get("page_end"),
                ))
        lexical_scored.sort(key=lambda item: item.score, reverse=True)
        if self.hybrid_retriever is None:
            return lexical_scored[:max(1, min(limit, 20))]

        try:
            dense_hits = await self.hybrid_retriever.search(
                kb_id,
                normalized,
                limit=max(limit * 4, 20),
            )
        except Exception:
            if self.hybrid_required:
                raise
            logger.exception("Dense retrieval failed; falling back to lexical search")
            return lexical_scored[:max(1, min(limit, 20))]

        corpus_by_key = {
            (document.document_id, index): (document, text, metadata)
            for document, index, text, _, metadata in corpus
        }
        fused: dict[tuple[str, int], float] = Counter()
        rrf_k = 60
        for rank, hit in enumerate(lexical_scored, 1):
            fused[(hit.document_id, hit.chunk_index)] += (
                self.hybrid_retriever.lexical_weight / (rrf_k + rank)
            )
        for rank, hit in enumerate(dense_hits, 1):
            fused[(hit.document_id, hit.chunk_index)] += (
                self.hybrid_retriever.dense_weight / (rrf_k + rank)
            )
        results: list[SearchHit] = []
        for key, fused_score in sorted(fused.items(), key=lambda item: item[1], reverse=True):
            source = corpus_by_key.get(key)
            if source is None:
                continue
            document, text, metadata = source
            results.append(SearchHit(
                document_id=document.document_id,
                title=document.title,
                source_type=document.source_type,
                source_url=document.source_url,
                chunk_index=key[1],
                content=text,
                score=fused_score,
                section_path=tuple(str(item) for item in metadata.get("section_path") or []),
                page_start=metadata.get("page_start"),
                page_end=metadata.get("page_end"),
            ))
        return results[:max(1, min(limit, 20))]

    async def reindex_embeddings(self, kb_id: str) -> int:
        if self.hybrid_retriever is None:
            raise RuntimeError("混合检索未启用")
        kb = await self._require_kb(kb_id)
        async with self.database.session() as session:
            documents = list(await session.scalars(
                select(KnowledgeDocumentRecord).where(
                    KnowledgeDocumentRecord.kb_id == kb_id,
                    KnowledgeDocumentRecord.status == "READY",
                )
            ))
        rebuilt = 0
        for document in documents:
            if not document.canonical_content.strip():
                logger.warning("Skipping document without canonical content: %s", document.document_id)
                continue
            await self._mark_ready(
                document.document_id,
                document.canonical_content,
                kb.chunk_size,
                kb.chunk_overlap,
            )
            rebuilt += 1
        return rebuilt

    async def stream_answer(self, kb_id: str, question: str) -> AsyncIterator[str]:
        hits = await self.search(kb_id, question)
        if not hits:
            yield "知识库中暂时没有找到与问题相关的内容。请先导入资料，或换一个更具体的问题。"
            return
        if self.chat_model:
            context = "\n\n".join(
                f"[来源 {index}: {self._source_label(hit)}]\n{hit.content}" for index, hit in enumerate(hits, 1)
            )
            try:
                from litellm import acompletion

                response = await acompletion(
                    model=self.chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "只依据给定资料回答；不确定时明确说明。使用 [来源 N] 标注依据。",
                        },
                        {"role": "user", "content": f"资料：\n{context}\n\n问题：{question}"},
                    ],
                    stream=True,
                )
                async for chunk in response:
                    content = chunk.choices[0].delta.content
                    if content:
                        yield content
                return
            except Exception:
                # The local evidence response keeps MRAG usable during provider outages.
                pass
        yield "根据知识库中最相关的资料，我找到以下依据：\n\n"
        for index, hit in enumerate(hits[:3], 1):
            excerpt = hit.content.strip().replace("\n\n\n", "\n\n")
            if len(excerpt) > 700:
                excerpt = excerpt[:700].rstrip() + "…"
            yield f"**[来源 {index}] {self._source_label(hit)}**\n\n{excerpt}\n\n"
        yield "以上是本地检索结果；配置 `CHAT_MODEL` 后会自动生成带来源标注的综合回答。"

    async def _require_kb(self, kb_id: str) -> KnowledgeBaseRecord:
        async with self.database.session() as session:
            record = await session.scalar(select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.kb_id == kb_id))
            if record is None:
                raise LookupError("知识库不存在")
            return record

    async def _mark_ready(
        self,
        document_id: str,
        canonical_content: str,
        chunk_size: int,
        chunk_overlap: int,
        *,
        title: str | None = None,
    ) -> None:
        canonical = canonical_content.strip()
        if not canonical:
            raise ValueError("没有从资料中提取到可检索文本")
        chunks = HierarchicalDocumentChunker(ChunkingConfig(
            max_tokens=chunk_size,
            overlap_tokens=chunk_overlap,
        )).chunk(canonical, document_id=document_id)
        if self.hybrid_retriever is not None:
            async with self.database.session() as session:
                source = await session.scalar(
                    select(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.document_id == document_id)
                )
            if source is None:
                raise LookupError("资料不存在")
            await self._run_vector_operation(self.hybrid_retriever.index_document(
                source.kb_id,
                document_id,
                title or source.title,
                chunks,
            ))
        async with self.database.session() as session:
            record = await session.scalar(select(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.document_id == document_id))
            if record is None:
                raise LookupError("资料不存在")
            record.canonical_content = canonical
            record.chunks = chunks
            record.status = "READY"
            record.error_message = ""
            record.updated_at = _utc_now()
            if title:
                record.title = title.strip()[:255]
            kb = await session.scalar(select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.kb_id == record.kb_id))
            if kb:
                kb.updated_at = _utc_now()
            await session.commit()

    async def _mark_failed(self, document_id: str, error_message: str) -> None:
        async with self.database.session() as session:
            record = await session.scalar(select(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.document_id == document_id))
            if record:
                record.status = "FAILED"
                record.error_message = (error_message or "资料处理失败")[:2_000]
                record.updated_at = _utc_now()
                await session.commit()

    async def _run_vector_operation(self, operation) -> None:
        try:
            await operation
        except Exception:
            if self.hybrid_required:
                raise
            logger.exception("Vector operation failed; lexical data remains available")

    @staticmethod
    def _extract_content(path: Path, content_type: str) -> str:
        extension = path.suffix.lower()
        if extension in {".txt", ".md", ".html", ".htm"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            return KnowledgeService._extract_html(text)[0] if extension in {".html", ".htm"} else text
        if extension == ".pdf":
            from pypdf import PdfReader

            return "\n\n".join(
                f"<!-- page:{page_number} -->\n\n{page.extract_text() or ''}"
                for page_number, page in enumerate(PdfReader(str(path)).pages, 1)
            )
        if extension == ".docx":
            from docx import Document
            from docx.oxml.table import CT_Tbl
            from docx.oxml.text.paragraph import CT_P
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            document = Document(str(path))
            blocks: list[str] = []
            for child in document.element.body.iterchildren():
                if isinstance(child, CT_P):
                    paragraph = Paragraph(child, document)
                    content = paragraph.text.strip()
                    if not content:
                        continue
                    style_name = (paragraph.style.name if paragraph.style else "") or ""
                    heading = re.search(r"(?:Heading|标题)\s*(\d+)", style_name, re.IGNORECASE)
                    if heading:
                        level = max(1, min(int(heading.group(1)), 6))
                        blocks.append(f"{'#' * level} {content}")
                    elif "list" in style_name.lower() or "列表" in style_name:
                        blocks.append(f"- {content}")
                    else:
                        blocks.append(content)
                elif isinstance(child, CT_Tbl):
                    table = Table(child, document)
                    rows = [
                        [cell.text.strip().replace("|", "\\|") for cell in row.cells]
                        for row in table.rows
                    ]
                    if rows:
                        width = max(len(row) for row in rows)
                        padded = [row + [""] * (width - len(row)) for row in rows]
                        markdown = [
                            "| " + " | ".join(padded[0]) + " |",
                            "| " + " | ".join(["---"] * width) + " |",
                            *("| " + " | ".join(row) + " |" for row in padded[1:]),
                        ]
                        blocks.append("\n".join(markdown))
            return "\n\n".join(blocks)
        if extension == ".csv":
            import pandas as pd

            return pd.read_csv(path).to_markdown(index=False)
        if extension == ".xlsx":
            import pandas as pd

            workbook = pd.read_excel(path, sheet_name=None)
            return "\n\n".join(f"## {name}\n\n{frame.to_markdown(index=False)}" for name, frame in workbook.items())
        if extension in {".jpg", ".jpeg", ".png", ".webp"}:
            from PIL import Image

            with Image.open(path) as image:
                return f"图片资料：{path.name}\n尺寸：{image.width}×{image.height}\n格式：{image.format or content_type}"
        raise ValueError(f"暂不支持解析 {extension}")

    async def _extract_source(self, path: Path, content_type: str) -> str:
        canonical = await asyncio.to_thread(self._extract_content, path, content_type)
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or not self.chat_model:
            return canonical
        try:
            from litellm import acompletion

            encoded = base64.b64encode(await asyncio.to_thread(path.read_bytes)).decode("ascii")
            mime = content_type if content_type.startswith("image/") else f"image/{path.suffix.lower().lstrip('.')}"
            response = await acompletion(
                model=self.chat_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "详细描述这张知识库图片中的对象、文字、结构、数据与可检索事实。"},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    ],
                }],
                stream=False,
            )
            caption = response.choices[0].message.content or ""
            return f"{canonical}\n\n图片语义描述：\n{caption.strip()}" if caption.strip() else canonical
        except Exception:
            return canonical + "\n图片语义描述生成失败；原图仍可预览，配置支持视觉的 CHAT_MODEL 后可重新导入。"

    @staticmethod
    def _extract_html(html: str) -> tuple[str, str]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for element in soup(["script", "style", "noscript", "svg"]):
            element.decompose()
        try:
            import trafilatura

            extracted = trafilatura.extract(html, include_links=True, include_tables=True, output_format="markdown")
        except Exception:
            extracted = None
        return (extracted or soup.get_text("\n", strip=True)), title

    @staticmethod
    def _chunk(text: str, size: int, overlap: int) -> list[str]:
        """Compatibility wrapper for callers that only need chunk text."""
        chunks = HierarchicalDocumentChunker(ChunkingConfig(
            max_tokens=size,
            overlap_tokens=overlap,
        )).chunk(text)
        return [str(item["text"]) for item in chunks]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = [item.lower() for item in _TOKEN_PATTERN.findall(text)]
        chinese = [item for item in tokens if "\u4e00" <= item <= "\u9fff"]
        tokens.extend("".join(chinese[index:index + 2]) for index in range(len(chinese) - 1))
        return tokens

    @staticmethod
    def _source_label(hit: SearchHit) -> str:
        label = hit.title
        if hit.section_path:
            label += " → " + " → ".join(hit.section_path)
        if hit.page_start is not None:
            page = str(hit.page_start)
            if hit.page_end is not None and hit.page_end != hit.page_start:
                page += f"–{hit.page_end}"
            label += f"，第 {page} 页"
        return label

    def _ensure_storage_target(self, target: Path) -> None:
        if target == self.storage_path or self.storage_path not in target.parents:
            raise ValueError("非法的知识库文件路径")

    @staticmethod
    def _kb_view(record: KnowledgeBaseRecord, document_count: int) -> KnowledgeBaseView:
        return KnowledgeBaseView(
            kb_id=record.kb_id,
            name=record.name,
            description=record.description,
            chunk_size=record.chunk_size,
            chunk_overlap=record.chunk_overlap,
            document_count=document_count,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _document_view(record: KnowledgeDocumentRecord) -> KnowledgeDocumentView:
        return KnowledgeDocumentView(
            document_id=record.document_id,
            kb_id=record.kb_id,
            title=record.title,
            source_type=record.source_type,
            source_url=record.source_url,
            content_type=record.content_type,
            file_ext=record.file_ext,
            file_size=record.file_size,
            status=record.status,
            error_message=record.error_message,
            chunk_count=len(record.chunks or []),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
