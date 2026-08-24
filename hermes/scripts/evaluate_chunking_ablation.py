from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import AsyncOpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hermes_officer.application.document_chunker import (
    ApproximateTokenCounter,
    ChunkingConfig,
    HierarchicalDocumentChunker,
)
from hermes_officer.application.knowledge_service import KnowledgeService
from hermes_officer.core.config import AppSettings


SOURCE_DIR = PROJECT_ROOT / "examples" / "knowledge" / "starboat_support"
DATASET = PROJECT_ROOT / "evals" / "enterprise_kb_retrieval.json"
EXPECTED_SECTIONS = {
    "exact-error-code": "E17 供水异常",
    "semantic-overheat": "E23 研磨电机过热",
    "exact-sla": "P1 紧急事件",
    "semantic-warranty": "不在标准保修范围",
    "semantic-approval": "智能助手不得执行的事项",
    "exact-product": "X2 Pro 企业版",
    "semantic-isolation": "权限与租户隔离",
}


@dataclass(frozen=True, slots=True)
class EvalChunk:
    title: str
    index: int
    text: str
    section_path: tuple[str, ...]


def fixed_window(text: str, size: int = 700, overlap: int = 100) -> list[str]:
    """The character-window baseline replaced by the structure-aware chunker."""
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(normalized) <= size:
        return [normalized]
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = max(
                normalized.rfind("\n", start + size // 2, end),
                normalized.rfind("。", start + size // 2, end),
            )
            if boundary > start:
                end = boundary + 1
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return [item for item in chunks if item]


def build_corpora() -> dict[str, list[EvalChunk]]:
    corpora: dict[str, list[EvalChunk]] = {"fixed_window": [], "structure_token": []}
    chunker = HierarchicalDocumentChunker(ChunkingConfig(max_tokens=700, overlap_tokens=100))
    for path in sorted(SOURCE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        corpora["fixed_window"].extend(
            EvalChunk(path.name, index, chunk, ())
            for index, chunk in enumerate(fixed_window(text))
        )
        corpora["structure_token"].extend(
            EvalChunk(
                path.name,
                int(chunk["chunk_index"]),
                str(chunk["text"]),
                tuple(str(item) for item in chunk["section_path"]),
            )
            for chunk in chunker.chunk(text)
        )
    return corpora


async def embed(client: AsyncOpenAI, model: str, texts: list[str]) -> np.ndarray:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 16):
        response = await client.embeddings.create(model=model, input=texts[start:start + 16])
        vectors.extend(list(item.embedding) for item in sorted(response.data, key=lambda item: item.index))
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def lexical_ranking(chunks: list[EvalChunk], query: str) -> list[int]:
    tokenized = [KnowledgeService._tokenize(chunk.text) for chunk in chunks]
    query_tokens = KnowledgeService._tokenize(query)
    doc_freq = Counter(
        token for token in set(query_tokens) for tokens in tokenized if token in set(tokens)
    )
    average_length = sum(len(tokens) for tokens in tokenized) / max(len(tokenized), 1)
    lexical_terms = {token for token in query_tokens if len(token) > 1}
    scored: list[tuple[int, float]] = []
    for index, tokens in enumerate(tokenized):
        frequencies = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            inverse_frequency = math.log(
                1 + (len(chunks) - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * len(tokens) / max(average_length, 1)
            )
            score += inverse_frequency * frequency * 2.5 / denominator
        coverage = sum(bool(frequencies[token]) for token in lexical_terms) / max(len(lexical_terms), 1)
        if score > 0 and coverage >= 0.20:
            scored.append((index, score))
    return [index for index, _ in sorted(scored, key=lambda item: item[1], reverse=True)]


def hybrid_ranking(
    chunks: list[EvalChunk],
    chunk_vectors: np.ndarray,
    query_vector: np.ndarray,
    query: str,
    *,
    dense_threshold: float,
    lexical_weight: float,
    dense_weight: float,
) -> list[int]:
    lexical = lexical_ranking(chunks, query)
    similarities = chunk_vectors @ query_vector
    dense = [
        int(index)
        for index in np.argsort(-similarities)
        if float(similarities[index]) >= dense_threshold
    ]
    fused: Counter[int] = Counter()
    for rank, index in enumerate(lexical, 1):
        fused[index] += lexical_weight / (60 + rank)
    for rank, index in enumerate(dense, 1):
        fused[index] += dense_weight / (60 + rank)
    return [index for index, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True)]


def expected_rank(chunks: list[EvalChunk], ranking: list[int], title: str | None) -> int | None:
    if title is None:
        return None
    for rank, index in enumerate(ranking[:5], 1):
        if chunks[index].title == title:
            return rank
    return None


async def evaluate() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    settings = AppSettings.from_env()
    settings.validate_for_startup()
    if not settings.text_embedding_model or not settings.text_embedding_api_key:
        raise RuntimeError("切分消融实验要求配置文本 Embedding")
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    corpora = build_corpora()
    token_counter = ApproximateTokenCounter()
    client = AsyncOpenAI(
        base_url=settings.text_embedding_base_url,
        api_key=settings.text_embedding_api_key,
    )
    query_vectors = await embed(client, settings.text_embedding_model, [case["query"] for case in cases])
    results: dict[str, dict] = {}
    try:
        for strategy, chunks in corpora.items():
            chunk_vectors = await embed(client, settings.text_embedding_model, [chunk.text for chunk in chunks])
            rows = []
            for case_index, case in enumerate(cases):
                ranking = hybrid_ranking(
                    chunks,
                    chunk_vectors,
                    query_vectors[case_index],
                    case["query"],
                    dense_threshold=settings.hybrid_dense_min_score,
                    lexical_weight=settings.hybrid_lexical_weight,
                    dense_weight=settings.hybrid_dense_weight,
                )
                rank = expected_rank(chunks, ranking, case["expected_title"])
                expected_section = EXPECTED_SECTIONS.get(case["id"])
                section_correct = False
                if expected_section and rank is not None:
                    matching = next(
                        (chunks[index] for index in ranking[:5] if chunks[index].title == case["expected_title"]),
                        None,
                    )
                    section_correct = bool(matching and expected_section in matching.section_path)
                rows.append({
                    "id": case["id"],
                    "kind": case["kind"],
                    "expected_title": case["expected_title"],
                    "rank": rank,
                    "top1_title": chunks[ranking[0]].title if ranking else None,
                    "top1_section_path": list(chunks[ranking[0]].section_path) if ranking else [],
                    "top1_context_tokens": token_counter.count(chunks[ranking[0]].text) if ranking else 0,
                    "top3_context_tokens": sum(
                        token_counter.count(chunks[index].text) for index in ranking[:3]
                    ),
                    "section_correct": section_correct,
                    "rejected": not ranking,
                })
            positives = [row for row in rows if row["expected_title"] is not None]
            negatives = [row for row in rows if row["expected_title"] is None]
            hit_rates = {
                k: round(
                    sum(row["rank"] is not None and row["rank"] <= k for row in positives) / len(positives),
                    4,
                )
                for k in (1, 3, 5)
            }
            results[strategy] = {
                "chunk_count": len(chunks),
                "hit_rate_at_1": hit_rates[1],
                "hit_rate_at_3": hit_rates[3],
                "hit_rate_at_5": hit_rates[5],
                "recall_at_1": hit_rates[1],
                "recall_at_3": hit_rates[3],
                "recall_at_5": hit_rates[5],
                "mrr_at_5": round(sum(1 / row["rank"] if row["rank"] else 0 for row in positives) / len(positives), 4),
                "top1_document_accuracy": round(sum(row["rank"] == 1 for row in positives) / len(positives), 4),
                "section_citation_accuracy": round(sum(row["section_correct"] for row in positives) / len(positives), 4),
                "negative_rejection_accuracy": round(sum(row["rejected"] for row in negatives) / max(len(negatives), 1), 4),
                "average_top1_context_tokens": round(
                    sum(row["top1_context_tokens"] for row in positives) / len(positives), 2
                ),
                "average_top3_context_tokens": round(
                    sum(row["top3_context_tokens"] for row in positives) / len(positives), 2
                ),
                "cases": rows,
            }
    finally:
        await client.close()

    baseline = results["fixed_window"]
    candidate = results["structure_token"]
    return {
        "dataset": DATASET.name,
        "positive_cases": sum(case["expected_title"] is not None for case in cases),
        "negative_cases": sum(case["expected_title"] is None for case in cases),
        "controlled_variables": {
            "embedding_model": settings.text_embedding_model,
            "dense_threshold": settings.hybrid_dense_min_score,
            "lexical_weight": settings.hybrid_lexical_weight,
            "dense_weight": settings.hybrid_dense_weight,
            "top_k": 5,
            "max_size": 700,
            "overlap": 100,
        },
        "metric_note": (
            "Each positive query has one relevant document label, so binary HitRate@K equals Recall@K. "
            "Evidence-level multi-relevance qrels are required to distinguish them."
        ),
        "strategies": results,
        "delta_percentage_points": {
            key: round((candidate[key] - baseline[key]) * 100, 2)
            for key in (
                "hit_rate_at_1",
                "hit_rate_at_3",
                "hit_rate_at_5",
                "recall_at_1",
                "recall_at_3",
                "recall_at_5",
                "mrr_at_5",
                "top1_document_accuracy",
                "section_citation_accuracy",
                "negative_rejection_accuracy",
            )
        },
        "context_token_reduction_percent": {
            "top1": round(
                (1 - candidate["average_top1_context_tokens"] / baseline["average_top1_context_tokens"]) * 100,
                2,
            ),
            "top3": round(
                (1 - candidate["average_top3_context_tokens"] / baseline["average_top3_context_tokens"]) * 100,
                2,
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="固定字符滑窗与结构化 Token 切分消融实验")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "evals" / "results" / "chunking_ablation_latest.json",
    )
    args = parser.parse_args()
    result = asyncio.run(evaluate())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"result_file={args.output}")


if __name__ == "__main__":
    main()
