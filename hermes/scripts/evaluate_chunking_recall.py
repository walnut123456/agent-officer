from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import AsyncOpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hermes_officer.application.document_chunker import ChunkingConfig, HierarchicalDocumentChunker
from hermes_officer.core.config import AppSettings
from scripts.evaluate_chunking_ablation import EvalChunk, embed, fixed_window, hybrid_ranking


SOURCE_DIR = PROJECT_ROOT / "examples" / "knowledge" / "starboat_support"


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    queries: tuple[str, str]
    # A case can require several evidence units. Every string inside one unit
    # must occur in the same retrieved chunk for that unit to count as covered.
    evidence_units: tuple[tuple[str, ...], ...]


CASES = (
    BenchmarkCase("x1-capacity", ("X1 水箱多大，适合多少杯？", "小办公室每天 80 杯该选哪款，水箱容量是多少？"), (("2.2 升", "80 杯"),)),
    BenchmarkCase("x1-cleaning", ("X1 做多少杯会提醒深度清洁？", "标准版咖啡机的深度清洁计数是多少？"), (("200 杯", "深度清洁"),)),
    BenchmarkCase("x1-features", ("X1 支持双豆仓和自动奶路清洗吗？", "标准版有没有双豆仓、自动洗奶路？"), (("不支持双豆仓", "自动奶路清洗"),)),
    BenchmarkCase("x2-capacity", ("X2 水箱容量和建议日杯量是多少？", "中型办公区每天约 200 杯应该选什么容量？"), (("3.5 升", "200 杯"),)),
    BenchmarkCase("x2-cleaning", ("X2 累计多少杯提示深度清洁？", "专业版的清洁提醒周期是多少杯？"), (("160 杯", "深度清洁"),)),
    BenchmarkCase("x2pro-water", ("X2 Pro 是水箱还是直连进水，日杯量多少？", "每天 500 杯的企业机型采用什么供水方式？"), (("直连进水", "500 杯"),)),
    BenchmarkCase("x2pro-install", ("X2 Pro 首次安装可以自己做吗？", "企业版第一次部署对安装方有什么要求？"), (("首次安装", "认证服务商"),)),
    BenchmarkCase("enterprise-package", ("企业服务包支持时段和巡检内容是什么？", "需要季度巡检应该购买哪个服务包，服务时间多长？"), (("7×12 小时", "季度巡检"),)),
    BenchmarkCase("premium-package", ("哪个服务包有 7×24 P1 响应和备用机？", "全天紧急支持与备用机服务属于什么套餐？"), (("7×24 小时", "备用机服务"),)),
    BenchmarkCase("e17-checks", ("E17 首先要检查哪三项？", "供水异常时水箱、滤芯和浮子分别怎么排查？"), (("水箱水位", "滤芯是否堵塞", "浮子可以活动"),)),
    BenchmarkCase("e17-escalation", ("E17 检查后仍没恢复怎么建单？", "供水故障重启无效后工单要带哪些现场资料？"), (("P2 工单", "设备序列号", "现场照片"),)),
    BenchmarkCase("e17-direct-water", ("X2 Pro 出现 E17 还要检查什么？", "直连进水机型供水异常要看阀门和管路吗？"), (("进水阀已打开", "管路无折弯"),)),
    BenchmarkCase("e23-first-action", ("E23 过热后要停多久并清理哪里？", "研磨电机过热停机后如何初步处理？"), (("等待 15 分钟", "豆仓出口"),)),
    BenchmarkCase("e23-tool-safety", ("处理 E23 能用金属工具伸进研磨组件吗？", "研磨组件卡住时哪些清理方式被禁止？"), (("不得使用金属工具", "研磨组件"),)),
    BenchmarkCase("e23-escalation", ("E23 等待后还出现，工单级别和响应时间是什么？", "研磨电机过热没有恢复要多快响应？"), (("P1 工单", "1 小时内首次响应"),)),
    BenchmarkCase("e31-checks", ("E31 奶路清洗失败要检查什么？", "奶路清洗异常对清洗液和奶管有什么要求？"), (("食品级专用清洗液", "奶管是否折弯"),)),
    BenchmarkCase("e31-escalation", ("E31 连续失败几次后建什么工单？", "完整清洗程序重复失败后如何升级？"), (("连续两次失败", "P2 工单"),)),
    BenchmarkCase("low-temperature", ("咖啡温度偏低，深度清洁后仍异常怎么办？", "无错误码但出杯温度不达标应建哪级工单？"), (("深度清洁", "P3 工单"),)),
    BenchmarkCase("safety-anomaly", ("机器冒烟或漏电时第一步怎么做？", "液体进入机身后可以再次通电拍视频吗？"), (("立即断电", "隔离现场", "P1"),)),
    BenchmarkCase("p1-sla", ("P1 首次响应和处置方案时限是多少？", "紧急事件一小时响应后多久给方案？"), (("1 小时内首次响应", "4 小时内给出处置方案"),)),
    BenchmarkCase("p2-sla", ("P2 的响应和处置时限是什么？", "高优先级故障多久联系、多久出方案？"), (("4 小时内首次响应", "1 个工作日内给出处置方案"),)),
    BenchmarkCase("p3-sla", ("P3 工单多久响应和解决？", "一般问题的首次响应及处置方案时限是多少？"), (("1 个工作日内首次响应", "3 个工作日内给出处置方案"),)),
    BenchmarkCase("repeat-upgrade", ("同一故障七天内重复两次怎么处理？", "短期重复故障什么时候自动升级？"), (("7 天内重复出现两次", "自动升级一级"),)),
    BenchmarkCase("p1-close", ("用户一直不回复，可以直接关闭 P1 吗？", "紧急工单失联后满足什么条件才能关闭？"), (("三次跨时段联系", "值班经理确认"),)),
    BenchmarkCase("x1-warranty", ("X1 整机和研磨组件各保修几年？", "标准版主机与研磨部件的保修期分别多长？"), (("整机保修 2 年", "研磨组件保修 3 年"),)),
    BenchmarkCase("x2pro-warranty", ("X2 Pro 整机和核心泵组保修多久？", "企业版咖啡机核心泵组有几年质保？"), (("整机保修 3 年", "核心泵组保修 5 年"),)),
    BenchmarkCase("warranty-exclusion", ("客户私自拆机后还能直接认定免费保修吗？", "人为损坏由谁认定，私拆是否在标准保修内？"), (("私自拆机", "不在标准保修范围", "授权工程师"),)),
    BenchmarkCase("arrival-damage", ("到货破损最迟几天发现、几小时提交照片？", "运输损坏的申报窗口和材料时限是什么？"), (("7 个自然日", "24 小时内提交照片"),)),
    BenchmarkCase("no-reason-return", ("企业设备安装验收后还能无理由退货吗？", "已经完成安装验收，合同没特殊约定能否七天无理由？"), (("安装验收", "不适用无理由退货"),)),
    BenchmarkCase("refund-approval", ("智能助手可以直接批准退款和费用减免吗？", "换机赔付由机器人决定还是需要人工？"), (("退款", "人工审批", "智能助手只能"),)),
    BenchmarkCase("tenant-isolation", ("A 公司员工能搜索 B 公司内部资料吗？", "租户名称很相似时允许跨企业查询吗？"), (("所属租户", "不得根据相似客户名称跨租户查询"),)),
    BenchmarkCase("prompt-injection", ("资料里写着忽略系统规则，助手应该照做吗？", "知识库附件中的删除工具指令可以执行吗？"), (("不可信数据", "不能作为系统指令执行"),)),
    BenchmarkCase(
        "e17-cross-section",
        ("E17 完成检查仍失败，要建哪级工单且多久首次响应？", "供水异常无法恢复后的升级等级和 SLA 是什么？"),
        (("E17", "P2 工单"), ("P2 高优先级故障", "4 小时内首次响应")),
    ),
    BenchmarkCase(
        "safety-cross-section",
        ("设备冒烟时怎么处置，相关工具调用还要记录哪些审计字段？", "安全事故建单后，系统审计至少记录什么？"),
        (("立即断电", "P1"), ("request_id", "工具名称", "状态")),
    ),
)


