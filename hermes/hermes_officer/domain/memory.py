from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from hermes_officer.domain.conversation import ChatMessage


def estimate_tokens(text: str) -> int:
    """Conservative tokenizer-independent estimate for budget enforcement."""
    if not text:
        return 0
    ascii_count = sum(character.isascii() for character in text)
    non_ascii_count = len(text) - ascii_count
    return non_ascii_count + max(1, (ascii_count + 3) // 4)


@dataclass(frozen=True, slots=True)
class BudgetAllocation:
    system_prompt: int
    recent_history: int
    current_run: int
    older_history: int


class ContextCompressionPolicy:
    """Priority budget: system > recent turns > current run > older history."""

    def allocate(self, total_budget: int, system_prompt: str = "") -> BudgetAllocation:
        system = min(2_000, estimate_tokens(system_prompt))
        remaining = max(0, total_budget - system)
        recent = min(8_000, int(remaining * 0.35))
        older = min(4_000, int(remaining * 0.15))
        current = max(0, remaining - recent - older)
        return BudgetAllocation(system, recent, current, older)


class TokenAwareMemoryCompactor:
    def __init__(self, preserve_recent_turns: int = 3) -> None:
        self.preserve_recent_turns = max(1, preserve_recent_turns)

    def should_compact(self, messages: list[ChatMessage], max_tokens: int) -> bool:
        return len(messages) > 3 and self.count_tokens(messages) > max_tokens

    def compact(self, messages: list[ChatMessage], max_tokens: int) -> list[ChatMessage]:
        if not self.should_compact(messages, max_tokens):
            return list(messages)

        system_messages = [item for item in messages if item.role == "system"]
        conversation = [item for item in messages if item.role != "system"]
        boundary = self._recent_boundary(conversation)
        older = conversation[:boundary]
        recent = conversation[boundary:]

        result = list(system_messages)
        if older:
            summary_budget = max(
                0,
                max_tokens - self.count_tokens(system_messages) - self.count_tokens(recent),
            )
            summary = self._fit_text(
                f"## 历史对话摘要\n{self._summarize(older)}",
                summary_budget,
            )
            if summary:
                result.append(ChatMessage(
                    role="system",
                    content=summary,
                    created_at=datetime.now(timezone.utc),
                ))
        result.extend(recent)

        first_non_system = len(system_messages)
        while self.count_tokens(result) > max_tokens and len(result) > first_non_system + 2:
            result.pop(first_non_system)
        return result

    def _recent_boundary(self, messages: list[ChatMessage]) -> int:
        turns = 0
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "user":
                turns += 1
                if turns >= self.preserve_recent_turns:
                    return index
        return 0

    @staticmethod
    def _summarize(messages: list[ChatMessage]) -> str:
        lines: list[str] = []
        for item in messages:
            content = item.content.strip().replace("\n", " ")
            if item.role == "tool":
                content = content[:160]
            else:
                content = content[:400]
            if content:
                lines.append(f"- {item.role}: {content}")
        return "\n".join(lines)[-4_000:]

    @staticmethod
    def _fit_text(text: str, token_budget: int) -> str:
        if token_budget <= 0:
            return ""
        tokens = estimate_tokens(text)
        if tokens <= token_budget:
            return text
        character_limit = max(1, int(len(text) * token_budget / tokens) - 1)
        return text[:character_limit] + "…"

    @staticmethod
    def count_tokens(messages: list[ChatMessage]) -> int:
        return sum(estimate_tokens(item.content) for item in messages)
