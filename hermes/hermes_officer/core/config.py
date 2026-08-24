from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float) -> float:
    return float(value.strip()) if value and value.strip() else default


def _as_csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    return tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())


def _chat_model_from_env() -> str | None:
    """Prefer the unified setting while accepting the legacy Python tool config."""
    model = (os.getenv("CHAT_MODEL") or os.getenv("DEFAULT_MODEL") or os.getenv("LLM_MODEL_NAME") or "").strip()
    if not model:
        return None
    if model.startswith("${") and model.endswith("}"):
        model = (os.getenv(model[2:-1]) or "").strip()
    if model and "/" not in model and os.getenv("OPENAI_BASE_URL"):
        return f"openai/{model}"
    return model or None


def _storage_path_from_env(name: str, default: str) -> Path:
    path = Path(os.getenv(name, default)).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _database_url_from_env() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        mysql_database = os.getenv("MYSQL_DATABASE", "").strip()
        if mysql_database:
            mysql_host = os.getenv("MYSQL_HOST", "127.0.0.1").strip()
            mysql_port = int(os.getenv("MYSQL_PORT", "3306"))
            mysql_user = os.getenv("MYSQL_USER", "").strip()
            mysql_password = os.getenv("MYSQL_PASSWORD", "")
            if not mysql_user:
                raise RuntimeError("配置 MYSQL_DATABASE 时必须同时配置 MYSQL_USER")
            url = (
                "mysql+aiomysql://"
                f"{quote_plus(mysql_user)}:{quote_plus(mysql_password)}@"
                f"{mysql_host}:{mysql_port}/{quote_plus(mysql_database)}?charset=utf8mb4"
            )
        else:
            url = "sqlite+aiosqlite:///data/hermes.db"
    if not url.startswith("sqlite") or "///" not in url:
        return url
    prefix, database_path = url.split("///", 1)
    if not database_path or database_path == ":memory:":
        return url
    path = Path(database_path).expanduser()
    if path.is_absolute():
        return url
    absolute_path = (PROJECT_ROOT / path).resolve().as_posix()
    return f"{prefix}///{absolute_path}"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Typed process-level configuration for bootstrap and operations."""

    name: str = "Hermes 智维 · 企业智能工作台"
    version: str = "0.3.0"
    environment: str = "local"
    host: str = "0.0.0.0"
    port: int = 1601
    workers: int = 1
    log_path: Path = Path("logs/server.log")
    log_level: str = "INFO"
    log_file_enabled: bool = True
    cors_origins: tuple[str, ...] = ("http://localhost:1601", "http://127.0.0.1:1601")
    cors_allow_credentials: bool = True
    docs_enabled: bool = True
    mcp_enabled: bool = True
    show_banner: bool = True
    database_url: str = "sqlite+aiosqlite:///data/hermes.db"
    knowledge_storage_path: Path = Path("data/knowledge")
    image_storage_path: Path = Path("data/images")
    dataset_storage_path: Path = Path("data/datasets")
    ui_enabled: bool = True
    session_secret: str = "change-me-in-production"
    chat_model: str | None = None
    admin_api_key: str | None = None
    scheduler_enabled: bool = True
    knowledge_hybrid_enabled: bool = False
    knowledge_hybrid_required: bool = False
    text_embedding_model: str | None = None
    text_embedding_base_url: str | None = None
    text_embedding_api_key: str | None = None
    text_embedding_dimension: int = 1024
    text_embedding_max_length: int = 8_000
    qdrant_url: str | None = None
    qdrant_path: Path | None = None
    qdrant_api_key: str | None = None
    qdrant_prefer_grpc: bool = True
    qdrant_timeout: float = 30.0
    qdrant_collection: str = "hermes_knowledge_chunks_v1"
    hybrid_dense_weight: float = 0.65
    hybrid_lexical_weight: float = 0.35
    hybrid_dense_min_score: float = 0.38

    @classmethod
    def from_env(cls) -> "AppSettings":
        environment = os.getenv("APP_ENV", os.getenv("ENV", "local")).strip().lower()
        environment = {"prod": "production", "dev": "development"}.get(environment, environment)
        default_log_path = Path(__file__).resolve().parents[2] / "logs" / "server.log"
        embedding_model = (os.getenv("TEXT_EMBEDDING_MODEL_NAME") or "").strip() or None
        raw_qdrant_path = (os.getenv("KNOWLEDGE_QDRANT_PATH") or "").strip()
        qdrant_path = _storage_path_from_env("KNOWLEDGE_QDRANT_PATH", "data/qdrant") if raw_qdrant_path else None
        qdrant_url = (
            os.getenv("KNOWLEDGE_QDRANT_URL")
            or (None if qdrant_path else os.getenv("QDRANT_URL"))
            or ""
        ).strip() or None
        hybrid_available = bool(
            embedding_model
            and (qdrant_url or qdrant_path)
            and (os.getenv("TEXT_EMBEDDING_API_KEY") or "").strip()
            and (qdrant_path or (os.getenv("QDRANT_API_KEY") or "").strip())
        )
        return cls(
            name=os.getenv("APP_NAME", "Hermes 智维 · 企业智能工作台"),
            version=os.getenv("APP_VERSION", "0.3.0"),
            environment=environment,
            host=os.getenv("APP_HOST", "0.0.0.0"),
            port=int(os.getenv("APP_PORT", "1601")),
            workers=max(1, int(os.getenv("APP_WORKERS", "1"))),
            log_path=Path(os.getenv("LOG_PATH", str(default_log_path))),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_file_enabled=_as_bool(os.getenv("LOG_FILE_ENABLED"), True),
            cors_origins=_as_csv(
                os.getenv("CORS_ORIGINS"),
                ("http://localhost:1601", "http://127.0.0.1:1601"),
            ),
            cors_allow_credentials=_as_bool(os.getenv("CORS_ALLOW_CREDENTIALS"), True),
            docs_enabled=_as_bool(os.getenv("DOCS_ENABLED"), environment != "production"),
            mcp_enabled=_as_bool(os.getenv("MCP_ENABLED"), True),
            show_banner=_as_bool(os.getenv("SHOW_BANNER"), environment == "local"),
            database_url=_database_url_from_env(),
            knowledge_storage_path=_storage_path_from_env("KNOWLEDGE_STORAGE_PATH", "data/knowledge"),
            image_storage_path=_storage_path_from_env("IMAGE_STORAGE_PATH", "data/images"),
            dataset_storage_path=_storage_path_from_env("DATASET_STORAGE_PATH", "data/datasets"),
            ui_enabled=_as_bool(os.getenv("UI_ENABLED"), True),
            session_secret=os.getenv("SESSION_SECRET", "change-me-in-production"),
            chat_model=_chat_model_from_env(),
            admin_api_key=os.getenv("ADMIN_API_KEY") or None,
            scheduler_enabled=_as_bool(os.getenv("SCHEDULER_ENABLED"), True),
            knowledge_hybrid_enabled=_as_bool(os.getenv("KNOWLEDGE_HYBRID_ENABLED"), hybrid_available),
            knowledge_hybrid_required=_as_bool(os.getenv("KNOWLEDGE_HYBRID_REQUIRED"), False),
            text_embedding_model=embedding_model,
            text_embedding_base_url=(os.getenv("TEXT_EMBEDDING_BASE_URL") or "").strip() or None,
            text_embedding_api_key=(os.getenv("TEXT_EMBEDDING_API_KEY") or "").strip() or None,
            text_embedding_dimension=max(1, int(os.getenv("TEXT_EMBEDDING_DIMENSION") or 1024)),
            text_embedding_max_length=max(100, int(os.getenv("TEXT_EMBEDDING_MAX_TEXT_LENGTH") or 8_000)),
            qdrant_url=qdrant_url,
            qdrant_path=qdrant_path,
            qdrant_api_key=(os.getenv("QDRANT_API_KEY") or "").strip() or None,
            qdrant_prefer_grpc=_as_bool(os.getenv("KNOWLEDGE_QDRANT_PREFER_GRPC"), False),
            qdrant_timeout=_as_float(os.getenv("QDRANT_TIMEOUT"), 30.0),
            qdrant_collection=(os.getenv("KNOWLEDGE_QDRANT_COLLECTION") or "hermes_knowledge_chunks_v1").strip(),
            hybrid_dense_weight=_as_float(os.getenv("HYBRID_DENSE_WEIGHT"), 0.65),
            hybrid_lexical_weight=_as_float(os.getenv("HYBRID_LEXICAL_WEIGHT"), 0.35),
            hybrid_dense_min_score=_as_float(os.getenv("HYBRID_DENSE_MIN_SCORE"), 0.38),
        )

    @property
    def effective_cors_allow_credentials(self) -> bool:
        return self.cors_allow_credentials and "*" not in self.cors_origins

    @property
    def reload_enabled(self) -> bool:
        return self.environment == "local" and self.workers == 1

    def validate_for_startup(self) -> None:
        if self.environment == "production" and self.session_secret == "change-me-in-production":
            raise RuntimeError("生产环境必须配置 SESSION_SECRET")
        if self.knowledge_hybrid_required and not self.knowledge_hybrid_enabled:
            raise RuntimeError("企业知识库要求混合检索，但 Embedding/Qdrant 配置不完整")
        if self.knowledge_hybrid_enabled:
            missing = []
            if not self.text_embedding_model:
                missing.append("TEXT_EMBEDDING_MODEL_NAME")
            if not self.text_embedding_api_key:
                missing.append("TEXT_EMBEDDING_API_KEY")
            if not self.qdrant_url and not self.qdrant_path:
                missing.append("KNOWLEDGE_QDRANT_URL 或 KNOWLEDGE_QDRANT_PATH")
            if self.qdrant_url and not self.qdrant_api_key:
                missing.append("QDRANT_API_KEY")
            if self.qdrant_url and self.qdrant_path:
                raise RuntimeError("KNOWLEDGE_QDRANT_URL 与 KNOWLEDGE_QDRANT_PATH 只能配置一个")
            if missing:
                raise RuntimeError("混合检索配置不完整：" + ", ".join(missing))
            if self.hybrid_dense_weight < 0 or self.hybrid_lexical_weight < 0:
                raise RuntimeError("混合检索权重不能为负数")
            if self.hybrid_dense_weight + self.hybrid_lexical_weight <= 0:
                raise RuntimeError("混合检索权重之和必须大于 0")