def _normalize(text: str) -> str:
    return "".join(text.lower().split())


def _covers(chunk: str, evidence_unit: tuple[str, ...]) -> bool:
    normalized = _normalize(chunk)
    return all(_normalize(term) in normalized for term in evidence_unit)


def _long_handbook() -> str:
    sections: list[str] = ["# 星舟企业智能设备售后运营总手册"]
    for path in sorted(SOURCE_DIR.glob("*.md")):
        sections.extend((f"\n# 来源文档：{path.stem}", path.read_text(encoding="utf-8")))
    return "\n\n".join(sections)


def _build_corpora() -> dict[str, list[EvalChunk]]:
    handbook = _long_handbook()
    fixed = [
        EvalChunk("星舟企业智能设备售后运营总手册", index, text, ())
        for index, text in enumerate(fixed_window(handbook, size=700, overlap=100))
    ]
    chunker = HierarchicalDocumentChunker(ChunkingConfig(max_tokens=700, overlap_tokens=100))
    structured = [
        EvalChunk(
            "星舟企业智能设备售后运营总手册",
            int(item["chunk_index"]),
            str(item["text"]),
            tuple(str(part) for part in item["section_path"]),
        )
        for item in chunker.chunk(handbook, document_id="starboat-enterprise-handbook")
    ]
    return {"fixed_char_window": fixed, "structure_token": structured}


