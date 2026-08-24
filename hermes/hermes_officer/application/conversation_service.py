from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from hermes_officer.domain.conversation import ChatMessage, ConversationSummary, VisitorProfile
from hermes_officer.domain.memory import TokenAwareMemoryCompactor
from hermes_officer.infrastructure.database import (
    ConversationRecord,
    Database,
    MessageRecord,
    VisitorRecord,
)
from hermes_officer.application.agent_runtime import AgentRuntime
from hermes_officer.domain.agent import AgentEvent, AgentEventType, AgentExecutionContext, AgentStrategy
from uuid import uuid4


class ChatResponder(Protocol):
    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]: ...


class DevelopmentResponder:
    """Safe local fallback used until an external model is configured."""

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        question = messages[-1].content if messages else ""
        text = f"Hermes 智维已收到：{question}\n\n请配置 CHAT_MODEL 后接入真实模型。"
        for index in range(0, len(text), 12):
            yield text[index:index + 12]


class LiteLLMResponder:
    def __init__(self, model: str) -> None:
        self.model = model

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        from litellm import acompletion

        response = await acompletion(
            model=self.model,
            messages=[{"role": item.role, "content": item.content} for item in messages],
            stream=True,
        )
        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content


class ConversationService:
    def __init__(
        self,
        database: Database,
        responder: ChatResponder,
        memory_compactor: TokenAwareMemoryCompactor | None = None,
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self.database = database
        self.responder = responder
        self.memory_compactor = memory_compactor or TokenAwareMemoryCompactor()
        self.agent_runtime = agent_runtime

    async def bootstrap_visitor(self, visitor_id: str, username: str | None = None) -> VisitorProfile:
        async with self.database.session() as session:
            record = await session.scalar(
                select(VisitorRecord).where(VisitorRecord.visitor_id == visitor_id)
            )
            now = datetime.now(timezone.utc)
            if record is None:
                record = VisitorRecord(visitor_id=visitor_id, username=username)
                session.add(record)
            else:
                record.last_seen_at = now
                if username:
                    record.username = username.strip()[:32]
            await session.commit()
            return VisitorProfile(record.visitor_id, record.username)

    async def name_visitor(self, visitor_id: str, username: str) -> VisitorProfile:
        normalized = username.strip()
        if not 2 <= len(normalized) <= 32:
            raise ValueError("用户名长度必须在 2 到 32 个字符之间")
        return await self.bootstrap_visitor(visitor_id, normalized)

    async def create_conversation(
        self,
        visitor_id: str,
        session_id: str,
        title: str = "新对话",
    ) -> ConversationSummary:
        await self.bootstrap_visitor(visitor_id)
        async with self.database.session() as session:
            record = await session.scalar(
                select(ConversationRecord).where(ConversationRecord.session_id == session_id)
            )
            if record is not None and record.visitor_id != visitor_id:
                raise PermissionError("无权访问该会话")
            if record is None:
                record = ConversationRecord(
                    session_id=session_id,
                    visitor_id=visitor_id,
                    title=(title.strip() or "新对话")[:128],
                )
                session.add(record)
                await session.commit()
            return self._summary(record)

    async def list_conversations(self, visitor_id: str, limit: int = 20) -> list[ConversationSummary]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(ConversationRecord)
                .where(ConversationRecord.visitor_id == visitor_id)
                .order_by(ConversationRecord.last_active_at.desc())
                .limit(max(1, min(limit, 100)))
            )
            return [self._summary(item) for item in rows]

    async def history(self, visitor_id: str, session_id: str) -> list[ChatMessage]:
        async with self.database.session() as session:
            record = await session.scalar(
                select(ConversationRecord)
                .options(selectinload(ConversationRecord.messages))
                .where(ConversationRecord.session_id == session_id)
            )
            self._ensure_owner(record, visitor_id)
            return [ChatMessage(item.role, item.content, item.created_at) for item in record.messages]

    async def stream_reply(
        self,
        visitor_id: str,
        session_id: str,
        content: str,
    ) -> AsyncIterator[str]:
        normalized = content.strip()
        if not normalized:
            raise ValueError("消息不能为空")
        await self.create_conversation(visitor_id, session_id, normalized[:40])
        await self._append_message(session_id, "user", normalized)
        history = self.memory_compactor.compact(
            await self.history(visitor_id, session_id),
            max_tokens=16_000,
        )
        chunks: list[str] = []
        async for chunk in self.responder.stream(history):
            chunks.append(chunk)
            yield chunk
        await self._append_message(session_id, "assistant", "".join(chunks))

    async def stream_agent_reply(
        self,
        visitor_id: str,
        session_id: str,
        content: str,
        *,
        strategy: AgentStrategy = AgentStrategy.AUTO,
        knowledge_base_id: str = "",
        workflow: tuple[dict, ...] = (),
        output_format: str = "chat",
    ) -> AsyncIterator[AgentEvent]:
        if self.agent_runtime is None:
            async for chunk in self.stream_reply(visitor_id, session_id, content):
                yield AgentEvent(AgentEventType.AGENT_STREAM, chunk)
            yield AgentEvent(AgentEventType.RESULT, "", is_final=True)
            return
        normalized = content.strip()
        if not normalized:
            raise ValueError("消息不能为空")
        await self.create_conversation(visitor_id, session_id, normalized[:40])
        await self._append_message(session_id, "user", normalized)
        compacted = self.memory_compactor.compact(
            await self.history(visitor_id, session_id),
            max_tokens=16_000,
        )
        prior_messages = tuple(
            {"role": item.role, "content": item.content}
            for item in compacted[:-1]
            if item.role in {"user", "assistant", "system"}
        )
        context = AgentExecutionContext(
            request_id=uuid4().hex,
            session_id=session_id,
            visitor_id=visitor_id,
            knowledge_base_id=knowledge_base_id,
            workflow=workflow,
            history=prior_messages,
            output_format=output_format,
        )
        answer_chunks: list[str] = []
        async for event in self.agent_runtime.run(normalized, context, strategy):
            if event.event_type == AgentEventType.AGENT_STREAM:
                answer_chunks.append(event.content)
            elif event.event_type == AgentEventType.RESULT and event.content and not event.data.get("intermediate"):
                answer_chunks.append(event.content)
            elif event.event_type == AgentEventType.ERROR and not answer_chunks:
                answer_chunks.append(f"执行失败：{event.content}")
            yield event
        answer = "".join(answer_chunks).strip() or "任务已完成。"
        await self._append_message(session_id, "assistant", answer)

    async def _append_message(self, session_id: str, role: str, content: str) -> None:
        async with self.database.session() as session:
            conversation = await session.scalar(
                select(ConversationRecord).where(ConversationRecord.session_id == session_id)
            )
            if conversation is None:
                raise LookupError("会话不存在")
            session.add(MessageRecord(session_id=session_id, role=role, content=content))
            conversation.latest_query_text = content if role == "user" else conversation.latest_query_text
            conversation.run_count += 1 if role == "assistant" else 0
            conversation.last_active_at = datetime.now(timezone.utc)
            conversation.status = "SUCCESS" if role == "assistant" else "RUNNING"
            await session.commit()

    @staticmethod
    def _ensure_owner(record: ConversationRecord | None, visitor_id: str) -> None:
        if record is None:
            raise LookupError("会话不存在")
        if record.visitor_id != visitor_id:
            raise PermissionError("无权访问该会话")

    @staticmethod
    def _summary(record: ConversationRecord) -> ConversationSummary:
        return ConversationSummary(
            session_id=record.session_id,
            title=record.title,
            status=record.status,
            latest_query_text=record.latest_query_text,
            run_count=record.run_count,
            started_at=record.started_at,
            last_active_at=record.last_active_at,
        )
