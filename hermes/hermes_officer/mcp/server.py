# -*- coding: utf-8 -*-
"""
Hermes Tool MCP Server

将 hermes 的所有工具以 MCP 协议暴露出去。
支持三种传输协议（共用同一份工具定义）：
  - Streamable HTTP（默认）：单端点 /mcp，由 SessionManager 管理会话
  - SSE（兼容旧客户端）：/mcp/sse + /mcp/messages/
  - STDIO（本地进程通信）：通过 mcp_stdio_server.py 启动
"""
import json
import os
import time
import uuid

from loguru import logger
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.sse import SseServerTransport
from mcp import types

# ── MCP Server 实例（三种协议共用）──────────────────────────
server = Server("hermes")

# ── Streamable HTTP Session Manager ─────────────────────────
session_manager = StreamableHTTPSessionManager(app=server)

# ── SSE Transport（兼容旧客户端）────────────────────────────
sse_transport = SseServerTransport("/mcp/messages/")


# =====================================================================
#  Tool Definitions
# =====================================================================

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="code_interpreter",
            description="Python 代码解释器。可以执行 Python 代码进行数据分析、绘图、文件处理等任务。"
                        "支持上传文件作为输入，返回执行结果和生成的文件。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "要执行的任务描述"},
                    "file_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "输入文件列表（文件名或URL）",
                    },
                    "permission_profile": {
                        "type": "string",
                        "enum": ["analysis", "workspace"],
                        "default": "analysis",
                        "description": "权限档位",
                    },
                },
                "required": ["task"],
            },
        ),
        types.Tool(
            name="report",
            description="报告生成工具。根据任务描述生成 HTML/Markdown/PPT 格式的报告。"
                        "支持上传文件作为参考数据。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "报告生成任务描述"},
                    "file_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "参考文件列表",
                    },
                    "file_type": {
                        "type": "string",
                        "enum": ["html", "markdown", "ppt"],
                        "default": "html",
                        "description": "输出文件格式",
                    },
                    "template_type": {
                        "type": "string",
                        "default": "html",
                        "description": "报告模板样式",
                    },
                },
                "required": ["task"],
            },
        ),
        types.Tool(
            name="image_generation",
            description="图片生成工具。支持文生图和图生图两种模式。",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "图片描述/提示词"},
                    "mode": {
                        "type": "string",
                        "enum": ["images", "edits"],
                        "description": "生成模式：images=文生图，edits=图生图",
                    },
                    "file_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "参考图列表（图生图时使用）",
                    },
                    "n": {"type": "integer", "default": 1, "description": "生成数量"},
                    "size": {"type": "string", "description": "输出尺寸，如 1024x1024"},
                },
                "required": ["prompt"],
            },
        ),
        types.Tool(
            name="deepsearch",
            description="深度搜索工具。通过多轮搜索和推理，提供深度研究结果。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询"},
                    "max_loop": {"type": "integer", "default": 1, "description": "最大搜索轮次"},
                    "search_engines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "搜索引擎列表，如 ['ddg', 'bing', 'jina']",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="web_fetch",
            description="网页抓取工具。抓取指定 URL 的网页内容并提取正文。",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的网页 URL"},
                    "timeout_seconds": {
                        "type": "integer",
                        "default": 30,
                        "description": "超时时间（秒）",
                    },
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="embedding_text",
            description="文本向量化工具。将文本转换为向量表示，用于语义搜索等场景。",
            inputSchema={
                "type": "object",
                "properties": {
                    "inputs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要向量化的文本列表",
                    },
                    "normalize": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否执行 L2 归一化",
                    },
                },
                "required": ["inputs"],
            },
        ),
        types.Tool(
            name="table_rag",
            description="表格 RAG 工具。根据用户问题检索相关表结构信息，用于 NL2SQL 等场景。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户问题"},
                    "current_date_info": {"type": "string", "description": "当前日期信息"},
                    "model_code_list": {
                        "type": "array",
                        "description": "表信息列表",
                    },
                    "schema_info": {
                        "type": "array",
                        "description": "字段信息列表",
                    },
                    "recall_type": {
                        "type": "string",
                        "default": "only_recall",
                        "description": "recall_type 为 only_recall 时仅进行粗排",
                    },
                    "use_vector": {"type": "boolean", "default": False, "description": "使用向量检索"},
                    "use_elastic": {"type": "boolean", "default": False, "description": "使用 ES 检索"},
                },
                "required": ["query", "current_date_info", "model_code_list", "schema_info"],
            },
        ),
        types.Tool(
            name="cal_engine",
            description="计算引擎工具。根据数据和查询生成指标计算公式。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户取数查询"},
                    "data": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "用户取数数据",
                    },
                },
                "required": ["query", "data"],
            },
        ),
        types.Tool(
            name="auto_analysis",
            description="自动数据分析工具。根据分析任务自动进行数据分析并生成报告。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "分析任务描述"},
                    "modelCodeList": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "数据模型 ID 列表（标识数据源）",
                    },
                    "businessKnowledge": {
                        "type": "string",
                        "description": "业务知识，包括分析维度、指标等",
                    },
                    "max_steps": {"type": "integer", "default": 10, "description": "最大分析步骤"},
                },
                "required": ["task", "modelCodeList"],
            },
        ),
        types.Tool(
            name="nl2sql",
            description="自然语言转 SQL 工具。将用户的自然语言问题转换为 SQL 查询。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户的自然语言问题"},
                    "current_date_info": {"type": "string", "description": "当前日期信息"},
                    "modelCodeList": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "表信息列表",
                    },
                    "schemaInfo": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "字段信息列表",
                    },
                    "dbType": {
                        "type": "string",
                        "default": "mysql",
                        "description": "SQL 方言类型",
                    },
                },
                "required": ["query", "current_date_info", "modelCodeList", "schemaInfo"],
            },
        ),
        types.Tool(
            name="sop_recall",
            description="SOP 召回工具。根据用户问题从 SOP 列表中选择最匹配的 SOP。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户问题"},
                    "sopList": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "SOP 列表",
                    },
                },
                "required": ["query", "sopList"],
            },
        ),
        types.Tool(
            name="script_runner",
            description="Skill 脚本执行工具。运行指定的 skill 脚本。",
            inputSchema={
                "type": "object",
                "properties": {
                    "skillName": {"type": "string", "description": "Skill 名称"},
                    "skillBasePath": {"type": "string", "description": "Skill 根目录"},
                    "scriptName": {"type": "string", "description": "脚本名称"},
                    "scriptPath": {"type": "string", "description": "脚本相对路径"},
                    "runtime": {
                        "type": "string",
                        "enum": ["python", "node", "shell", "powershell", "bat"],
                        "description": "脚本运行时",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "结构化参数",
                    },
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "原始命令行参数",
                    },
                    "timeoutSeconds": {
                        "type": "integer",
                        "default": 120,
                        "description": "超时时间（秒）",
                    },
                },
                "required": ["skillName", "skillBasePath", "scriptName", "scriptPath", "runtime"],
            },
        ),
        types.Tool(
            name="mrag_query",
            description="多模态 RAG 查询工具。支持文本和图片的多模态知识检索。",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "文本检索问题"},
                    "image_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "图片 URL 列表",
                    },
                    "kb_id": {
                        "type": "string",
                        "description": "知识库 ID，留空使用默认知识库",
                    },
                },
                "required": ["question"],
            },
        ),
    ]


