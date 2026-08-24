from __future__ import annotations

from typing import Any

from hermes_officer.application.agent_runtime import ToolDefinition, ToolRegistry
from hermes_officer.application.image_service import ImageWorkspaceService
from hermes_officer.application.knowledge_service import KnowledgeService
from hermes_officer.application.data_service import DataWorkspaceService
from hermes_officer.domain.agent import AgentExecutionContext


def build_tool_registry(
    registry: ToolRegistry,
    knowledge: KnowledgeService,
    images: ImageWorkspaceService,
    data_workspace: DataWorkspaceService,
) -> ToolRegistry:
    async def list_knowledge_bases(_: dict[str, Any], __: AgentExecutionContext):
        items = await knowledge.list_knowledge_bases()
        return {
            "knowledge_bases": [
                {"kb_id": item.kb_id, "name": item.name, "document_count": item.document_count}
                for item in items
            ]
        }

    async def search_knowledge(arguments: dict[str, Any], context: AgentExecutionContext):
        kb_id = str(arguments.get("kb_id") or context.knowledge_base_id).strip()
        if not kb_id:
            raise ValueError("knowledge_search 需要 kb_id；请先选择知识库")
        query = str(arguments.get("query") or "").strip()
        hits = await knowledge.search(kb_id, query, limit=int(arguments.get("limit") or 6))
        return {
            "hits": [
                {
                    "document_id": item.document_id,
                    "title": item.title,
                    "content": item.content,
                    "score": round(item.score, 4),
                    "source_url": item.source_url,
                }
                for item in hits
            ]
        }

    async def fetch_web(arguments: dict[str, Any], _: AgentExecutionContext):
        url = str(arguments.get("url") or "").strip()
        html, content_type = await knowledge.web_fetcher.fetch(url)
        content, title = knowledge._extract_html(html)
        return {
            "url": url,
            "title": title,
            "content_type": content_type,
            "content": content[:20_000],
            "truncated": len(content) > 20_000,
        }

    async def generate_image(arguments: dict[str, Any], context: AgentExecutionContext):
        run = await images.generate(
            context.visitor_id,
            str(arguments.get("prompt") or ""),
            size=str(arguments.get("size") or "1024x1024"),
            count=int(arguments.get("count") or 1),
        )
        if run.status != "SUCCESS":
            raise RuntimeError(run.error_message or "图片生成失败")
        return run.output

    async def query_data(arguments: dict[str, Any], context: AgentExecutionContext):
        result = await data_workspace.query(
            context.visitor_id,
            str(arguments.get("dataset_id") or ""),
            str(arguments.get("question") or ""),
        )
        return {
            "summary": result.summary,
            "sql": result.sql,
            "columns": result.columns,
            "rows": result.rows,
            "chart": result.chart,
        }

    async def create_report(arguments: dict[str, Any], context: AgentExecutionContext):
        from hermes_officer.tool.report import report
        from hermes_officer.util.file_util import upload_file
        from hermes_officer.util.report_file_util import sanitize_report_html_content

        output_format = str(arguments.get("format") or context.output_format or "docs")
        output_format = "markdown" if output_format == "docs" else output_format
        if output_format not in {"markdown", "html", "ppt"}:
            raise ValueError("report_tool 仅支持 docs、html 或 ppt")
        content = ""
        async for chunk in report(
            task=str(arguments.get("task") or ""),
            file_names=[],
            file_type=output_format,
            template_type=str(arguments.get("template") or "html"),
        ):
            content += chunk
        if output_format in {"html", "ppt"}:
            content = sanitize_report_html_content(content)
        file_type = "html" if output_format == "ppt" else output_format
        file_info = await upload_file(
            content=content,
            file_name=f"report-{context.request_id[:8]}",
            file_type=file_type,
            request_id=context.request_id,
        )
        return {"content": content, "fileInfo": [file_info], "format": output_format}

    async def run_code(arguments: dict[str, Any], context: AgentExecutionContext):
        from hermes_officer.tool.code_interpreter import code_interpreter_agent

        output = ""
        files: list[dict[str, Any]] = []
        async for chunk in code_interpreter_agent(
            task=str(arguments.get("task") or ""),
            file_names=[],
            request_id=context.request_id,
            stream=False,
            permission_profile="analysis",
        ):
            if hasattr(chunk, "output"):
                output += str(chunk.output or "")
            elif hasattr(chunk, "content"):
                output += str(chunk.content or "")
            elif isinstance(chunk, str):
                output += chunk
            file_list = getattr(chunk, "file_list", None)
            if file_list:
                files.extend(file_list)
        return {"result": output, "fileInfo": files}

    async def deep_search(arguments: dict[str, Any], context: AgentExecutionContext):
        from hermes_officer.tool.deepsearch import DeepSearch

        chunks: list[str] = []
        async for chunk in DeepSearch().run(
            query=str(arguments.get("query") or ""),
            request_id=context.request_id,
            max_loop=int(arguments.get("max_loop") or 1),
            stream=True,
        ):
            chunks.append(str(chunk))
            if sum(len(item) for item in chunks) > 30_000:
                break
        return {"content": "".join(chunks)[:30_000]}

    registry.register(ToolDefinition(
        name="list_knowledge_bases",
        description="列出可用知识库及其 ID。需要知识库检索但不知道 kb_id 时先调用。",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=list_knowledge_bases,
    ))
    registry.register(ToolDefinition(
        name="knowledge_search",
        description="在指定 MRAG 知识库中检索相关原文片段。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题"},
                "kb_id": {"type": "string", "description": "知识库 ID，可省略并使用当前工作区选择"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 6},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_knowledge,
    ))
    registry.register(ToolDefinition(
        name="web_fetch",
        description="安全抓取一个公开 HTTP/HTTPS 网页并提取正文。不能访问本机或内网。",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "公开网页 URL"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        handler=fetch_web,
    ))
    registry.register(ToolDefinition(
        name="image_generation",
        description="根据文字提示生成一到四张图片。",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "size": {"type": "string", "enum": ["1024x1024", "1536x1024", "1024x1536"]},
                "count": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        handler=generate_image,
    ))
    registry.register(ToolDefinition(
        name="data_query",
        description="用自然语言分析用户已经上传的 CSV/XLSX 数据集，返回表格与图表数据。",
        parameters={
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "数据集 ID"},
                "question": {"type": "string", "description": "分析问题"},
            },
            "required": ["dataset_id", "question"],
            "additionalProperties": False,
        },
        handler=query_data,
    ))
    registry.register(ToolDefinition(
        name="report_tool",
        description="把任务结果生成 Markdown 文档、HTML 网页或 PPT 风格 HTML 文件。",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "format": {"type": "string", "enum": ["docs", "markdown", "html", "ppt"]},
                "template": {"type": "string"},
            },
            "required": ["task", "format"],
            "additionalProperties": False,
        },
        handler=create_report,
    ))
    registry.register(ToolDefinition(
        name="code_interpreter",
        description="在受限分析沙箱中执行 Python 数据计算与绘图任务。",
        parameters={
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
            "additionalProperties": False,
        },
        handler=run_code,
    ))
    registry.register(ToolDefinition(
        name="deep_search",
        description="对公开互联网进行多轮搜索与归纳。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_loop": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=deep_search,
    ))
    return registry
