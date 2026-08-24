from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hermes_officer.application.agent_runtime import (
    AgentRuntime,
    ModelToolCall,
    ModelTurn,
    ToolDefinition,
    ToolRegistry,
)
from hermes_officer.domain.agent import AgentEventType, AgentExecutionContext, AgentPlan, AgentStrategy
from hermes_officer.infrastructure.database import Database


class ExactLoopModel:
    def __init__(self, seed: int) -> None:
        self.turn = 0
        self.seed = seed

    async def decide(self, messages, tools):
        del messages, tools
        self.turn += 1
        filters = {"tenant": f"tenant-{self.seed % 11}", "active": self.seed % 2 == 0}
        arguments = {
            "scope": f"device-{self.seed % 7}",
            "query": f"E{self.seed % 37:02d}",
            "filters": filters,
        }
        if self.turn % 2 == 0:
            arguments = {
                "filters": {"active": self.seed % 2 == 0, "tenant": f"tenant-{self.seed % 11}"},
                "query": f"E{self.seed % 37:02d}",
                "scope": f"device-{self.seed % 7}",
            }
        return ModelTurn("继续查询", (ModelToolCall(f"loop-{self.turn}", "echo", arguments),))

    async def create_plan(self, task):
        return AgentPlan(str(task), [])

    async def stream_answer(self, messages):
        if False:
            yield str(messages)


class UniqueThenAnswerModel:
    def __init__(self, seed: int) -> None:
        self.turn = 0
        self.seed = seed
        self.calls = 2 + seed % 5

    async def decide(self, messages, tools):
        del messages, tools
        self.turn += 1
        if self.turn <= self.calls:
            return ModelTurn(
                "查询不同设备",
                (ModelToolCall(
                    f"unique-{self.seed}-{self.turn}",
                    "echo",
                    {"query": f"case-{self.seed}-step-{self.turn}", "limit": 1 + self.turn % 5},
                ),),
            )
        return ModelTurn("已完成不同参数的合法查询")

    async def create_plan(self, task):
        return AgentPlan(str(task), [])

    async def stream_answer(self, messages):
        yield str(messages)


class FailureThenRecoverModel:
    def __init__(self, seed: int) -> None:
        self.turn = 0
        self.seed = seed

    async def decide(self, messages, tools):
        del tools
        self.turn += 1
        if self.turn == 1:
            return ModelTurn(
                "调用故障工具",
                (ModelToolCall(f"failure-{self.seed}", "fail", {"query": f"probe-{self.seed}"}),),
            )
        observed = any(
            message.get("role") == "tool" and "工具执行失败" in str(message.get("content"))
            for message in messages
        )
        return ModelTurn("识别到工具异常并安全结束" if observed else "未识别异常")

    async def create_plan(self, task):
        return AgentPlan(str(task), [])

    async def stream_answer(self, messages):
        yield str(messages)


class NonConvergingUniqueModel:
    def __init__(self, seed: int) -> None:
        self.turn = 0
        self.seed = seed

    async def decide(self, messages, tools):
        del messages, tools
        self.turn += 1
        return ModelTurn(
            "持续调用不同参数",
            (ModelToolCall(
                f"budget-{self.seed}-{self.turn}",
                "echo",
                {"query": f"case-{self.seed}-step-{self.turn}"},
            ),),
        )

    async def create_plan(self, task):
        return AgentPlan(str(task), [])

    async def stream_answer(self, messages):
        if False:
            yield str(messages)


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(len(ordered) * quantile + 0.999999) - 1))
    return ordered[index]


