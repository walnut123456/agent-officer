from __future__ import annotations

import unittest
from datetime import datetime, timezone

from hermes_officer.domain.conversation import ChatMessage
from hermes_officer.domain.memory import ContextCompressionPolicy, TokenAwareMemoryCompactor


def message(role: str, content: str) -> ChatMessage:
    return ChatMessage(role, content, datetime.now(timezone.utc))


class MemoryCompactionTest(unittest.TestCase):
    def test_should_preserve_system_and_recent_turns(self) -> None:
        messages = [message("system", "不可删除的系统约束")]
        for index in range(6):
            messages.extend([
                message("user", f"问题 {index} " + "很长" * 80),
                message("assistant", f"回答 {index} " + "内容" * 80),
            ])

        compacted = TokenAwareMemoryCompactor(preserve_recent_turns=2).compact(
            messages,
            max_tokens=800,
        )

        self.assertEqual("不可删除的系统约束", compacted[0].content)
        self.assertTrue(any("历史对话摘要" in item.content for item in compacted))
        self.assertTrue(any("问题 5" in item.content for item in compacted))
        self.assertTrue(any("回答 5" in item.content for item in compacted))

    def test_should_allocate_non_negative_budget(self) -> None:
        allocation = ContextCompressionPolicy().allocate(32_000, "system")
        self.assertGreaterEqual(allocation.current_run, 0)
        self.assertLessEqual(sum((
            allocation.system_prompt,
            allocation.recent_history,
            allocation.current_run,
            allocation.older_history,
        )), 32_000)


if __name__ == "__main__":
    unittest.main()
