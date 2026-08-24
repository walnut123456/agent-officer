from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select

from hermes_officer.domain.agent import (
    AgentEvent,
    AgentEventType,
    AgentExecutionContext,
    AgentPlan,
    AgentStrategy,
)
from hermes_officer.infrastructure.database import Database, ToolRunRecord


ToolHandler = Callable[[dict[str, Any], AgentExecutionContext], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelTurn:
    content: str
    tool_calls: tuple[ModelToolCall, ...] = ()


class AgentModel(Protocol):
    async def decide(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn: ...
    async def create_plan(self, task: str) -> AgentPlan: ...
    async def stream_answer(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]: ...


class DevelopmentAgentModel:
    """Offline model adapter: deterministic and explicit, useful for local setup/tests."""

    async def decide(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        del tools
        question = str(messages[-1].get("content", "")) if messages else ""
        return ModelTurn(f"Hermes 智维已收到：{question}\n\n配置 `CHAT_MODEL` 后可启用真实的工具决策与多步推理。")

    async def create_plan(self, task: str) -> AgentPlan:
        return AgentPlan(
            title="任务执行计划",
            steps=["理解目标与约束", "收集并处理必要信息", "汇总结论并检查结果"],
        )

    async def stream_answer(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        content = str(messages[-1].get("content", "")) if messages else ""
        text = f"已完成本地执行框架验证。配置 `CHAT_MODEL` 后将综合以下执行结果：\n\n{content[-1200:]}"
        for index in range(0, len(text), 24):
            yield text[index:index + 24]


class LiteLLMAgentModel:
    def __init__(self, model: str) -> None:
        self.model = model

    async def decide(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        from litellm import acompletion

        response = await acompletion(
            model=self.model,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
            stream=False,
        )
        message = response.choices[0].message
        calls: list[ModelToolCall] = []
        for call in getattr(message, "tool_calls", None) or []:
            raw_arguments = getattr(call.function, "arguments", "{}") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            calls.append(ModelToolCall(
                call_id=getattr(call, "id", None) or uuid4().hex,
                name=call.function.name,
                arguments=arguments,
            ))
        return ModelTurn(getattr(message, "content", None) or "", tuple(calls))

    async def create_plan(self, task: str) -> AgentPlan:
        from litellm import acompletion

        response = await acompletion(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "把任务拆成 2-6 个可执行步骤。只返回 JSON：{\"title\": string, \"steps\": [string]}。",
                },
                {"role": "user", "content": task},
            ],
            stream=False,
        )
        raw = response.choices[0].message.content or ""
        match = re.search(r"\{[\s\S]*\}", raw)
        try:
            payload = json.loads(match.group(0) if match else raw)
            steps = [str(item).strip() for item in payload.get("steps", []) if str(item).strip()]
        except (AttributeError, TypeError, json.JSONDecodeError):
            steps = []
            payload = {}
        if not steps:
            steps = ["分析任务", "执行任务", "验证并总结"]
        return AgentPlan(str(payload.get("title") or "任务执行计划"), steps[:6])

    async def stream_answer(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        from litellm import acompletion

        response = await acompletion(model=self.model, messages=messages, stream=True)
        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content


class AgentRouter:
    COMPLEX_KEYWORDS = {
        "分析", "比较", "对比", "调研", "研究", "规划", "报告", "总结", "评估",
        "方案", "设计", "优化", "架构", "路线图", "analyze", "compare", "research",
        "report", "strategy", "roadmap", "architecture", "refactor",
    }

    @classmethod
    def decide(cls, query: str) -> AgentStrategy:
        normalized = query.strip().lower()
        if len(re.sub(r"\s+", "", normalized)) < 15:
            return AgentStrategy.REACT
        if any(keyword in normalized for keyword in cls.COMPLEX_KEYWORDS):
            return AgentStrategy.PLAN_SOLVE
        return AgentStrategy.REACT


class ToolRegistry:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"工具已注册：{definition.name}")
        self._tools[definition.name] = definition

    def schemas(self) -> list[dict[str, Any]]:
        return [item.openai_schema() for item in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any], context: AgentExecutionContext) -> Any:
        definition = self._tools.get(name)
        if definition is None:
            raise LookupError(f"未知工具：{name}")
        run = ToolRunRecord(
            request_id=context.request_id,
            session_id=context.session_id,
            tool_name=name,
            input_payload=arguments,
            status="RUNNING",
        )
        async with self.database.session() as session:
            session.add(run)
            await session.commit()
            await session.refresh(run)
            run_id = run.id
        try:
            result = await definition.handler(arguments, context)
            output = result if isinstance(result, dict) else {"result": result}
            await self._finish(run_id, "SUCCESS", output=output)
            return result
        except Exception as exc:
            await self._finish(run_id, "FAILED", error=str(exc))
            raise

    async def _finish(self, run_id: int, status: str, *, output: dict | None = None, error: str = "") -> None:
        async with self.database.session() as session:
            record = await session.scalar(select(ToolRunRecord).where(ToolRunRecord.id == run_id))
            if record:
                record.status = status
                record.output_payload = output or {}
                record.error_message = error or None
                record.finished_at = datetime.now(timezone.utc)
                await session.commit()


class AgentRuntime:
    def __init__(
        self,
        model: AgentModel,
        tools: ToolRegistry,
        *,
        max_steps: int = 8,
        max_identical_tool_calls: int = 2,
    ) -> None:
        self.model = model
        self.tools = tools
        self.max_steps = max_steps
        self.max_identical_tool_calls = max(1, max_identical_tool_calls)

    @staticmethod
    def tool_call_fingerprint(name: str, arguments: dict[str, Any]) -> str:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(f"{name}:{canonical}".encode("utf-8")).hexdigest()[:16]

    async def run(
        self,
        query: str,
        context: AgentExecutionContext,
        strategy: AgentStrategy = AgentStrategy.AUTO,
    ) -> AsyncIterator[AgentEvent]:
        selected = AgentRouter.decide(query) if strategy == AgentStrategy.AUTO else strategy
        try:
            if selected == AgentStrategy.PLAN_SOLVE:
                async for event in self._run_plan_solve(query, context):
                    yield event
            elif selected == AgentStrategy.WORKFLOW:
                async for event in self._run_workflow(query, context):
                    yield event
            else:
                async for event in self._run_react(query, context):
                    yield event
        except Exception as exc:
            yield AgentEvent(AgentEventType.ERROR, str(exc), is_final=True)

    async def _run_react(
        self,
        query: str,
        context: AgentExecutionContext,
        *,
        emit_final: bool = True,
    ) -> AsyncIterator[AgentEvent]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是 Hermes 执行智能体。需要外部信息时调用工具；拿到观察结果后继续推理，直到给出可靠答案。"
                    f"用户要求的输出形态是 {context.output_format}。当输出形态为 html、docs 或 ppt 时，最终应调用 report_tool 生成文件。"
                ),
            },
        ]
        messages.extend(context.history)
        messages.append({"role": "user", "content": query})
        repeated_calls: Counter[str] = Counter()
        for _ in range(self.max_steps):
            turn = await self.model.decide(messages, self.tools.schemas())
            if turn.content:
                yield AgentEvent(AgentEventType.TOOL_THOUGHT, turn.content)
            if not turn.tool_calls:
                if emit_final:
                    yield AgentEvent(AgentEventType.RESULT, turn.content, is_final=True)
                else:
                    yield AgentEvent(AgentEventType.RESULT, turn.content, {"intermediate": True})
                return
            messages.append({
                "role": "assistant",
                "content": turn.content or None,
                "tool_calls": [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                    }
                    for call in turn.tool_calls
                ],
            })
            for call in turn.tool_calls:
                fingerprint = self.tool_call_fingerprint(call.name, call.arguments)
                repeated_calls[fingerprint] += 1
                if repeated_calls[fingerprint] > self.max_identical_tool_calls:
                    raise RuntimeError(
                        "检测到重复工具调用，已熔断以避免死循环："
                        f"tool={call.name}, fingerprint={fingerprint}"
                    )
                yield AgentEvent(
                    AgentEventType.TOOL_CALL,
                    call.name,
                    {
                        "tool_call_id": call.call_id,
                        "tool_name": call.name,
                        "arguments": call.arguments,
                        "fingerprint": fingerprint,
                    },
                )
                try:
                    result = await self.tools.execute(call.name, call.arguments, context)
                    serialized = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
                    if isinstance(result, dict) and result.get("fileInfo"):
                        yield AgentEvent(
                            AgentEventType.FILE,
                            data={"tool_call_id": call.call_id, "files": result.get("fileInfo", [])},
                        )
                    yield AgentEvent(
                        AgentEventType.TOOL_RESULT,
                        serialized,
                        {"tool_call_id": call.call_id, "tool_name": call.name},
                    )
                except Exception as exc:
                    serialized = f"工具执行失败：{exc}"
                    yield AgentEvent(
                        AgentEventType.TOOL_RESULT,
                        serialized,
                        {"tool_call_id": call.call_id, "tool_name": call.name, "failed": True},
                    )
                messages.append({"role": "tool", "tool_call_id": call.call_id, "content": serialized})
        yield AgentEvent(AgentEventType.ERROR, f"智能体超过最大执行步数 {self.max_steps}", is_final=True)

    async def _run_plan_solve(self, query: str, context: AgentExecutionContext) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(AgentEventType.PLAN_THOUGHT, "正在拆解目标、依赖与验收条件……")
        plan = await self.model.create_plan(query)
        yield AgentEvent(AgentEventType.PLAN, data={"plan": asdict(plan)})
        observations: list[str] = []
        for index, task in enumerate(plan.steps):
            plan.statuses[index] = "running"
            yield AgentEvent(AgentEventType.PLAN, data={"plan": asdict(plan)})
            yield AgentEvent(AgentEventType.TASK, task, {"step": index + 1})
            step_result = ""
            async for event in self._run_react(f"总目标：{query}\n当前步骤：{task}", context, emit_final=False):
                yield event
                if event.event_type == AgentEventType.RESULT:
                    step_result = event.content
            observations.append(f"步骤 {index + 1}（{task}）：{step_result}")
            plan.statuses[index] = "completed"
            plan.notes[index] = step_result[:240]
            yield AgentEvent(AgentEventType.PLAN, data={"plan": asdict(plan)})
        synthesis_messages = [
            {"role": "system", "content": "根据任务目标和各步骤执行结果，给出完整结论；不要声称做过未执行的事情。"},
            {"role": "user", "content": f"目标：{query}\n\n执行记录：\n" + "\n".join(observations)},
        ]
        async for chunk in self.model.stream_answer(synthesis_messages):
            yield AgentEvent(AgentEventType.AGENT_STREAM, chunk)
        yield AgentEvent(AgentEventType.RESULT, "", is_final=True)

    async def _run_workflow(self, query: str, context: AgentExecutionContext) -> AsyncIterator[AgentEvent]:
        if not context.workflow:
            async for event in self._run_react(query, context):
                yield event
            return
        results: list[str] = []
        plan = AgentPlan("工作流", [str(node.get("label") or node.get("tool") or "步骤") for node in context.workflow])
        yield AgentEvent(AgentEventType.PLAN, data={"plan": asdict(plan)})
        for index, node in enumerate(context.workflow):
            tool_name = str(node.get("tool", ""))
            arguments = dict(node.get("arguments") or {})
            arguments = {key: (query if value == "{{query}}" else value) for key, value in arguments.items()}
            yield AgentEvent(AgentEventType.TASK, plan.steps[index], {"step": index + 1})
            yield AgentEvent(AgentEventType.TOOL_CALL, tool_name, {"tool_name": tool_name, "arguments": arguments})
            result = await self.tools.execute(tool_name, arguments, context)
            serialized = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            results.append(serialized)
            plan.statuses[index] = "completed"
            plan.notes[index] = serialized[:240]
            yield AgentEvent(AgentEventType.TOOL_RESULT, serialized, {"tool_name": tool_name})
            yield AgentEvent(AgentEventType.PLAN, data={"plan": asdict(plan)})
        async for chunk in self.model.stream_answer([
            {"role": "system", "content": "总结工作流执行结果。"},
            {"role": "user", "content": f"目标：{query}\n\n结果：\n" + "\n".join(results)},
        ]):
            yield AgentEvent(AgentEventType.AGENT_STREAM, chunk)
        yield AgentEvent(AgentEventType.RESULT, "", is_final=True)
