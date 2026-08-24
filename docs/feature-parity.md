# 全量功能替代矩阵

“替代”指用户能力已经由 Python 页面、应用服务和持久化主链路承载；旧 Java 与 React 不再参与构建或运行。

| 旧能力 | Python 替代 | 入口 / 核心实现 | 验证 |
| --- | --- | --- | --- |
| 匿名访客、命名与会话归属 | 访客身份、所有权校验、历史恢复 | `/`、`application/conversation_service.py` | API + 浏览器 |
| 普通 Agent | ReAct 工具循环 | `application/agent_runtime.py` | 单测 + 浏览器 |
| Router Agent | AUTO 复杂度路由 | `AgentRouter` | 单测 |
| Plan-Solve | 计划、步骤状态、逐步执行与综合 | `AgentRuntime._run_plan_solve` | 单测 |
| Workflow | 版本化 Flow 节点顺序执行 | `AgentRuntime._run_workflow` | 策略单测 |
| Tool/Plan/Task SSE | 统一类型化 Agent 事件 | `/api/agent/.../messages` | API 回归 |
| HTML / 文档 / PPT / 表格模式 | 输出形态约束、报告工具、文件事件、DataAgent | AI 工作台输出选择器 | 协议回归 |
| MRAG 知识库 CRUD | SQLAlchemy 知识库与资料模型 | `/knowledge`、`/api/knowledge` | 单测 + 浏览器 |
| 文件导入与解析状态 | PDF、DOCX、TXT、MD、CSV、XLSX、图片 | `KnowledgeService.ingest_file` | 单测 |
| 网页导入 | SSRF 防护抓取、正文提取、切块 | `SafeWebFetcher` | 安全单测 |
| MRAG 问答 | 本地 BM25、模型综合、来源引用 | `KnowledgeService.stream_answer` | SSE 单测 |
| 多模态资料 | 图片元数据、视觉模型语义描述、原图预览 | `KnowledgeService._extract_source` | 文件链路 |
| 高级向量 MRAG | Python Qdrant/Embedding/Rerank/VLM 工具链 | `/v1/tool/mragQuery` | 原有 MRAG 测试 |
| 文生图 / 图生图 | Python 图片服务与参考图管理 | `/images`、`/api/images` | API + 浏览器 |
| 图片历史 | `tool_run` 持久化任务和结果 | `ImageWorkspaceService.list_history` | 服务回归 |
| DataAgent | CSV/XLSX、只读 NL2SQL、本地统计、图表 | `/data`、`/api/data` | 单测 + 浏览器 |
| 代码解释器 | analysis/workspace 权限策略与产物 | `/v1/tool/code_interpreter`、Agent tool | 原有安全测试 |
| 深度搜索 / 网页抓取 | Python 搜索与安全抓取工具 | `/v1/tool/deepsearch`、`web_fetch` | 原有测试 |
| Agent/Model/Prompt/MCP 等管理 | 11 类版本化统一资源 | `/admin`、`/api/admin/resources` | API + 浏览器 |
| Schedule | 生命周期内异步 Agent 调度 | `application/scheduler_service.py` | 调度单测 |
| MCP | HTTP、SSE、STDIO Python MCP Server | `/mcp`、`/mcp/sse` | 启动回归 |
| 文件预览与下载 | Python 文件服务与路径约束 | `/v1/file_tool` | 原有兼容测试 |

## 不再存在的运行依赖

- Java / Spring 多模块、Maven 与 Reactor 响应链；
- React / TypeScript / Vite / Node 构建；
- Java 与 Python 间重复 DTO、配置表和转发网关；
- 前端对不同工具响应的散落解析逻辑。
