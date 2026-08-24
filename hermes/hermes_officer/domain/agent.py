from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentStrategy(StrEnum):
    AUTO = "auto"
    REACT = "react"
    PLAN_SOLVE = "plan_solve"
    WORKFLOW = "workflow"


class AgentEventType(StrEnum):
    PLAN_THOUGHT = "plan_thought"
    PLAN = "plan"
    TASK = "task"
    TOOL_THOUGHT = "tool_thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    RESULT = "result"
    AGENT_STREAM = "agent_stream"
    FILE = "file"
    BROWSER = "browser"
    ERROR = "error"


@dataclass(slots=True)
class AgentPlan:
    title: str
    steps: list[str]
    statuses: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.statuses:
            self.statuses = ["pending"] * len(self.steps)
        if not self.notes:
            self.notes = [""] * len(self.steps)
        if len(self.statuses) != len(self.steps) or len(self.notes) != len(self.steps):
            raise ValueError("计划步骤、状态和备注数量必须一致")


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_type: AgentEventType
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    is_final: bool = False


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    request_id: str
    session_id: str
    visitor_id: str
    knowledge_base_id: str = ""
    workflow: tuple[dict[str, Any], ...] = ()
    history: tuple[dict[str, str], ...] = ()
    output_format: str = "chat"