# =====================================================================
#  Tool Call Handler
# =====================================================================

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """分发工具调用到对应的处理函数。"""
    request_id = f"mcp-{uuid.uuid4().hex[:12]}"
    try:
        handler = _TOOL_HANDLERS.get(name)
        if not handler:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
        result = await handler(arguments, request_id)
        if isinstance(result, str):
            return [types.TextContent(type="text", text=result)]
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]
    except Exception as exc:
        logger.exception(f"MCP tool '{name}' failed")
        return [types.TextContent(type="text", text=f"Error: {exc}")]


# =====================================================================
#  Individual Tool Handlers
# =====================================================================

async def _handle_code_interpreter(args: dict, request_id: str) -> str:
    from hermes_officer.tool.code_interpreter import code_interpreter_agent

    task = args.get("task", "")
    file_names = args.get("file_names", [])
    permission_profile = args.get("permission_profile", "analysis")

    # 处理文件路径
    for idx, f_name in enumerate(file_names):
        if not f_name.startswith("/") and not f_name.startswith("http"):
            file_names[idx] = f"{os.getenv('FILE_SERVER_URL')}/preview/{request_id}/{f_name}"

    content = ""
    async for chunk in code_interpreter_agent(
        task=task,
        file_names=file_names,
        request_id=request_id,
        stream=False,
        permission_profile=permission_profile,
    ):
        if hasattr(chunk, "output"):
            content = str(chunk.output) if chunk.output is not None else ""
            break
        if isinstance(chunk, str):
            content += chunk
    return content or "(code interpreter returned no output)"