async def evaluate(cases_per_scenario: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="hermes-reliability-eval-") as directory:
        database = Database(f"sqlite+aiosqlite:///{(Path(directory) / 'eval.db').as_posix()}")
        await database.initialize()
        registry = ToolRegistry(database)

        async def echo(arguments, context):
            del context
            return {"echo": arguments}

        async def fail(arguments, context):
            del arguments, context
            raise TimeoutError("injected tool timeout")

        registry.register(ToolDefinition(
            "echo",
            "deterministic echo",
            {"type": "object", "additionalProperties": True},
            echo,
        ))
        registry.register(ToolDefinition(
            "fail",
            "deterministic failure",
            {"type": "object", "additionalProperties": True},
            fail,
        ))

        counters = {
            "loop_intercepted": 0,
            "legitimate_completed": 0,
            "failure_contained": 0,
            "budget_enforced": 0,
        }
        latencies: dict[str, list[float]] = {key: [] for key in counters}
        duplicate_side_effect_counts: list[int] = []

        scenarios = (
            ("loop_intercepted", ExactLoopModel),
            ("legitimate_completed", UniqueThenAnswerModel),
            ("failure_contained", FailureThenRecoverModel),
            ("budget_enforced", NonConvergingUniqueModel),
        )
        try:
            for metric, model_factory in scenarios:
                for case_index in range(cases_per_scenario):
                    model = model_factory(case_index)
                    runtime = AgentRuntime(
                        model,
                        registry,
                        max_steps=8,
                        max_identical_tool_calls=2,
                    )
                    context = AgentExecutionContext(
                        f"reliability-{metric}-{case_index}",
                        f"session-{metric}-{case_index}",
                        "eval-visitor",
                    )
                    started = time.perf_counter()
                    events = [
                        event
                        async for event in runtime.run("故障注入", context, AgentStrategy.REACT)
                    ]
                    latencies[metric].append((time.perf_counter() - started) * 1000)
                    tool_calls = [event for event in events if event.event_type == AgentEventType.TOOL_CALL]
                    tool_results = [event for event in events if event.event_type == AgentEventType.TOOL_RESULT]
                    final = events[-1]
                    passed = False
                    if metric == "loop_intercepted":
                        duplicate_side_effect_counts.append(len(tool_results))
                        passed = (
                            len(tool_calls) == 2
                            and len(tool_results) == 2
                            and final.event_type == AgentEventType.ERROR
                            and "重复工具调用" in final.content
                            and len({event.data.get("fingerprint") for event in tool_calls}) == 1
                        )
                    elif metric == "legitimate_completed":
                        passed = len(tool_calls) == model.calls and final.event_type == AgentEventType.RESULT
                    elif metric == "failure_contained":
                        passed = (
                            len(tool_results) == 1
                            and bool(tool_results[0].data.get("failed"))
                            and final.event_type == AgentEventType.RESULT
                            and "安全结束" in final.content
                        )
                    elif metric == "budget_enforced":
                        passed = (
                            len(tool_calls) == 8
                            and final.event_type == AgentEventType.ERROR
                            and "最大执行步数 8" in final.content
                        )
                    counters[metric] += int(passed)
        finally:
            await database.dispose()

    fingerprint_samples = 0
    fingerprint_mismatches = 0
    base_items = [("query", "E23"), ("scope", "device"), ("limit", 5), ("tenant", "A")]
    expected = AgentRuntime.tool_call_fingerprint("knowledge_search", dict(base_items))
    for permutation in itertools.permutations(base_items):
        fingerprint_samples += 1
        fingerprint_mismatches += int(
            AgentRuntime.tool_call_fingerprint("knowledge_search", dict(permutation)) != expected
        )

    total = cases_per_scenario * len(counters)
    return {
        "benchmark_type": "deterministic fault-injection replay",
        "case_count": total,
        "cases_per_scenario": cases_per_scenario,
        "metrics": {
            "loop_interception_rate": round(counters["loop_intercepted"] / cases_per_scenario, 4),
            "legitimate_call_completion_rate": round(counters["legitimate_completed"] / cases_per_scenario, 4),
            "false_breaker_rate": round(1 - counters["legitimate_completed"] / cases_per_scenario, 4),
            "tool_failure_containment_rate": round(counters["failure_contained"] / cases_per_scenario, 4),
            "max_step_enforcement_rate": round(counters["budget_enforced"] / cases_per_scenario, 4),
            "max_duplicate_side_effects_per_trace": max(duplicate_side_effect_counts, default=0),
            "fingerprint_order_invariance_rate": round(
                1 - fingerprint_mismatches / max(fingerprint_samples, 1), 4
            ),
        },
        "latency_ms": {
            metric: {
                "p50": round(statistics.median(values), 2),
                "p95": round(percentile(values, 0.95), 2),
            }
            for metric, values in latencies.items()
        },
        "fingerprint_permutations": fingerprint_samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent运行时确定性故障注入评测")
    parser.add_argument("--cases-per-scenario", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "evals" / "results" / "agent_reliability_latest.json",
    )
    args = parser.parse_args()
    if args.cases_per_scenario < 1:
        raise ValueError("cases-per-scenario 必须大于0")
    report = asyncio.run(evaluate(args.cases_per_scenario))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"result_file={args.output}")


if __name__ == "__main__":
    main()