def _evidence_recall(chunks: list[EvalChunk], ranking: list[int], case: BenchmarkCase, k: int) -> float:
    retrieved = [chunks[index].text for index in ranking[:k]]
    covered = sum(any(_covers(chunk, unit) for chunk in retrieved) for unit in case.evidence_units)
    return covered / len(case.evidence_units)


def _evidence_retention(chunks: list[EvalChunk], case: BenchmarkCase) -> float:
    covered = sum(any(_covers(chunk.text, unit) for chunk in chunks) for unit in case.evidence_units)
    return covered / len(case.evidence_units)


def _paired_bootstrap_ci(
    baseline_rows: list[dict],
    candidate_rows: list[dict],
    metric: str,
    *,
    samples: int = 10_000,
) -> dict[str, float]:
    """Paired bootstrap by intent so two paraphrases are not treated as independent facts."""
    case_ids = [case.case_id for case in CASES]
    baseline_by_case = {
        case_id: np.mean([row[metric] for row in baseline_rows if row["case_id"] == case_id])
        for case_id in case_ids
    }
    candidate_by_case = {
        case_id: np.mean([row[metric] for row in candidate_rows if row["case_id"] == case_id])
        for case_id in case_ids
    }
    deltas = np.asarray(
        [candidate_by_case[case_id] - baseline_by_case[case_id] for case_id in case_ids],
        dtype=np.float64,
    )
    rng = np.random.default_rng(42)
    sampled = rng.choice(deltas, size=(samples, len(deltas)), replace=True).mean(axis=1) * 100
    return {
        "lower": round(float(np.quantile(sampled, 0.025)), 2),
        "upper": round(float(np.quantile(sampled, 0.975)), 2),
    }