async def _handle_report(args: dict, request_id: str) -> dict:
    from hermes_officer.tool.report import report
    from hermes_officer.util.file_util import upload_file
    from hermes_officer.util.report_file_util import sanitize_report_html_content

    task = args.get("task", "")
    file_names = args.get("file_names", [])
    file_type = args.get("file_type", "html")
    template_type = args.get("template_type", "html")

    for idx, f_name in enumerate(file_names):
        if not f_name.startswith("/") and not f_name.startswith("http"):
            file_names[idx] = f"{os.getenv('FILE_SERVER_URL')}/preview/{request_id}/{f_name}"

    content = ""
    async for chunk in report(
        task=task,
        file_names=file_names,
        file_type=file_type,
        template_type=template_type,
    ):
        content += chunk

    if file_type in ["ppt", "html"]:
        content = sanitize_report_html_content(content)

    file_info = [await upload_file(
        content=content, file_name="report", request_id=request_id,
        file_type="html" if file_type == "ppt" else file_type,
    )]
    return {"code": 200, "data": content, "fileInfo": file_info, "requestId": request_id}


async def _handle_image_generation(args: dict, request_id: str) -> dict:
    from hermes_officer.tool.image_generation import generate_images
    from hermes_officer.model.protocal import ImageGenerationRequest

    req = ImageGenerationRequest(
        requestId=request_id,
        prompt=args["prompt"],
        mode=args.get("mode"),
        fileNames=args.get("file_names", []),
        maskFileNames=args.get("mask_file_names", []),
        n=args.get("n", 1),
        size=args.get("size"),
    )
    return await generate_images(req)


async def _handle_deepsearch(args: dict, request_id: str) -> str:
    from hermes_officer.tool.deepsearch import DeepSearch

    query = args.get("query", "")
    max_loop = args.get("max_loop", 1)
    search_engines = args.get("search_engines", [])

    deepsearch = DeepSearch(engines=search_engines)
    result_chunks = []
    async for chunk in deepsearch.run(
        query=query,
        request_id=request_id,
        max_loop=max_loop,
        stream=False,
    ):
        result_chunks.append(str(chunk))
    return "\n".join(result_chunks) or "(deepsearch returned no output)"


async def _handle_web_fetch(args: dict, request_id: str) -> dict:
    from hermes_officer.tool.web_fetcher import WebFetcher
    from hermes_officer.model.protocal import WebFetchRequest
    from hermes_officer.util.file_util import upload_file

    req = WebFetchRequest(
        requestId=request_id,
        url=args["url"],
        timeoutSeconds=args.get("timeout_seconds", 30),
    )
    result = await WebFetcher().fetch(req)
    file_info = [await upload_file(
        content=result.full_content,
        file_name=result.file_name,
        request_id=request_id,
        file_type="markdown",
    )]
    return {
        "code": 200,
        "data": result.to_response_data(),
        "fileInfo": file_info,
        "requestId": request_id,
    }


async def _handle_embedding_text(args: dict, request_id: str) -> dict:
    import math
    from hermes_officer.tool.mrag.embedding.text_embedding import get_text_embedding_model

    inputs = args.get("inputs", [])
    normalize = args.get("normalize", True)

    embedding_model = get_text_embedding_model()
    vectors = embedding_model.encode_text_batch(inputs)

    if normalize:
        def _l2_norm(vec):
            norm = math.sqrt(sum(c * c for c in vec))
            return [c / norm for c in vec] if norm > 0 else vec
        vectors = [_l2_norm(v) for v in vectors]

    dimension = len(vectors[0]) if vectors else None
    return {
        "vectors": vectors,
        "dimension": dimension,
        "model": os.getenv("TEXT_EMBEDDING_MODEL_NAME"),
    }


async def _handle_table_rag(args: dict, request_id: str) -> dict:
    from hermes_officer.tool.table_rag import TableRAGAgent

    table_rag = TableRAGAgent(
        request_id=request_id,
        query=args["query"],
        modelCodeList=args.get("model_code_list", []),
        current_date_info=args.get("current_date_info", ""),
        schema_info=args.get("schema_info", []),
        use_vector=args.get("use_vector", False),
        use_elastic=args.get("use_elastic", False),
    )

    recall_type = args.get("recall_type", "only_recall")
    if recall_type == "only_recall":
        result = await table_rag.run_recall(query=args["query"])
    else:
        result = await table_rag.run(query=args["query"])

    return {"code": 200, "data": result.get("choosed_schema", {}), "requestId": request_id}


