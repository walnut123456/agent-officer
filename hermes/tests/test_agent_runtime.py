from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_officer.application.agent_runtime import (
    AgentRuntime,
    ModelToolCall,
    ModelTurn,
    ToolDefinition,
    ToolRegistry,
)
from hermes_officer.domain.agent import AgentEventType, AgentExecutionContext, AgentPlan, AgentStrategy
from hermes_officer.infrastructure.database import Database


class FakeModel:
    def __init__(self) -> None:
        self.turn = 0

    async def decide(self, messages, tools):
        self.turn += 1
        if self.turn == 1:
            return ModelTurn("先查询资料", (ModelToolCall("call-1", "echo", {"text": "evidence"}),))
        return ModelTurn("依据工具结果完成回答")

    async def create_plan(self, task):
        return AgentPlan("测试计划", ["第一步", "第二步"])

    async def stream_answer(self, messages):
        yield "综合"
        yield "结论"


class RepeatingToolModel:
    async def decide(self, messages, tools):
        return ModelTurn("继续调用相同工具", (ModelToolCall("repeat", "echo", {"text": "same"}),))

    async def create_plan(self, task):
        return AgentPlan("不会使用", [])

    async def stream_answer(self, messages):
        yield "不会使用"


class AgentRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-agent-")
        path = Path(self.temp.name) / "agent.db"
        self.database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
        await self.database.initialize()
        registry = ToolRegistry(self.database)

        async def echo(arguments, context):
            return {"echo": arguments["text"], "visitor": context.visitor_id}

        registry.register(ToolDefinition(
            "echo",
            "echo",
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            echo,
        ))
        self.registry = registry

    async def asyncTearDown(self) -> None:
        await self.database.dispose()
        self.temp.cleanup()

    async def test_react_should_emit_tool_protocol_and_final_result(self) -> None:
        runtime = AgentRuntime(FakeModel(), self.registry)
        context = AgentExecutionContext("request-1", "session-1", "visitor-1")
        events = [item async for item in runtime.run("执行任务", context, AgentStrategy.REACT)]
        self.assertEqual(
            [AgentEventType.TOOL_THOUGHT, AgentEventType.TOOL_CALL, AgentEventType.TOOL_RESULT, AgentEventType.TOOL_THOUGHT, AgentEventType.RESULT],
            [item.event_type for item in events],
        )
        self.assertTrue(events[-1].is_final)

    async def test_plan_solve_should_publish_plan_tasks_and_streamed_result(self) -> None:
        runtime = AgentRuntime(FakeModel(), self.registry)
        context = AgentExecutionContext("request-2", "session-2", "visitor-2")
        events = [item async for item in runtime.run("分析复杂问题", context, AgentStrategy.PLAN_SOLVE)]
        event_types = [item.event_type for item in events]
        self.assertIn(AgentEventType.PLAN, event_types)
        self.assertEqual(2, event_types.count(AgentEventType.TASK))
        self.assertIn(AgentEventType.AGENT_STREAM, event_types)
        self.assertEqual(AgentEventType.RESULT, event_types[-1])

    async def test_workflow_should_execute_configured_tool_nodes(self) -> None:
        runtime = AgentRuntime(FakeModel(), self.registry)
        context = AgentExecutionContext(
            "request-3",
            "session-3",
            "visitor-3",
            workflow=({"label": "回显输入", "tool": "echo", "arguments": {"text": "{{query}}"}},),
        )
        events = [item async for item in runtime.run("工作流输入", context, AgentStrategy.WORKFLOW)]
        event_types = [item.event_type for item in events]
        self.assertIn(AgentEventType.TOOL_CALL, event_types)
        self.assertIn(AgentEventType.TOOL_RESULT, event_types)
        self.assertIn("工作流输入", next(item.content for item in events if item.event_type == AgentEventType.TOOL_RESULT))

    async def test_react_should_break_repeated_tool_call_cycle_by_fingerprint(self) -> None:
        runtime = AgentRuntime(RepeatingToolModel(), self.registry, max_steps=8, max_identical_tool_calls=2)
        context = AgentExecutionContext("request-repeat", "session-repeat", "visitor-repeat")
        events = [item async for item in runtime.run("重复调用测试", context, AgentStrategy.REACT)]
        tool_calls = [item for item in events if item.event_type == AgentEventType.TOOL_CALL]
        tool_results = [item for item in events if item.event_type == AgentEventType.TOOL_RESULT]
        self.assertEqual(2, len(tool_calls))
        self.assertEqual(2, len(tool_results))
        self.assertEqual(tool_calls[0].data["fingerprint"], tool_calls[1].data["fingerprint"])
        self.assertEqual(AgentEventType.ERROR, events[-1].event_type)
        self.assertIn("重复工具调用", events[-1].content)
