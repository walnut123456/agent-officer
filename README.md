# 实习产出亮点介绍
针对跨类型统一接口引入热度+时间+score综合打分的方案  

## 多维度同一尺度打分细节
热度是min(1,log(1+score)/log(1+max))，以浏览量作为热度
max应该是定时计算的全库的max快照  
两种打分算法，一种是多权重加法，一种是乘法
综合下来我们选了法2，经过调研，我看很多相似场景是方法1，方法2皆有的  这种场景我的结果论是，没有完美的打分算法，我们只能尝试以用户角度去逐步完善它，当然系数的话也是可以根据效果动态配置的  

# 项目问答一览
**主要架构**：
**切分**：拿到一个文档我们会先识别类型，按照正文、标题、faq、表格、列表等，按照不同的内容去分大block,这个过程是按行正则，然后大block去递归切分成小的atom,小的atom再合并
递归切的时候只要不超过max_token就好， 合并的条件是合并后不超过target_token,以及原chunk<=min&&合并后chunk<=max
**测评**：
rag召回层面：Recall@k 召回目标chunk/召回chunk,HitRate@k 召回是否有目标chunk,Precision@k,召回目标chunk/目标chunk  
rag生成层面：answer_correctness:答案和标准答案拆成事实，计算召回率(答案中被标准答案覆盖的)和准确率（标准答案中被答案命中的）的调和平均数  
context_recall：ground_truth拆成事实，被召回chunk命中的比率
****


# agent-officer

agent-officer 是一个纯 Python 的 AI 工作台和智能体平台。项目使用 FastAPI 提供 API、SSE 与 MCP，使用 NiceGUI 编写浏览器界面，并将 Agent、MRAG、DataAgent、文件处理、代码执行、报告和图像生成统一到一个 Python 应用中。

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
