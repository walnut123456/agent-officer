# 实习产出亮点介绍
针对跨类型统一接口引入 es bm25粗召回+java应用层 热度+时间+score综合打分的方案
page*size*k粗召回，然后再精排取对应位置的
1.为什么不把热度和时间直接在es里和相关性统一打分   
2.跨分页不同次访问接口，热度可能不统一导致排序乱的问题     
3.分页
                                                                                                                                                                                                                                                                                                                                                                                     
# Hermes Officer

Hermes Officer 是一个纯 Python 的 AI 工作台和智能体平台。项目使用 FastAPI 提供 API、SSE 与 MCP，使用 NiceGUI 编写浏览器界面，并将 Agent、MRAG、DataAgent、文件处理、代码执行、报告和图像生成统一到一个 Python 应用中。

## 架构

```text
Browser
  |
  +-- NiceGUI pages (Python)
  +-- FastAPI HTTP / SSE / MCP
          |
          +-- application   用例编排
          +-- domain        会话、记忆、配置等业务规则
          +-- infrastructure 数据库和外部系统适配
          +-- tool          RAG、搜索、文件、报告、代码与图像工具
```

核心原则：API 不写业务规则，领域层不依赖 Web 框架，外部模型和存储通过适配器接入。

## 已迁移工作台

- AI 对话：自动路由、ReAct、Plan-Solve、Workflow、计划/工具事件和文件产物；
- MRAG：知识库、文件与网页导入、全文预览、本地 BM25、图片语义描述、来源引用；
- 图片生成：文生图、参考图编辑、多图输出和持久化历史；
- 数据分析：CSV/XLSX、字段与数据预览、只读 SQL、表格和图表；
- 平台配置：Agent、Model、Provider、Prompt、MCP、Flow、Schedule 等 11 类版本化资源。

## 快速开始

```powershell
cd hermes
uv sync --frozen
Copy-Item .env.example .env
uv run python server.py
```

默认地址：

- Python UI：`http://127.0.0.1:1601/`
- OpenAPI：`http://127.0.0.1:1601/docs`
- 健康检查：`http://127.0.0.1:1601/health/ready`

真实模型通过 `.env` 中的 `CHAT_MODEL` 及对应提供商密钥配置。未配置模型时使用安全的本地响应器，方便离线开发和测试。

## 质量检查

```powershell
cd hermes
uv run python -m compileall -q hermes_officer server.py
uv run python -m unittest discover -s tests -q
```

详细设计和迁移结果见 [纯 Python 架构说明](docs/python-architecture.md)。
逐项替代情况见 [功能替代矩阵](docs/feature-parity.md)。
