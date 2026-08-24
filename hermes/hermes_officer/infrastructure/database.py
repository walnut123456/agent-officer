from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, Float, JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event, select
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class VisitorRecord(Base):
    __tablename__ = "visitor_identity"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    visitor_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationRecord(Base):
    __tablename__ = "dialogue_session"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    latest_query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    messages: Mapped[list["MessageRecord"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="MessageRecord.id",
    )


class MessageRecord(Base):
    __tablename__ = "dialogue_message"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("dialogue_session.session_id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    conversation: Mapped[ConversationRecord] = relationship(back_populates="messages")


class ResourceRecord(Base):
    """Versioned configuration replacing the duplicated Java admin tables."""

    __tablename__ = "resource_config"
    __table_args__ = (UniqueConstraint("resource_type", "resource_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    resource_type: Mapped[str] = mapped_column(String(32), index=True)
    resource_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[int] = mapped_column(Integer, default=1, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryNoteRecord(Base):
    __tablename__ = "session_memory_note"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    note_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    vector_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ToolRunRecord(Base):
    __tablename__ = "tool_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentEvalRunRecord(Base):
    """One immutable Agent evaluation batch and its release-gate summary."""

    __tablename__ = "agent_eval_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gate: Mapped[str] = mapped_column(String(16), index=True)
    overall_score: Mapped[float] = mapped_column(Float)
    passed_count: Mapped[int] = mapped_column(Integer)
    case_count: Mapped[int] = mapped_column(Integer)
    category_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    category_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    latency_summaries: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_gaps: Mapped[list] = mapped_column(JSON, default=list)
    hard_blockers: Mapped[list] = mapped_column(JSON, default=list)
    metadata_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    cases: Mapped[list["AgentEvalCaseRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentEvalCaseRecord.id",
    )


class AgentEvalCaseRecord(Base):
    """Machine-readable result for one case inside an Agent evaluation batch."""

    __tablename__ = "agent_eval_case"
    __table_args__ = (UniqueConstraint("run_id", "case_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_eval_run.run_id", ondelete="CASCADE"),
        index=True,
    )
    case_id: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    passed: Mapped[bool] = mapped_column(Boolean, index=True)
    score: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    run: Mapped[AgentEvalRunRecord] = relationship(back_populates="cases")


class KnowledgeBaseRecord(Base):
    """User-facing knowledge-base metadata for the Python MRAG workspace."""

    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    chunk_size: Mapped[int] = mapped_column(Integer, default=800)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=80)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    documents: Mapped[list["KnowledgeDocumentRecord"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )


class KnowledgeDocumentRecord(Base):
    """Canonical source and chunks; no external vector database is required."""

    __tablename__ = "knowledge_document"
    __table_args__ = (UniqueConstraint("kb_id", "document_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    kb_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base.kb_id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(16), default="file")
    source_url: Mapped[str] = mapped_column(Text, default="")
    stored_path: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(128), default="text/plain")
    file_ext: Mapped[str] = mapped_column(String(24), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="PROCESSING", index=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    canonical_content: Mapped[str] = mapped_column(Text, default="")
    chunks: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    knowledge_base: Mapped[KnowledgeBaseRecord] = relationship(back_populates="documents")


class ImageReferenceRecord(Base):
    __tablename__ = "image_reference"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reference_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(128))
    file_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DatasetRecord(Base):
    __tablename__ = "workspace_dataset"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    file_ext: Mapped[str] = mapped_column(String(16))
    file_size: Mapped[int] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer)
    columns: Mapped[list] = mapped_column(JSON, default=list)
    preview: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FileInfoRecord(Base):
    """File-service metadata stored in the primary database.

    The binary payload remains in the configured file storage directory; only
    searchable business metadata belongs in the relational database.
    """

    __tablename__ = "file_info"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[int] = mapped_column(Integer, default=0, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite") and "///" in url:
            database_path = url.split("///", 1)[1]
            if database_path and database_path != ":memory:":
                Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        is_mysql = url.startswith("mysql")
        # aiomysql 0.3.x exposes an async ping(reconnect) signature that is not
        # compatible with SQLAlchemy's generic pre-ping adapter. The explicit
        # healthcheck below remains the source of truth for DB readiness.
        engine_options = {"pool_pre_ping": not is_mysql}
        if is_mysql:
            engine_options["pool_recycle"] = 1800
        self.engine = create_async_engine(url, **engine_options)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine.sync_engine, "connect")
            def _configure_sqlite(dbapi_connection, _connection_record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.sessions()

    async def healthcheck(self) -> None:
        async with self.session() as session:
            await session.execute(select(1))
