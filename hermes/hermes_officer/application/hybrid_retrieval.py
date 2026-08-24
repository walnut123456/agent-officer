from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient, models

from hermes_officer.core.config import AppSettings


@dataclass(frozen=True, slots=True)
class DenseSearchHit:
    document_id: str
    chunk_index: int
    score: float


class QdrantHybridRetriever:
    """Dense retrieval adapter; MySQL remains the canonical document store."""

    def __init__(self, settings: AppSettings) -> None:
        if not settings.text_embedding_model or not settings.text_embedding_api_key:
            raise ValueError("文本 Embedding 配置不完整")
        if not settings.qdrant_url and not settings.qdrant_path:
            raise ValueError("Qdrant 配置不完整")
        if settings.qdrant_url and not settings.qdrant_api_key:
            raise ValueError("远程 Qdrant 缺少 API Key")
        self.model = settings.text_embedding_model
        self.dimension = settings.text_embedding_dimension
        self.max_text_length = settings.text_embedding_max_length
        self.collection = settings.qdrant_collection
        self.dense_min_score = settings.hybrid_dense_min_score
        self.dense_weight = settings.hybrid_dense_weight
        self.lexical_weight = settings.hybrid_lexical_weight
        self.embedding_client = AsyncOpenAI(
            base_url=settings.text_embedding_base_url,
            api_key=settings.text_embedding_api_key,
        )
        if settings.qdrant_path:
            settings.qdrant_path.mkdir(parents=True, exist_ok=True)
            self.vector_client = AsyncQdrantClient(path=str(settings.qdrant_path))
        else:
            self.vector_client = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key,
                prefer_grpc=settings.qdrant_prefer_grpc,
                timeout=int(settings.qdrant_timeout),
                check_compatibility=False,
            )
        self._collection_ready = False
        self._collection_lock = asyncio.Lock()
        self._query_cache: dict[str, list[float]] = {}

    async def close(self) -> None:
        await self.vector_client.close()

    async def healthcheck(self) -> None:
        await self._ensure_collection()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        normalized = [text.strip()[: self.max_text_length] for text in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(normalized), 16):
            response = await self.embedding_client.embeddings.create(
                model=self.model,
                input=normalized[start:start + 16],
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([list(item.embedding) for item in ordered])
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding 返回数量与输入文本数量不一致")
        invalid = [len(vector) for vector in vectors if len(vector) != self.dimension]
        if invalid:
            raise RuntimeError(
                f"Embedding 维度不匹配：配置 {self.dimension}，实际 {invalid[0]}"
            )
        return vectors

    async def index_document(
        self,
        kb_id: str,
        document_id: str,
        title: str,
        chunks: list[dict],
    ) -> None:
        await self._ensure_collection()
        texts = [str(item.get("text") or "") for item in chunks]
        vectors = await self.embed_texts(texts)
        await self.delete_document(document_id)
        points = [
            models.PointStruct(
                id=str(uuid5(NAMESPACE_URL, f"hermes:{kb_id}:{document_id}:{index}")),
                vector=vector,
                payload={
                    "kb_id": kb_id,
                    "document_id": document_id,
                    "chunk_index": index,
                    "title": title,
                    "section_path": list(item.get("section_path") or []),
                    "page_start": item.get("page_start"),
                    "page_end": item.get("page_end"),
                    "content_hash": str(item.get("content_hash") or ""),
                },
            )
            for index, (item, vector) in enumerate(zip(chunks, vectors, strict=True))
        ]
        if points:
            await self.vector_client.upsert(
                collection_name=self.collection,
                points=points,
                wait=True,
            )

    async def search(self, kb_id: str, query: str, *, limit: int) -> list[DenseSearchHit]:
        await self._ensure_collection()
        cache_key = query.strip()
        query_vector = self._query_cache.get(cache_key)
        if query_vector is None:
            query_vector = (await self.embed_texts([cache_key]))[0]
            if len(self._query_cache) >= 128:
                self._query_cache.pop(next(iter(self._query_cache)))
            self._query_cache[cache_key] = query_vector
        response = await self.vector_client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="kb_id",
                        match=models.MatchValue(value=kb_id),
                    )
                ]
            ),
            limit=max(1, min(limit, 100)),
            score_threshold=self.dense_min_score,
            with_payload=True,
        )
        hits: list[DenseSearchHit] = []
        for point in response.points:
            payload = point.payload or {}
            document_id = str(payload.get("document_id") or "")
            if not document_id:
                continue
            hits.append(DenseSearchHit(
                document_id=document_id,
                chunk_index=int(payload.get("chunk_index") or 0),
                score=float(point.score),
            ))
        return hits

    async def delete_document(self, document_id: str) -> None:
        await self._ensure_collection()
        await self.vector_client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document_id),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def delete_knowledge_base(self, kb_id: str) -> None:
        await self._ensure_collection()
        await self.vector_client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="kb_id",
                            match=models.MatchValue(value=kb_id),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        async with self._collection_lock:
            if self._collection_ready:
                return
            exists = await self.vector_client.collection_exists(self.collection)
            if not exists:
                await self.vector_client.create_collection(
                    collection_name=self.collection,
                    vectors_config=models.VectorParams(
                        size=self.dimension,
                        distance=models.Distance.COSINE,
                    ),
                )
                for field in ("kb_id", "document_id"):
                    await self.vector_client.create_payload_index(
                        collection_name=self.collection,
                        field_name=field,
                        field_schema=models.PayloadSchemaType.KEYWORD,
                        wait=True,
                    )
            else:
                info = await self.vector_client.get_collection(self.collection)
                vectors = info.config.params.vectors
                actual_dimension = getattr(vectors, "size", None)
                if actual_dimension is None and isinstance(vectors, dict):
                    vector_config = vectors.get("") or vectors.get("vector")
                    actual_dimension = getattr(vector_config, "size", None)
                if actual_dimension and int(actual_dimension) != self.dimension:
                    raise RuntimeError(
                        f"Qdrant collection 维度不匹配：{actual_dimension} != {self.dimension}"
                    )
            self._collection_ready = True


def build_hybrid_retriever(settings: AppSettings) -> QdrantHybridRetriever | None:
    if not settings.knowledge_hybrid_enabled:
        return None
    return QdrantHybridRetriever(settings)