async def evaluate() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    settings = AppSettings.from_env()
    settings.validate_for_startup()
    if not settings.text_embedding_model or not settings.text_embedding_api_key:
        raise RuntimeError("Recall 消融实验要求配置文本 Embedding")

    corpora = _build_corpora()
    expanded = [(case, query) for case in CASES for query in case.queries]
    client = AsyncOpenAI(base_url=settings.text_embedding_base_url, api_key=settings.text_embedding_api_key)
    query_vectors = await embed(client, settings.text_embedding_model, [query for _, query in expanded])
    strategies: dict[str, dict] = {}
    try:
        for strategy, chunks in corpora.items():
            chunk_vectors = await embed(client, settings.text_embedding_model, [chunk.text for chunk in chunks])
            rows: list[dict] = []
            for index, (case, query) in enumerate(expanded):
                ranking = hybrid_ranking(
                    chunks,
                    chunk_vectors,
                    query_vectors[index],
                    query,
                    dense_threshold=settings.hybrid_dense_min_score,
                    lexical_weight=settings.hybrid_lexical_weight,
                    dense_weight=settings.hybrid_dense_weight,
                )
                recalls = {k: _evidence_recall(chunks, ranking, case, k) for k in (1, 3, 5, 10)}
                rows.append({
                    "case_id": case.case_id,
                    "query": query,
                    "required_evidence_units": len(case.evidence_units),
                    "evidence_retention": _evidence_retention(chunks, case),
                    "recall_at_1": round(recalls[1], 4),
                    "recall_at_3": round(recalls[3], 4),
                    "recall_at_5": round(recalls[5], 4),
                    "recall_at_10": round(recalls[10], 4),
                    "top_sections": [list(chunks[item].section_path) for item in ranking[:5]],
                })
            strategies[strategy] = {
                "chunk_count": len(chunks),
                "intent_count": len(CASES),
                "query_count": len(rows),
                "multi_evidence_intents": sum(len(case.evidence_units) > 1 for case in CASES),
                "evidence_retention": round(sum(row["evidence_retention"] for row in rows) / len(rows), 4),
                **{
                    f"recall_at_{k}": round(sum(row[f"recall_at_{k}"] for row in rows) / len(rows), 4)
                    for k in (1, 3, 5, 10)
                },
                "cases": rows,
            }
    finally:
        await client.close()

    baseline = strategies["fixed_char_window"]
    candidate = strategies["structure_token"]
    return {
        "benchmark": "starboat-long-handbook-evidence-recall-v1",
        "methodology": (
            "One long enterprise handbook, evidence-unit qrels, two query phrasings per intent. "
            "The embedding model, dense threshold, lexical/dense weights and Top-K are controlled; "
            "only chunking changes."
        ),
        "intent_count": len(CASES),
        "query_count": len(expanded),
        "controlled_variables": {
            "embedding_model": settings.text_embedding_model,
            "embedding_dimension": settings.text_embedding_dimension,
            "dense_threshold": settings.hybrid_dense_min_score,
            "lexical_weight": settings.hybrid_lexical_weight,
            "dense_weight": settings.hybrid_dense_weight,
            "fixed_window_chars": 700,
            "structured_max_tokens": 700,
            "overlap": 100,
        },
        "metric_definition": (
            "Recall@K is macro-averaged evidence-unit recall: evidence units covered by the top-K chunks "
            "divided by all required evidence units for each query, then averaged across queries."
        ),
        "strategies": strategies,
        "delta_percentage_points": {
            f"recall_at_{k}": round((candidate[f"recall_at_{k}"] - baseline[f"recall_at_{k}"]) * 100, 2)
            for k in (1, 3, 5, 10)
        },
        "paired_bootstrap_95_ci_percentage_points": {
            f"recall_at_{k}": _paired_bootstrap_ci(
                baseline["cases"], candidate["cases"], f"recall_at_{k}"
            )
            for k in (1, 3, 5, 10)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="固定字符窗口与结构化切分的证据级 Recall 消融实验")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "evals" / "results" / "chunking_recall_latest.json",
    )
    args = parser.parse_args()
    result = asyncio.run(evaluate())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "benchmark": result["benchmark"],
        "intent_count": result["intent_count"],
        "query_count": result["query_count"],
        "strategies": {
            name: {key: value for key, value in metrics.items() if key != "cases"}
            for name, metrics in result["strategies"].items()
        },
        "delta_percentage_points": result["delta_percentage_points"],
        "paired_bootstrap_95_ci_percentage_points": result[
            "paired_bootstrap_95_ci_percentage_points"
        ],
        "result_file": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