async def _handle_cal_engine(args: dict, request_id: str) -> dict:
    from jinja2 import Template
    from hermes_officer.util.llm_util import ask_llm
    from hermes_officer.util.prompt_util import get_prompt

    query = args.get("query", "")
    data = args.get("data", [])

    prompt = Template(get_prompt("analysis")["cal_engine_prompt"]).render(
        query=query, data=data,
    )
    expression = ""
    async for chunk in ask_llm(messages=prompt, model=os.getenv("CAL_ENGINE_MODEL", "qwen-vl-max"), only_content=True):
        expression = chunk
    return {"code": 200, "expression": expression, "requestId": request_id, "query": query}


async def _handle_auto_analysis(args: dict, request_id: str) -> dict:
    from hermes_officer.tool.auto_analysis import AutoAnalysisAgent

    task = args.get("task", "")
    model_code_list = args.get("modelCodeList", [])
    business_knowledge = args.get("businessKnowledge")
    max_steps = args.get("max_steps", 10)

    if not model_code_list:
        return {"code": 200, "data": "没有提供数据源，无法进行数据分析", "requestId": request_id}

    agent = AutoAnalysisAgent(max_steps=max_steps)
    result = await agent.run(
        requestId=request_id,
        task=task,
        modelCodeList=model_code_list,
        businessKnowledge=business_knowledge,
        max_steps=max_steps,
        stream=False,
    )
    return {"code": 200, "data": result, "requestId": request_id}


async def _handle_nl2sql(args: dict, request_id: str) -> dict:
    from hermes_officer.tool.nl2sql import NL2SQLAgent
    from hermes_officer.model.protocal import NL2SQLRequest

    req = NL2SQLRequest(
        requestId=request_id,
        query=args["query"],
        currentDateInfo=args.get("current_date_info", ""),
        modelCodeList=args.get("model_code_list", []),
        schemaInfo=args.get("schema_info", []),
        stream=False,
        dbType=args.get("db_type", "mysql"),
    )
    return await NL2SQLAgent().run(req)


async def _handle_sop_recall(args: dict, request_id: str) -> dict:
    from hermes_officer.tool.plan_sop import PlanSOP

    query = args.get("query", "")
    sop_list = args.get("sopList", [])

    pl_sop = PlanSOP(request_id)
    sop_mode, choosed_sop_string = pl_sop.sop_choose(query=query, sop_list=sop_list)
    return {
        "code": 200,
        "data": {"sop_mode": sop_mode, "choosed_sop_string": choosed_sop_string},
        "requestId": request_id,
    }


async def _handle_script_runner(args: dict, request_id: str) -> dict:
    from hermes_officer.tool.script_runner import run_script_request
    from hermes_officer.model.protocal import ScriptRunnerRequest

    req = ScriptRunnerRequest(
        requestId=request_id,
        skillName=args["skillName"],
        skillBasePath=args["skillBasePath"],
        scriptName=args["scriptName"],
        scriptPath=args["scriptPath"],
        runtime=args["runtime"],
        arguments=args.get("arguments", {}),
        argv=args.get("argv", []),
        timeoutSeconds=args.get("timeoutSeconds", 120),
    )
    response = await run_script_request(req)
    return response.model_dump(by_alias=True)


async def _handle_mrag_query(args: dict, request_id: str) -> str:
    from hermes_officer.tool.mrag.query import AgenticRAG

    kb_id = (args.get("kb_id", "") or os.getenv("DEFAULT_KB_ID", "")).strip()
    question = args.get("question", "")
    image_urls = args.get("image_urls", [])

    if not kb_id:
        return "MRAG 配置不完整：缺少 DEFAULT_KB_ID"

    agent = AgenticRAG(kb_id=kb_id, n_round=3)
    result_chunks = []
    for chunk in agent.run(question, image_urls):
        if isinstance(chunk, str):
            result_chunks.append(chunk)
        elif isinstance(chunk, dict):
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    result_chunks.append(content)
        else:
            # Handle OpenAI SDK chunk types
            choices = getattr(chunk, "choices", None)
            if choices:
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", "") or ""
                if content:
                    result_chunks.append(content)

    return "\n".join(result_chunks) or "(MRAG returned no output)"


# ── Handler 注册表 ─────────────────────────────────────────────
_TOOL_HANDLERS = {
    "code_interpreter": _handle_code_interpreter,
    "report": _handle_report,
    "image_generation": _handle_image_generation,
    "deepsearch": _handle_deepsearch,
    "web_fetch": _handle_web_fetch,
    "embedding_text": _handle_embedding_text,
    "table_rag": _handle_table_rag,
    "cal_engine": _handle_cal_engine,
    "auto_analysis": _handle_auto_analysis,
    "nl2sql": _handle_nl2sql,
    "sop_recall": _handle_sop_recall,
    "script_runner": _handle_script_runner,
    "mrag_query": _handle_mrag_query,
}
