from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class VisitorProfile:
    visitor_id: str
    username: str | None

    @property
    def named(self) -> bool:
        return bool(self.username)


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    session_id: str
    title: str
    status: str
    latest_query_text: str | None
    run_count: int
    started_at: datetime
    last_active_at: datetime


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str
    created_at: datetime
