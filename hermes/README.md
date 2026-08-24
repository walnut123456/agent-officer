# Hermes 智维 · 企业智能工作台

面向企业知识、数据与流程协同的纯 Python Agent 工作台。这是项目唯一的应用工程。

企业 Hybrid RAG、Agent 策略、循环保护、测评数据和面试讲解见
[企业知识与 Agent 架构说明](docs/enterprise-knowledge-and-agent-design.md)。

## 目录

```text
hermes_officer/
├── api/             FastAPI、SSE 与管理接口
├── application/     会话和资源配置用例
├── domain/          会话与 Token 感知记忆压缩
├── infrastructure/ SQLAlchemy 持久化
├── web/             NiceGUI Python 页面
├── mcp/             MCP Server
├── tool/            AI、RAG、文件和报告工具
└── core/            配置、日志、异常、探针和请求上下文
```

## 本地启动

需要 Python 3.11+；使用 MySQL 时需先启动 MySQL 8.x。

```powershell
cd hermes
Copy-Item .env.example .env   # 已有 .env 时不要覆盖
uv sync --frozen
.\.venv\Scripts\python.exe server.py
```

首次完成 `uv sync` 后，也可以直接运行 `py server.py`；入口会自动切换到项目
`.venv`，避免误用全局 Python 导致缺少 MySQL 驱动。

Linux/macOS 的最后一条命令为 `.venv/bin/python server.py`。默认页面是
`http://127.0.0.1:1601/`，API 文档是 `http://127.0.0.1:1601/docs`。

## MySQL 配置

推荐在 `.env` 中分别配置，密码中的特殊字符会由程序安全编码：

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=your_user
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=reactor_agent
```

`DATABASE_URL` 的优先级高于 `MYSQL_*`。两者都未配置时仅为方便开发而回退到
`data/hermes.db`，生产环境应使用 MySQL。

旧 SQLite 数据迁移到一个已经备份的目标库时，可执行：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_sqlite_to_primary.py `
  --reset-target --confirm-database reactor_agent
```

该命令会删除目标库中的全部旧表，必须先做整库备份，并让
`--confirm-database` 与目标库名完全一致。

## 验证

```powershell
Invoke-RestMethod http://127.0.0.1:1601/health/live
Invoke-RestMethod http://127.0.0.1:1601/health/ready
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

- `/health/live` 验证进程；`/health/ready` 会真实查询主数据库，并在企业混合检索为必需时检查 Qdrant。
- 在“对话”发送消息后刷新页面，历史消息应仍存在。
- 在“企业知识库”新建库、上传文档、检索并删除，刷新后结果应与操作一致。
- 在 MySQL 中查看 `dialogue_session`、`dialogue_message`、`knowledge_base`、
  `knowledge_document` 和 `file_info`，可直接确认记录持久化。

## 架构亮点

- 单一 Python 应用：FastAPI、NiceGUI、Agent、Hybrid RAG 和工具服务共享同一生命周期。
- 单一关系数据源：会话、消息、资料库、工具运行和文件元数据统一由 SQLAlchemy
  写入 MySQL；上传的原始二进制文件保留在可替换的文件存储层。
- 清晰分层：API 只处理协议，application 编排业务，domain 表达规则，
  infrastructure 负责数据库与外部能力。
- 可运维：配置集中、依赖锁定、就绪探针会查询数据库，并提供可重复的数据迁移脚本。
- 可测试：知识库提供精确、语义和负样本测评集，核心功能可通过自动化测试和 HTTP 探针验证。

生产环境至少需要设置 `SESSION_SECRET`、`ADMIN_API_KEY`、`CORS_ORIGINS` 和模型提供商密钥。管理接口统一位于 `/api/admin/resources`，使用 `X-Admin-Key` 认证。
