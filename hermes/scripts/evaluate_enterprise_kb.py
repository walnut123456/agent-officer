from __future__ import annotations

import asyncio
import argparse
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
DATASET = PROJECT_ROOT / "evals" / "enterprise_kb_retrieval.json"


def _rank(hits, expected_title: str | None) -> int | None:
    if expected_title is None:
        return None
    for rank, hit in enumerate(hits, 1):
        if hit.title == expected_title:
            return rank
    return None


async def evaluate() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    settings = AppSettings.from_env()
    settings.validate_for_startup()
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    database = Database(settings.database_url)
    retriever = build_hybrid_retriever(settings)
    if retriever is None:
        raise RuntimeError("企业检索测评要求启用混合检索")
    hybrid = KnowledgeService(
        database,
        settings.knowledge_storage_path,
        chat_model=None,
        hybrid_retriever=retriever,
        hybrid_required=True,
    )
    lexical = KnowledgeService(database, settings.knowledge_storage_path, chat_model=None)
    rows = []
    try:
        await database.initialize()
        for case in cases:
            lexical_hits = await lexical.search(KB_ID, case["query"], limit=5)
            dense_hits = await retriever.search(KB_ID, case["query"], limit=5)
            hybrid_hits = await hybrid.search(KB_ID, case["query"], limit=5)
            lexical_rank = _rank(lexical_hits, case["expected_title"])
            hybrid_rank = _rank(hybrid_hits, case["expected_title"])
            is_negative = case["expected_title"] is None
            passed = not hybrid_hits if is_negative else hybrid_rank is not None and hybrid_rank <= 3
            rows.append({
                **case,
                "lexical_rank": lexical_rank,
                "hybrid_rank": hybrid_rank,
                "lexical_top1": lexical_hits[0].title if lexical_hits else None,
                "hybrid_top1": hybrid_hits[0].title if hybrid_hits else None,
                "dense_top_score": round(dense_hits[0].score, 4) if dense_hits else None,
                "passed": passed,
            })
    finally:
        await hybrid.close()
        await database.dispose()

    total = len(rows)
    positive_rows = [row for row in rows if row["expected_title"] is not None]
    negative_rows = [row for row in rows if row["expected_title"] is None]
    lexical_recall = sum(row["lexical_rank"] is not None and row["lexical_rank"] <= 3 for row in positive_rows) / len(positive_rows)
    hybrid_recall = sum(row["hybrid_rank"] is not None and row["hybrid_rank"] <= 3 for row in positive_rows) / len(positive_rows)
    lexical_mrr = sum(1 / row["lexical_rank"] if row["lexical_rank"] else 0 for row in positive_rows) / len(positive_rows)
    hybrid_mrr = sum(1 / row["hybrid_rank"] if row["hybrid_rank"] else 0 for row in positive_rows) / len(positive_rows)
    negative_accuracy = sum(row["passed"] for row in negative_rows) / len(negative_rows) if negative_rows else 1.0
    return {
        "dataset": DATASET.name,
        "kb_id": KB_ID,
        "case_count": total,
        "metrics": {
            "lexical_recall_at_3": round(lexical_recall, 4),
            "hybrid_recall_at_3": round(hybrid_recall, 4),
            "lexical_mrr_at_5": round(lexical_mrr, 4),
            "hybrid_mrr_at_5": round(hybrid_mrr, 4),
            "negative_rejection_accuracy": round(negative_accuracy, 4),
        },
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="测评企业知识库混合检索")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "evals" / "results" / "enterprise_kb_latest.json",
    )
    args = parser.parse_args()
    result = asyncio.run(evaluate())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"result_file={args.output}")


if __name__ == "__main__":
    main()
