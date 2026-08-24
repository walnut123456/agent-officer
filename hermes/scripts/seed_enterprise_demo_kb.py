from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hermes_officer.application.hybrid_retrieval import build_hybrid_retriever
from hermes_officer.application.knowledge_service import KnowledgeService
from hermes_officer.core.config import AppSettings
from hermes_officer.infrastructure.database import Database


KB_ID = "starboat-enterprise-support-v1"
KB_NAME = "星舟企业设备售后知识库"
KB_DESCRIPTION = "面向企业客户的产品、故障、SLA、保修与合规知识；采用 BM25 + Embedding 混合检索。"
SOURCE_DIR = PROJECT_ROOT / "examples" / "knowledge" / "starboat_support"


async def seed(*, reindex: bool = False) -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    settings = AppSettings.from_env()
    settings.validate_for_startup()
    if not settings.knowledge_hybrid_enabled:
        raise RuntimeError("企业演示知识库要求启用 KNOWLEDGE_HYBRID_ENABLED")

    database = Database(settings.database_url)
    retriever = build_hybrid_retriever(settings)
    service = KnowledgeService(
        database,
        settings.knowledge_storage_path,
        chat_model=settings.chat_model,
        hybrid_retriever=retriever,
        hybrid_required=True,
    )
    created = False
    ingested: list[str] = []
    skipped: list[str] = []
    try:
        await database.initialize()
        knowledge_base = await service.get_knowledge_base(KB_ID)
        if knowledge_base is None:
            knowledge_base = await service.create_knowledge_base(
                KB_NAME,
                KB_DESCRIPTION,
                kb_id=KB_ID,
                chunk_size=700,
                chunk_overlap=100,
            )
            created = True

        existing = {item.title: item for item in await service.list_documents(KB_ID)}
        for path in sorted(SOURCE_DIR.glob("*.md")):
            current = existing.get(path.name)
            if current is not None and current.status == "READY":
                skipped.append(path.name)
                continue
            if current is not None:
                await service.delete_document(KB_ID, current.document_id)
            document = await service.ingest_file(
                KB_ID,
                path.name,
                path.read_bytes(),
                "text/markdown",
            )
            if document.status != "READY":
                raise RuntimeError(f"{path.name} 导入失败：{document.error_message}")
            ingested.append(path.name)

        reindexed = await service.reindex_embeddings(KB_ID) if reindex else 0
        final = await service.get_knowledge_base(KB_ID)
        assert final is not None
        return {
            "kb_id": KB_ID,
            "name": final.name,
            "created": created,
            "document_count": final.document_count,
            "ingested": ingested,
            "skipped": skipped,
            "reindexed": reindexed,
            "retrieval": "BM25 + dense embedding + weighted RRF",
            "embedding_model": settings.text_embedding_model,
            "vector_store": "Qdrant",
        }
    finally:
        await service.close()
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 Hermes 智维企业售后演示知识库")
    parser.add_argument("--reindex", action="store_true", help="重新切分资料并生成该知识库全部向量")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(seed(reindex=args.reindex)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
