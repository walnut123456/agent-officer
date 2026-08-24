# 纯 Python 架构说明

## 当前状态

项目已经收敛为单一 Python 应用。旧 Java 多模块和 React 工程退出构建；界面、API、AI 工具、数据访问与 MCP 均由 `hermes` 工程承载。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `core` | 类型化配置、日志、异常、请求 ID、CORS、健康检查 |
| `api` | HTTP、SSE、管理认证和参数校验 |
| `application` | Agent 策略、MRAG、数据、图片、调度、会话和配置用例 |
| `domain` | 与框架无关的 Agent 事件、计划、会话、Token 预算和记忆规则 |
| `infrastructure` | SQLAlchemy 模型、事务与外部系统适配 |
| `web` | NiceGUI 页面；页面事件只调用 application service |
| `tool` | MRAG、搜索、文件、代码解释、报告、图像和数据分析 |
| `mcp` | Streamable HTTP、SSE 与 STDIO MCP 入口 |

## Java 管理表的收敛

旧管理端为 Agent、Client、Model、Provider、Prompt、RAG、MCP、Flow、Schedule 等资源分别维护重复 CRUD。Python 版将它们收敛为版本化 `resource_config`：

- `(resource_type, resource_id)` 唯一约束；
- 每次更新递增 `version`；
- `payload` 保存不同资源的扩展字段；
- 删除采用禁用语义，保留审计所需的数据；
- 管理接口要求 `X-Admin-Key`，未配置密钥时默认关闭。

## 会话与记忆

会话链路包括访客身份、会话所有权、消息落库、SSE 增量输出和历史恢复。Token 感知压缩遵循以下优先级：

1. System 指令永久保留；
2. 最近完整对话轮次优先；
3. 更早历史压缩为预算内摘要；
4. 极端预算下从最旧的非系统上下文开始裁剪。

压缩摘要可以写入 `session_memory_note`，用于后续语义召回。

## Python Agent 运行时

旧工程的 WORKFLOW / PLAN_SOLVE / REACT 分发已改为显式策略表。运行时统一输出 `plan_thought`、`plan`、`task`、`tool_call`、`tool_result`、`file`、`agent_stream`、`result` 和 `error` 事件；工具只能从注册表调用，每次调用写入 `tool_run` 执行账本。

- `AUTO` 使用快速规则在 ReAct 与 Plan-Solve 间路由；
- `REACT` 在有限步数内执行“模型决策 → 工具调用 → 观察结果”；
- `PLAN_SOLVE` 先生成可视计划，再逐步执行并综合；
- `WORKFLOW` 执行平台配置中的白名单节点，参数支持 `{{query}}` 注入。

## MRAG

知识库元数据、原始资料、规范正文和切块统一存储。默认 BM25 检索不依赖外部向量库；配置 `CHAT_MODEL` 后生成带来源编号的综合回答，视觉模型还会为图片生成可检索语义描述。原有 Qdrant 多模态高级检索仍作为 Python 工具端点保留，可按部署配置启用。

网页导入逐跳校验协议与目标 IP，拒绝本机、内网、凭据 URL、超大响应和无限重定向；文件路径固定在工作区存储根目录内。

## DataAgent 与产物

CSV/XLSX 数据集按访客隔离。无模型时提供本地描述统计；有模型时只允许单条 `SELECT`，写操作和多语句会在执行前拒绝。查询结果统一返回表格与图表规格。报告、图片、代码解释器产物通过 `file` 事件回传，不再依赖前端猜测工具响应结构。

## 调度

启用的 `schedule` 资源由异步调度器轮询，执行间隔限制在 60 秒到 31 天。任务使用同一 Agent 运行时与会话持久化，应用退出时会取消并等待在途任务，避免孤儿线程。

## 运维约束

- NiceGUI 使用单进程异步模型，因此 Web 进程固定为一个 worker；水平扩容由容器副本完成。
- 重型模型延迟加载，健康检查不得触发模型下载。
- 所有密钥只从环境变量或密钥系统读取。
- SSE 中间件不得缓存响应体。
- 生产数据库应使用外部数据库和版本化迁移；SQLite 只作为本地默认值。
