from __future__ import annotations

from uuid import uuid4
import hmac
import json

from fastapi import FastAPI, Request

from hermes_officer.core.config import AppSettings
from hermes_officer.domain.agent import AgentEventType, AgentStrategy


def mount_ui(fastapi_app: FastAPI, settings: AppSettings) -> None:
    """Mount the Python UI on the same process and lifecycle as the public API."""
    from nicegui import app as nicegui_app
    from nicegui import ui

    conversation_service = fastapi_app.state.conversations
    knowledge_service = fastapi_app.state.knowledge
    image_service = fastapi_app.state.images
    data_service = fastapi_app.state.data_workspace
    resource_service = fastapi_app.state.resources
    page_css = """
        :root {
            --hermes-bg: #f5f7fb;
            --hermes-card: rgba(255, 255, 255, .88);
            --hermes-border: rgba(15, 23, 42, .10);
        }
        body { background: var(--hermes-bg); color: #172033; }
        .hermes-shell { height: 100vh; width: 100%; overflow: hidden; }
        .hermes-sidebar { width: 280px; border-right: 1px solid var(--hermes-border); }
        .hermes-main { min-width: 0; height: 100vh; }
        .hermes-scroll { overflow-y: auto; min-height: 0; }
        .hermes-glass { background: var(--hermes-card); backdrop-filter: blur(18px); }
        .hermes-nav-active { background: #eaf1ff; color: #1d4ed8; }
        .hermes-panel { border: 1px solid var(--hermes-border); border-radius: 16px; background: white; }
        .hermes-mobile-nav { display: none; }
        .hermes-kb-grid { display: grid; grid-template-columns: 270px minmax(360px, 1fr) minmax(320px, 420px); }
        @media (max-width: 1100px) { .hermes-kb-grid { grid-template-columns: 240px 1fr; } .hermes-query-panel { grid-column: 1 / -1; } }
        @media (max-width: 720px) {
            .hermes-sidebar { display: none; }
            .hermes-main { width: 100vw; flex: 0 0 100vw; }
            .hermes-mobile-nav { display: flex; }
            .hermes-kb-grid { grid-template-columns: minmax(0, 1fr); overflow-y: auto; }
            .hermes-query-panel { grid-column: auto; min-height: 360px; }
        }
    """

    @ui.page("/")
    async def workspace(request: Request) -> None:
        del request
        ui.add_css(page_css)
        service = conversation_service
        visitor_id = nicegui_app.storage.user.get("visitor_id") or uuid4().hex
        nicegui_app.storage.user["visitor_id"] = visitor_id
        profile = await service.bootstrap_visitor(visitor_id)

        if not profile.named:
            _render_naming_page(service, visitor_id)
            return

        session_id = nicegui_app.storage.user.get("session_id") or f"session-{uuid4().hex}"
        nicegui_app.storage.user["session_id"] = session_id
        await service.create_conversation(visitor_id, session_id)

        state = {
            "session_id": session_id,
            "sessions": await service.list_conversations(visitor_id),
            "messages": await service.history(visitor_id, session_id),
            "knowledge_bases": await knowledge_service.list_knowledge_bases(),
            "workflows": await resource_service.list("flow", enabled_only=True),
        }

        async def reload_sessions() -> None:
            state["sessions"] = await service.list_conversations(visitor_id)
            sidebar.refresh()

        async def select_session(selected_id: str) -> None:
            state["session_id"] = selected_id
            nicegui_app.storage.user["session_id"] = selected_id
            state["messages"] = await service.history(visitor_id, selected_id)
            messages.refresh()
            sidebar.refresh()

        async def create_session() -> None:
            selected_id = f"session-{uuid4().hex}"
            await service.create_conversation(visitor_id, selected_id)
            await select_session(selected_id)
            await reload_sessions()

        @ui.refreshable
        def sidebar() -> None:
            with ui.column().classes("w-full gap-1 p-3"):
                ui.button("新对话", icon="add", on_click=create_session).props("unelevated").classes("w-full")
                ui.separator().classes("my-2")
                ui.label("最近对话").classes("text-xs font-medium text-slate-500 px-2")
                for item in state["sessions"]:
                    active = item.session_id == state["session_id"]
                    button = ui.button(
                        item.title or "新对话",
                        on_click=lambda session=item.session_id: select_session(session),
                    ).props("flat no-caps align=left")
                    button.classes(
                        "w-full justify-start truncate " +
                        ("bg-blue-50 text-blue-700" if active else "text-slate-700")
                    )

        @ui.refreshable
        def messages() -> None:
            if not state["messages"]:
                with ui.column().classes("w-full h-full items-center justify-center text-center py-24"):
                    ui.icon("auto_awesome", size="42px").classes("text-blue-500")
                    ui.label(f"你好，{profile.username}").classes("text-2xl font-semibold mt-3")
                    ui.label("所有界面与业务逻辑现在都由 Python 驱动").classes("text-slate-500")
                return
            for item in state["messages"]:
                ui.chat_message(
                    text=item.content,
                    name=profile.username if item.role == "user" else "Hermes",
                    sent=item.role == "user",
                ).classes("w-full")

        async def send_message() -> None:
            content = (composer.value or "").strip()
            if not content:
                return
            composer.value = ""
            composer.disable()
            try:
                state["messages"] = await service.history(visitor_id, state["session_id"])
                with message_column:
                    ui.chat_message(text=content, name=profile.username, sent=True).classes("w-full")
                    with ui.chat_message(name="智维助手").classes("w-full"):
                        event_log = ui.column().classes("w-full gap-1 mb-2")
                        answer = ui.markdown("")
                buffer = ""
                selected_strategy = AgentStrategy(strategy_select.value or AgentStrategy.AUTO.value)
                selected_kb = knowledge_select.value or ""
                selected_flow = next(
                    (item for item in state["workflows"] if item.resource_id == (workflow_select.value or "")),
                    None,
                )
                workflow = tuple((selected_flow.payload or {}).get("nodes") or ()) if selected_flow else ()
                async for event in service.stream_agent_reply(
                    visitor_id,
                    state["session_id"],
                    content,
                    strategy=selected_strategy,
                    knowledge_base_id=selected_kb,
                    workflow=workflow,
                    output_format=output_select.value or "chat",
                ):
                    if event.event_type in {AgentEventType.AGENT_STREAM, AgentEventType.RESULT}:
                        if event.content and not event.data.get("intermediate"):
                            buffer += event.content
                            answer.set_content(buffer)
                    elif event.event_type == AgentEventType.PLAN:
                        plan = event.data.get("plan", {})
                        steps = plan.get("steps", [])
                        statuses = plan.get("statuses", [])
                        summary = "\n".join(
                            f"{'✅' if index < len(statuses) and statuses[index] == 'completed' else '⏳' if index < len(statuses) and statuses[index] == 'running' else '○'} {step}"
                            for index, step in enumerate(steps)
                        )
                        event_log.clear()
                        with event_log:
                            with ui.expansion(plan.get("title", "执行计划"), icon="account_tree", value=True).classes("w-full"):
                                ui.markdown(summary)
                    elif event.event_type in {AgentEventType.TOOL_CALL, AgentEventType.TOOL_RESULT, AgentEventType.TASK}:
                        with event_log:
                            icon = "build" if event.event_type == AgentEventType.TOOL_CALL else "check_circle" if event.event_type == AgentEventType.TOOL_RESULT else "play_arrow"
                            label = (
                                f"调用工具：{event.data.get('tool_name', event.content)}"
                                if event.event_type == AgentEventType.TOOL_CALL
                                else f"工具完成：{event.data.get('tool_name', '')}"
                                if event.event_type == AgentEventType.TOOL_RESULT
                                else f"执行：{event.content}"
                            )
                            ui.label(label).classes("text-xs text-slate-500").props(f"icon={icon}")
                    elif event.event_type == AgentEventType.FILE:
                        with event_log:
                            for file_item in event.data.get("files", []):
                                if not isinstance(file_item, dict):
                                    continue
                                file_url = file_item.get("domainUrl") or file_item.get("downloadUrl") or file_item.get("ossUrl")
                                ui.button(
                                    file_item.get("fileName") or "打开生成文件",
                                    icon="description",
                                    on_click=lambda url=file_url: ui.navigate.to(url, new_tab=True),
                                ).props("outline no-caps")
                    elif event.event_type == AgentEventType.ERROR:
                        with event_log:
                            ui.label(event.content).classes("text-sm text-red-600")
                state["messages"] = await service.history(visitor_id, state["session_id"])
                await reload_sessions()
            except Exception as error:
                ui.notify(f"发送失败：{error}", type="negative")
            finally:
                composer.enable()

        with ui.row().classes("hermes-shell no-wrap gap-0"):
            with ui.column().classes("hermes-sidebar hermes-glass h-full gap-0"):
                with ui.row().classes("items-center gap-2 px-5 py-4"):
                    ui.icon("hub", size="26px").classes("text-blue-600")
                    ui.label("Hermes 智维").classes("text-lg font-semibold")
                _render_workspace_navigation("chat")
                ui.separator().classes("mx-3")
                sidebar()
                ui.space()
                ui.label(f"{profile.username} · 企业版").classes("text-xs text-slate-500 px-5 py-4")

            with ui.column().classes("hermes-main flex-1 gap-0"):
                _render_mobile_navigation("chat")
                with ui.row().classes("hermes-glass w-full items-center border-b border-slate-200 px-6 py-4"):
                    with ui.column().classes("gap-0"):
                        ui.label("企业智能工作台").classes("text-base font-medium")
                        ui.label("知识、数据与流程统一编排").classes("text-xs text-slate-500")
                    ui.space()
                    strategy_select = ui.select(
                        {
                            "auto": "自动选择（推荐）",
                            "react": "快速执行（ReAct）",
                            "plan_solve": "复杂任务（Plan-Solve）",
                            "workflow": "固定流程（Workflow）",
                        },
                        value="auto",
                        label="执行策略",
                    ).props("outlined dense options-dense").classes("w-56").tooltip(
                        "默认由系统判断；高级用户可手动固定执行方式，便于审计和复现"
                    )
                    knowledge_select = ui.select(
                        {"": "不绑定知识库", **{item.kb_id: item.name for item in state["knowledge_bases"]}},
                        value="",
                        label="知识检索",
                    ).props("outlined dense options-dense clearable").classes("w-52")
                    workflow_select = ui.select(
                        {"": "未选择工作流", **{item.resource_id: item.name for item in state["workflows"]}},
                        value="",
                        label="Workflow",
                    ).props("outlined dense options-dense clearable").classes("w-48")
                    output_select = ui.select(
                        {"chat": "聊天", "docs": "文档", "html": "网页", "ppt": "PPT", "table": "表格"},
                        value="chat",
                        label="输出",
                    ).props("outlined dense options-dense").classes("w-32").tooltip(
                        "聊天直接回复；文档、网页和 PPT 会调用报告工具生成可交付文件"
                    )
                    ui.badge("FastAPI + NiceGUI", color="blue").props("outline")
                message_column = ui.column().classes("hermes-scroll flex-1 w-full max-w-4xl mx-auto px-6 py-5")
                with message_column:
                    messages()
                with ui.row().classes("hermes-glass w-full items-end gap-3 border-t border-slate-200 px-6 py-4"):
                    composer = ui.textarea(placeholder="输入你的问题……").props("outlined autogrow").classes("flex-1")
                    ui.button(icon="send", on_click=send_message).props("round unelevated").tooltip("发送")

    @ui.page("/knowledge")
    async def knowledge_workspace(request: Request) -> None:
        del request
        ui.add_css(page_css)
        visitor_id = nicegui_app.storage.user.get("visitor_id") or uuid4().hex
        nicegui_app.storage.user["visitor_id"] = visitor_id
        profile = await conversation_service.bootstrap_visitor(visitor_id)
        if not profile.named:
            _render_naming_page(conversation_service, visitor_id)
            return

        knowledge_bases = await knowledge_service.list_knowledge_bases()
        selected_kb_id = nicegui_app.storage.user.get("knowledge_base_id")
        if not any(item.kb_id == selected_kb_id for item in knowledge_bases):
            selected_kb_id = knowledge_bases[0].kb_id if knowledge_bases else ""
        documents = await knowledge_service.list_documents(selected_kb_id) if selected_kb_id else []
        state = {
            "knowledge_bases": knowledge_bases,
            "selected_kb_id": selected_kb_id,
            "documents": documents,
        }

        async def reload_catalog() -> None:
            state["knowledge_bases"] = await knowledge_service.list_knowledge_bases()
            catalog.refresh()

        async def reload_documents() -> None:
            kb_id = state["selected_kb_id"]
            state["documents"] = await knowledge_service.list_documents(kb_id) if kb_id else []
            document_list.refresh()
            await reload_catalog()

        async def select_knowledge_base(kb_id: str) -> None:
            state["selected_kb_id"] = kb_id
            nicegui_app.storage.user["knowledge_base_id"] = kb_id
            await reload_documents()
            catalog.refresh()

        async def create_knowledge_base() -> None:
            try:
                created = await knowledge_service.create_knowledge_base(
                    create_name.value or "",
                    create_description.value or "",
                )
                create_dialog.close()
                create_name.value = ""
                create_description.value = ""
                await reload_catalog()
                await select_knowledge_base(created.kb_id)
                ui.notify("知识库已创建", type="positive")
            except Exception as error:
                ui.notify(str(error), type="negative")

        async def remove_knowledge_base(kb_id: str) -> None:
            try:
                await knowledge_service.delete_knowledge_base(kb_id)
                state["knowledge_bases"] = await knowledge_service.list_knowledge_bases()
                state["selected_kb_id"] = state["knowledge_bases"][0].kb_id if state["knowledge_bases"] else ""
                state["documents"] = (
                    await knowledge_service.list_documents(state["selected_kb_id"])
                    if state["selected_kb_id"] else []
                )
                nicegui_app.storage.user["knowledge_base_id"] = state["selected_kb_id"]
                catalog.refresh()
                document_list.refresh()
                ui.notify("知识库及其资料已删除", type="positive")
            except Exception as error:
                ui.notify(str(error), type="negative")

        async def upload_source(event) -> None:
            kb_id = state["selected_kb_id"]
            if not kb_id:
                ui.notify("请先创建知识库", type="warning")
                return
            notification = ui.notification(timeout=None, spinner=True, message=f"正在解析 {event.file.name}")
            try:
                document = await knowledge_service.ingest_file(
                    kb_id,
                    event.file.name,
                    await event.file.read(),
                    event.file.content_type,
                )
                await reload_documents()
                if document.status == "READY":
                    notification.message = f"{document.title} 已建立索引"
                    notification.type = "positive"
                else:
                    notification.message = f"解析失败：{document.error_message}"
                    notification.type = "negative"
            except Exception as error:
                notification.message = str(error)
                notification.type = "negative"
            finally:
                notification.spinner = False
                notification.timeout = 5

        async def add_web_source() -> None:
            kb_id = state["selected_kb_id"]
            if not kb_id:
                ui.notify("请先创建知识库", type="warning")
                return
            notification = ui.notification(timeout=None, spinner=True, message="正在抓取并解析网页")
            try:
                document = await knowledge_service.ingest_url(kb_id, web_url.value or "")
                web_url.value = ""
                await reload_documents()
                notification.message = "网页已建立索引" if document.status == "READY" else document.error_message
                notification.type = "positive" if document.status == "READY" else "negative"
            except Exception as error:
                notification.message = str(error)
                notification.type = "negative"
            finally:
                notification.spinner = False
                notification.timeout = 5

        async def delete_document(document_id: str) -> None:
            try:
                await knowledge_service.delete_document(state["selected_kb_id"], document_id)
                await reload_documents()
                ui.notify("资料已删除", type="positive")
            except Exception as error:
                ui.notify(str(error), type="negative")

        async def show_full_content(document_id: str) -> None:
            document = await knowledge_service.get_document(document_id)
            if document is None:
                ui.notify("资料不存在", type="negative")
                return
            with ui.dialog() as content_dialog, ui.card().classes("w-[760px] max-w-[95vw] h-[80vh]"):
                with ui.row().classes("w-full items-center"):
                    ui.label(document.title).classes("text-lg font-semibold truncate")
                    ui.space()
                    ui.button(icon="close", on_click=content_dialog.close).props("flat round")
                ui.separator()
                if document.status == "READY":
                    with ui.scroll_area().classes("w-full flex-1"):
                        ui.markdown(document.canonical_content).classes("px-2")
                else:
                    ui.label(document.error_message or "正文仍在处理中").classes("text-amber-700")
            content_dialog.open()

        async def ask_knowledge_base() -> None:
            question = (question_input.value or "").strip()
            if not state["selected_kb_id"] or not question:
                ui.notify("请选择知识库并输入问题", type="warning")
                return
            ask_button.disable()
            answer_area.clear()
            try:
                with answer_area:
                    answer = ui.markdown("").classes("w-full")
                buffer = ""
                async for chunk in knowledge_service.stream_answer(state["selected_kb_id"], question):
                    buffer += chunk
                    answer.set_content(buffer)
            except Exception as error:
                ui.notify(str(error), type="negative")
            finally:
                ask_button.enable()

        with ui.dialog() as create_dialog, ui.card().classes("w-[460px] max-w-[92vw] p-6 gap-4"):
            ui.label("新建知识库").classes("text-xl font-semibold")
            create_name = ui.input("名称", placeholder="例如：产品资料库").props("outlined").classes("w-full")
            create_description = ui.textarea("描述").props("outlined autogrow").classes("w-full")
            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=create_dialog.close).props("flat")
                ui.button("创建", icon="add", on_click=create_knowledge_base).props("unelevated")

        delete_target = {"kb_id": "", "name": ""}

        async def confirm_remove_knowledge_base() -> None:
            kb_id = delete_target["kb_id"]
            delete_dialog.close()
            if kb_id:
                await remove_knowledge_base(kb_id)

        def request_remove_knowledge_base(kb_id: str, name: str) -> None:
            delete_target.update(kb_id=kb_id, name=name)
            delete_message.set_text(f"将永久删除“{name}”及其中全部资料，且无法撤销。")
            delete_dialog.open()

        with ui.dialog() as delete_dialog, ui.card().classes("w-[460px] max-w-[92vw] p-6 gap-4"):
            ui.label("确认删除知识库").classes("text-xl font-semibold text-red-700")
            delete_message = ui.label().classes("text-sm text-slate-600")
            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=delete_dialog.close).props("flat")
                ui.button("永久删除", icon="delete_forever", on_click=confirm_remove_knowledge_base).props("unelevated color=negative")

        @ui.refreshable
        def catalog() -> None:
            with ui.column().classes("w-full gap-2"):
                with ui.row().classes("w-full items-center px-1"):
                    ui.label("知识库").classes("font-semibold")
                    ui.space()
                    ui.button(icon="add", on_click=create_dialog.open).props("flat round dense").tooltip("新建")
                if not state["knowledge_bases"]:
                    ui.label("还没有知识库").classes("text-sm text-slate-500 p-3")
                for item in state["knowledge_bases"]:
                    selected = item.kb_id == state["selected_kb_id"]
                    with ui.card().classes(
                        "w-full p-3 cursor-pointer border " +
                        ("border-blue-400 bg-blue-50" if selected else "border-slate-200")
                    ).on("click", lambda _, kb_id=item.kb_id: select_knowledge_base(kb_id)):
                        with ui.row().classes("w-full items-start no-wrap"):
                            with ui.column().classes("gap-0 min-w-0"):
                                ui.label(item.name).classes("font-medium truncate")
                                ui.label(item.description or "暂无描述").classes("text-xs text-slate-500 truncate")
                                ui.label(f"{item.document_count} 条资料").classes("text-xs text-blue-600 mt-1")
                            ui.space()
                            ui.button(
                                "删除",
                                icon="delete_outline",
                            ).props("flat dense no-caps color=negative").on(
                                "click.stop",
                                lambda _, kb_id=item.kb_id, name=item.name: request_remove_knowledge_base(kb_id, name),
                            ).tooltip("删除知识库")

        @ui.refreshable
        def document_list() -> None:
            with ui.column().classes("w-full gap-2"):
                if not state["selected_kb_id"]:
                    ui.label("请先创建或选择知识库").classes("text-slate-500 py-12 self-center")
                    return
                if not state["documents"]:
                    ui.label("暂无资料，可上传文件或导入网页").classes("text-slate-500 py-12 self-center")
                for item in state["documents"]:
                    status_color = "positive" if item.status == "READY" else "negative" if item.status == "FAILED" else "warning"
                    with ui.card().classes("w-full p-3 border border-slate-200"):
                        with ui.row().classes("w-full items-center no-wrap"):
                            ui.icon("language" if item.source_type == "url" else "description").classes("text-blue-500")
                            with ui.column().classes("gap-0 min-w-0 flex-1"):
                                ui.label(item.title).classes("font-medium truncate")
                                ui.label(f"{item.chunk_count} 个片段 · {item.file_size / 1024:.1f} KB").classes("text-xs text-slate-500")
                            ui.badge(item.status, color=status_color).props("outline")
                            ui.button(icon="article", on_click=lambda _, doc_id=item.document_id: show_full_content(doc_id)).props("flat round dense").tooltip("查看正文")
                            if item.preview_url:
                                ui.button(icon="open_in_new", on_click=lambda _, url=item.preview_url: ui.navigate.to(url, new_tab=True)).props("flat round dense").tooltip("打开原资料")
                            ui.button(icon="delete_outline", on_click=lambda _, doc_id=item.document_id: delete_document(doc_id)).props("flat round dense color=negative").tooltip("删除")
                        if item.error_message:
                            ui.label(item.error_message).classes("text-xs text-red-600")

        with ui.row().classes("hermes-shell no-wrap gap-0"):
            with ui.column().classes("hermes-sidebar hermes-glass h-full gap-0"):
                with ui.row().classes("items-center gap-2 px-5 py-4"):
                    ui.icon("hub", size="26px").classes("text-blue-600")
                    ui.label("Hermes 智维").classes("text-lg font-semibold")
                _render_workspace_navigation("knowledge")
                ui.space()
                ui.label(f"{profile.username} · 企业版").classes("text-xs text-slate-500 px-5 py-4")

            with ui.column().classes("hermes-main flex-1 gap-0"):
                _render_mobile_navigation("knowledge")
                with ui.row().classes("hermes-glass w-full items-center border-b border-slate-200 px-6 py-4"):
                    with ui.column().classes("gap-0"):
                        ui.label("企业知识中台").classes("text-base font-semibold")
                        ui.label("Embedding 语义召回 + BM25 精确召回，融合排序后生成引用回答").classes("text-xs text-slate-500")
                    ui.space()
                    ui.badge("Hybrid RAG", color="blue").props("outline")
                with ui.element("div").classes("hermes-kb-grid hermes-scroll flex-1 gap-4 p-4"):
                    with ui.column().classes("hermes-panel p-3 overflow-auto"):
                        catalog()
                    with ui.column().classes("hermes-panel p-4 min-w-0 overflow-auto"):
                        with ui.row().classes("w-full items-center"):
                            ui.label("资料与索引").classes("font-semibold")
                            ui.space()
                            ui.button(icon="refresh", on_click=reload_documents).props("flat round dense")
                        with ui.row().classes("w-full items-end gap-2"):
                            ui.upload(
                                label="上传 PDF / Word / 表格 / 图片",
                                on_upload=upload_source,
                                auto_upload=True,
                                max_file_size=50_000_000,
                            ).props("accept=.pdf,.docx,.txt,.md,.csv,.xlsx,.png,.jpg,.jpeg,.webp flat bordered").classes("flex-1")
                        with ui.row().classes("w-full items-end gap-2"):
                            web_url = ui.input("导入网页", placeholder="https://example.com/article").props("outlined dense").classes("flex-1")
                            ui.button("导入", icon="link", on_click=add_web_source).props("unelevated")
                        ui.separator().classes("my-2")
                        document_list()
                    with ui.column().classes("hermes-query-panel hermes-panel p-4 min-w-0"):
                        ui.label("知识问答").classes("font-semibold")
                        ui.label("回答会标注来源；未配置模型时返回最相关原文证据。").classes("text-xs text-slate-500")
                        question_input = ui.textarea("问题", placeholder="根据这些资料，总结关键结论……").props("outlined autogrow").classes("w-full")
                        ask_button = ui.button("开始检索", icon="search", on_click=ask_knowledge_base).props("unelevated").classes("w-full")
                        ui.separator().classes("my-1")
                        answer_area = ui.scroll_area().classes("w-full flex-1")

    @ui.page("/images")
    async def image_workspace(request: Request) -> None:
        del request
        ui.add_css(page_css)
        visitor_id = nicegui_app.storage.user.get("visitor_id") or uuid4().hex
        nicegui_app.storage.user["visitor_id"] = visitor_id
        profile = await conversation_service.bootstrap_visitor(visitor_id)
        if not profile.named:
            _render_naming_page(conversation_service, visitor_id)
            return

        state = {
            "references": await image_service.list_references(visitor_id),
            "selected_references": set(),
            "history": await image_service.list_history(visitor_id),
        }

        async def upload_reference(event) -> None:
            try:
                reference = await image_service.save_reference(
                    visitor_id,
                    event.file.name,
                    await event.file.read(),
                    event.file.content_type,
                )
                state["references"] = await image_service.list_references(visitor_id)
                state["selected_references"].add(reference.reference_id)
                reference_list.refresh()
                ui.notify("参考图已上传", type="positive")
            except Exception as error:
                ui.notify(str(error), type="negative")

        def toggle_reference(reference_id: str, selected: bool) -> None:
            if selected:
                state["selected_references"].add(reference_id)
            else:
                state["selected_references"].discard(reference_id)

        async def refresh_history() -> None:
            state["history"] = await image_service.list_history(visitor_id)
            history_list.refresh()

        async def generate() -> None:
            prompt = (prompt_input.value or "").strip()
            if not prompt:
                ui.notify("请输入图片提示词", type="warning")
                return
            generate_button.disable()
            notification = ui.notification(timeout=None, spinner=True, message="图片任务执行中，可能需要几分钟")
            try:
                selected = list(state["selected_references"])
                run = await image_service.generate(
                    visitor_id,
                    prompt,
                    mode="edits" if selected else "images",
                    reference_ids=selected,
                    size=size_select.value,
                    count=int(count_select.value or 1),
                )
                await refresh_history()
                result_panel.clear()
                with result_panel:
                    _render_image_run(run)
                if run.status == "SUCCESS":
                    notification.message = "图片生成完成"
                    notification.type = "positive"
                else:
                    notification.message = run.error_message or "图片生成失败"
                    notification.type = "negative"
            except Exception as error:
                notification.message = str(error)
                notification.type = "negative"
            finally:
                notification.spinner = False
                notification.timeout = 6
                generate_button.enable()

        @ui.refreshable
        def reference_list() -> None:
            with ui.column().classes("w-full gap-2"):
                if not state["references"]:
                    ui.label("未选择参考图时为文生图").classes("text-xs text-slate-500")
                for item in state["references"]:
                    preview = f"{item.preview_url}?visitor_id={visitor_id}"
                    with ui.row().classes("w-full items-center no-wrap rounded-lg border border-slate-200 p-2"):
                        ui.image(preview).classes("w-12 h-12 rounded object-cover")
                        ui.label(item.filename).classes("text-sm truncate flex-1")
                        checkbox = ui.checkbox(value=item.reference_id in state["selected_references"])
                        checkbox.on_value_change(
                            lambda event, reference_id=item.reference_id: toggle_reference(reference_id, bool(event.value))
                        )

        @ui.refreshable
        def history_list() -> None:
            with ui.column().classes("w-full gap-3"):
                if not state["history"]:
                    ui.label("暂无生成记录").classes("text-sm text-slate-500 py-8 self-center")
                for run in state["history"]:
                    with ui.expansion(run.prompt[:46] or "图片任务", icon="image").classes("w-full border border-slate-200 rounded-xl"):
                        _render_image_run(run)

        with ui.row().classes("hermes-shell no-wrap gap-0"):
            with ui.column().classes("hermes-sidebar hermes-glass h-full gap-0"):
                with ui.row().classes("items-center gap-2 px-5 py-4"):
                    ui.icon("hub", size="26px").classes("text-blue-600")
                    ui.label("Hermes 智维").classes("text-lg font-semibold")
                _render_workspace_navigation("images")
                ui.space()
                ui.label(f"{profile.username} · 企业版").classes("text-xs text-slate-500 px-5 py-4")

            with ui.column().classes("hermes-main flex-1 gap-0"):
                _render_mobile_navigation("images")
                with ui.row().classes("hermes-glass w-full items-center border-b border-slate-200 px-6 py-4"):
                    with ui.column().classes("gap-0"):
                        ui.label("图片生成工作台").classes("text-base font-semibold")
                        ui.label("文生图、参考图编辑、批量结果与历史记录均由 Python 管理").classes("text-xs text-slate-500")
                    ui.space()
                    ui.badge("Image Agent", color="purple").props("outline")
                with ui.row().classes("hermes-scroll flex-1 w-full gap-4 p-4 no-wrap"):
                    with ui.column().classes("hermes-panel w-[360px] shrink-0 p-4 gap-4 overflow-auto"):
                        ui.label("创作参数").classes("font-semibold")
                        prompt_input = ui.textarea(
                            "提示词",
                            placeholder="描述画面、风格、构图、光线和文字要求……",
                        ).props("outlined autogrow").classes("w-full")
                        with ui.row().classes("w-full gap-2"):
                            size_select = ui.select(
                                ["1024x1024", "1536x1024", "1024x1536"],
                                value="1024x1024",
                                label="尺寸",
                            ).props("outlined dense").classes("flex-1")
                            count_select = ui.select([1, 2, 3, 4], value=1, label="数量").props("outlined dense").classes("w-24")
                        generate_button = ui.button("生成图片", icon="auto_awesome", on_click=generate).props("unelevated color=purple").classes("w-full")
                        ui.separator()
                        with ui.row().classes("w-full items-center"):
                            ui.label("参考图（选中后进入编辑模式）").classes("text-sm font-medium")
                        ui.upload(
                            label="上传参考图",
                            on_upload=upload_reference,
                            auto_upload=True,
                            max_file_size=20_000_000,
                        ).props("accept=.png,.jpg,.jpeg,.webp flat bordered").classes("w-full")
                        reference_list()
                    with ui.column().classes("hermes-panel flex-1 min-w-0 p-5 gap-4 overflow-auto"):
                        ui.label("本次结果").classes("font-semibold")
                        result_panel = ui.column().classes("w-full gap-3")
                        with result_panel:
                            ui.label("填写提示词后开始创作").classes("text-slate-500 py-16 self-center")
                    with ui.column().classes("hermes-panel w-[360px] shrink-0 p-4 overflow-auto"):
                        with ui.row().classes("w-full items-center"):
                            ui.label("生成历史").classes("font-semibold")
                            ui.space()
                            ui.button(icon="refresh", on_click=refresh_history).props("flat round dense")
                        history_list()

    @ui.page("/data")
    async def data_workspace(request: Request) -> None:
        del request
        ui.add_css(page_css)
        visitor_id = nicegui_app.storage.user.get("visitor_id") or uuid4().hex
        nicegui_app.storage.user["visitor_id"] = visitor_id
        profile = await conversation_service.bootstrap_visitor(visitor_id)
        if not profile.named:
            _render_naming_page(conversation_service, visitor_id)
            return

        datasets = await data_service.list_datasets(visitor_id)
        state = {
            "datasets": datasets,
            "selected_id": datasets[0].dataset_id if datasets else "",
        }

        async def upload_dataset(event) -> None:
            notification = ui.notification(timeout=None, spinner=True, message=f"正在读取 {event.file.name}")
            try:
                dataset = await data_service.upload(visitor_id, event.file.name, await event.file.read())
                state["datasets"] = await data_service.list_datasets(visitor_id)
                state["selected_id"] = dataset.dataset_id
                dataset_list.refresh()
                preview_panel.refresh()
                notification.message = f"已载入 {dataset.row_count:,} 行数据"
                notification.type = "positive"
            except Exception as error:
                notification.message = str(error)
                notification.type = "negative"
            finally:
                notification.spinner = False
                notification.timeout = 5

        def select_dataset(dataset_id: str) -> None:
            state["selected_id"] = dataset_id
            dataset_list.refresh()
            preview_panel.refresh()
            result_panel.clear()
            with result_panel:
                ui.label("输入问题开始分析").classes("text-slate-500 py-12 self-center")

        async def query_dataset() -> None:
            dataset_id = state["selected_id"]
            question = (data_question.value or "").strip()
            if not dataset_id or not question:
                ui.notify("请选择数据集并输入问题", type="warning")
                return
            query_button.disable()
            notification = ui.notification(timeout=None, spinner=True, message="正在分析数据")
            try:
                result = await data_service.query(visitor_id, dataset_id, question)
                result_panel.clear()
                with result_panel:
                    ui.label(result.summary).classes("text-sm text-slate-700")
                    with ui.expansion("查询逻辑", icon="code").classes("w-full"):
                        ui.code(result.sql, language="sql").classes("w-full")
                    if result.rows:
                        columns = [{"name": item, "label": item, "field": item, "align": "left"} for item in result.columns]
                        ui.table(columns=columns, rows=result.rows, pagination=10).props("flat bordered dense").classes("w-full")
                    if result.chart:
                        ui.echart({
                            "tooltip": {"trigger": "axis"},
                            "xAxis": {"type": "category", "data": result.chart.get("x", [])},
                            "yAxis": {"type": "value"},
                            "series": [{
                                "type": result.chart.get("type", "bar"),
                                "name": result.chart.get("y_field", "数值"),
                                "data": result.chart.get("y", []),
                            }],
                        }).classes("w-full h-80")
                notification.message = "分析完成"
                notification.type = "positive"
            except Exception as error:
                notification.message = str(error)
                notification.type = "negative"
            finally:
                notification.spinner = False
                notification.timeout = 5
                query_button.enable()

        @ui.refreshable
        def dataset_list() -> None:
            with ui.column().classes("w-full gap-2"):
                if not state["datasets"]:
                    ui.label("请上传 CSV 或 XLSX").classes("text-sm text-slate-500 p-3")
                for item in state["datasets"]:
                    selected = item.dataset_id == state["selected_id"]
                    with ui.card().classes(
                        "w-full p-3 cursor-pointer border " +
                        ("border-blue-400 bg-blue-50" if selected else "border-slate-200")
                    ).on("click", lambda _, dataset_id=item.dataset_id: select_dataset(dataset_id)):
                        ui.label(item.name).classes("font-medium truncate")
                        ui.label(f"{item.row_count:,} 行 · {len(item.columns)} 列").classes("text-xs text-slate-500")

        @ui.refreshable
        def preview_panel() -> None:
            dataset = next((item for item in state["datasets"] if item.dataset_id == state["selected_id"]), None)
            if dataset is None:
                ui.label("暂无数据集").classes("text-slate-500 py-12 self-center")
                return
            ui.label(dataset.name).classes("font-semibold")
            ui.label("字段：" + "、".join(item["name"] for item in dataset.columns)).classes("text-xs text-slate-500")
            columns = [{"name": item["name"], "label": item["name"], "field": item["name"], "align": "left"} for item in dataset.columns]
            ui.table(columns=columns, rows=dataset.preview, pagination=10).props("flat bordered dense").classes("w-full")

        with ui.row().classes("hermes-shell no-wrap gap-0"):
            with ui.column().classes("hermes-sidebar hermes-glass h-full gap-0"):
                with ui.row().classes("items-center gap-2 px-5 py-4"):
                    ui.icon("hub", size="26px").classes("text-blue-600")
                    ui.label("Hermes 智维").classes("text-lg font-semibold")
                _render_workspace_navigation("data")
                ui.space()
                ui.label(f"{profile.username} · 企业版").classes("text-xs text-slate-500 px-5 py-4")

            with ui.column().classes("hermes-main flex-1 gap-0"):
                _render_mobile_navigation("data")
                with ui.row().classes("hermes-glass w-full items-center border-b border-slate-200 px-6 py-4"):
                    with ui.column().classes("gap-0"):
                        ui.label("数据分析工作台").classes("text-base font-semibold")
                        ui.label("CSV/XLSX 数据集、只读查询、表格与图表输出").classes("text-xs text-slate-500")
                    ui.space()
                    ui.badge("Python DataAgent", color="green").props("outline")
                with ui.row().classes("hermes-scroll flex-1 w-full gap-4 p-4 no-wrap"):
                    with ui.column().classes("hermes-panel w-[300px] shrink-0 p-4 overflow-auto"):
                        ui.upload(
                            label="上传数据集",
                            on_upload=upload_dataset,
                            auto_upload=True,
                            max_file_size=50_000_000,
                        ).props("accept=.csv,.xlsx flat bordered").classes("w-full")
                        ui.separator()
                        dataset_list()
                    with ui.column().classes("flex-1 min-w-0 gap-4 overflow-auto"):
                        with ui.column().classes("hermes-panel w-full p-4 overflow-auto max-h-[45%]"):
                            preview_panel()
                        with ui.column().classes("hermes-panel w-full p-4 flex-1 min-h-0"):
                            with ui.row().classes("w-full items-end gap-2"):
                                data_question = ui.input(
                                    "分析问题",
                                    placeholder="例如：汇总各地区销售额，并按从高到低排序",
                                ).props("outlined").classes("flex-1")
                                query_button = ui.button("分析", icon="analytics", on_click=query_dataset).props("unelevated color=green")
                            result_panel = ui.column().classes("w-full flex-1 overflow-auto gap-3")
                            with result_panel:
                                ui.label("输入问题开始分析").classes("text-slate-500 py-12 self-center")

    @ui.page("/admin")
    async def admin_workspace(request: Request) -> None:
        del request
        ui.add_css(page_css)
        visitor_id = nicegui_app.storage.user.get("visitor_id") or uuid4().hex
        nicegui_app.storage.user["visitor_id"] = visitor_id
        profile = await conversation_service.bootstrap_visitor(visitor_id)
        if not profile.named:
            _render_naming_page(conversation_service, visitor_id)
            return

        requires_key = bool(settings.admin_api_key)
        authorized = bool(nicegui_app.storage.user.get("admin_authorized")) or not requires_key
        if settings.environment == "production" and not settings.admin_api_key:
            _render_admin_locked("生产环境未配置 ADMIN_API_KEY，平台配置已锁定。")
            return
        if not authorized:
            async def unlock() -> None:
                candidate = key_input.value or ""
                if settings.admin_api_key and hmac.compare_digest(candidate, settings.admin_api_key):
                    nicegui_app.storage.user["admin_authorized"] = True
                    ui.navigate.reload()
                else:
                    ui.notify("管理密钥不正确", type="negative")

            with ui.column().classes("h-screen w-full items-center justify-center"):
                with ui.card().classes("hermes-glass w-full max-w-md p-8 gap-4"):
                    ui.icon("admin_panel_settings", size="44px").classes("text-blue-600")
                    ui.label("平台配置授权").classes("text-xl font-semibold")
                    key_input = ui.input("ADMIN_API_KEY", password=True, password_toggle_button=True).props("outlined autofocus").classes("w-full")
                    ui.button("解锁", icon="lock_open", on_click=unlock).props("unelevated").classes("w-full")
            return

        resource_types = {
            "agent": "智能体",
            "client": "模型客户端",
            "model": "模型",
            "provider": "供应商",
            "advisor": "顾问",
            "prompt": "提示词",
            "knowledge_base": "知识库配置",
            "mcp_server": "MCP 服务",
            "flow": "工作流",
            "schedule": "调度任务",
            "data_model": "数据模型",
        }
        state = {"resource_type": "agent", "records": await resource_service.list("agent")}

        async def select_resource_type(resource_type: str) -> None:
            state["resource_type"] = resource_type
            state["records"] = await resource_service.list(resource_type)
            type_menu.refresh()
            resource_table.refresh()

        async def save_resource() -> None:
            try:
                payload = json.loads(payload_input.value or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("配置 JSON 必须是对象")
                await resource_service.upsert(
                    state["resource_type"],
                    (resource_id_input.value or "").strip(),
                    name=name_input.value or "",
                    description=description_input.value or "",
                    payload=payload,
                    status=1 if enabled_input.value else 0,
                )
                edit_dialog.close()
                state["records"] = await resource_service.list(state["resource_type"])
                resource_table.refresh()
                ui.notify("配置已保存并生成新版本", type="positive")
            except Exception as error:
                ui.notify(str(error), type="negative")

        def open_editor(record=None) -> None:
            resource_id_input.value = record.resource_id if record else ""
            resource_id_input.set_enabled(record is None)
            name_input.value = record.name if record else ""
            description_input.value = record.description if record else ""
            payload_input.value = json.dumps(record.payload if record else {}, ensure_ascii=False, indent=2)
            enabled_input.value = bool(record.status) if record else True
            edit_dialog.open()

        async def toggle_resource(record) -> None:
            await resource_service.upsert(
                state["resource_type"],
                record.resource_id,
                name=record.name,
                description=record.description,
                payload=record.payload,
                status=0 if record.status else 1,
            )
            state["records"] = await resource_service.list(state["resource_type"])
            resource_table.refresh()

        with ui.dialog() as edit_dialog, ui.card().classes("w-[680px] max-w-[95vw] p-6 gap-4"):
            ui.label("编辑资源配置").classes("text-xl font-semibold")
            with ui.row().classes("w-full gap-3"):
                resource_id_input = ui.input("资源 ID").props("outlined").classes("flex-1")
                name_input = ui.input("名称").props("outlined").classes("flex-1")
            description_input = ui.input("描述").props("outlined").classes("w-full")
            payload_input = ui.textarea("配置 JSON").props("outlined autogrow input-style=font-family:monospace").classes("w-full")
            enabled_input = ui.switch("启用", value=True)
            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=edit_dialog.close).props("flat")
                ui.button("保存", icon="save", on_click=save_resource).props("unelevated")

        @ui.refreshable
        def type_menu() -> None:
            with ui.column().classes("w-full gap-1"):
                for key, label in resource_types.items():
                    button = ui.button(label, on_click=lambda resource_type=key: select_resource_type(resource_type)).props("flat no-caps align=left")
                    button.classes("w-full justify-start " + ("hermes-nav-active" if state["resource_type"] == key else "text-slate-600"))

        @ui.refreshable
        def resource_table() -> None:
            with ui.column().classes("w-full gap-2"):
                if not state["records"]:
                    ui.label("暂无配置").classes("text-slate-500 py-12 self-center")
                for record in state["records"]:
                    with ui.card().classes("w-full p-4 border border-slate-200"):
                        with ui.row().classes("w-full items-center no-wrap"):
                            with ui.column().classes("gap-0 min-w-0"):
                                ui.label(record.name).classes("font-medium")
                                ui.label(f"{record.resource_id} · v{record.version}").classes("text-xs text-slate-500")
                            ui.space()
                            ui.badge("启用" if record.status else "停用", color="positive" if record.status else "grey").props("outline")
                            ui.button(icon="edit", on_click=lambda _, item=record: open_editor(item)).props("flat round dense")
                            ui.button(
                                icon="toggle_off" if record.status else "toggle_on",
                                on_click=lambda _, item=record: toggle_resource(item),
                            ).props("flat round dense").tooltip("切换状态")
                        if record.description:
                            ui.label(record.description).classes("text-sm text-slate-600")
                        with ui.expansion("配置内容", icon="data_object").classes("w-full"):
                            ui.code(json.dumps(record.payload, ensure_ascii=False, indent=2), language="json").classes("w-full")

        with ui.row().classes("hermes-shell no-wrap gap-0"):
            with ui.column().classes("hermes-sidebar hermes-glass h-full gap-0"):
                with ui.row().classes("items-center gap-2 px-5 py-4"):
                    ui.icon("hub", size="26px").classes("text-blue-600")
                    ui.label("Hermes 智维").classes("text-lg font-semibold")
                _render_workspace_navigation("admin")
                ui.separator().classes("mx-3 my-2")
                type_menu()
                ui.space()
                ui.label(f"{profile.username} · Admin").classes("text-xs text-slate-500 px-5 py-4")
            with ui.column().classes("hermes-main flex-1 gap-0"):
                _render_mobile_navigation("admin")
                with ui.row().classes("hermes-glass w-full items-center border-b border-slate-200 px-6 py-4"):
                    with ui.column().classes("gap-0"):
                        ui.label("平台资源配置").classes("text-base font-semibold")
                        ui.label("统一管理智能体、模型、提示词、MCP、工作流和调度配置").classes("text-xs text-slate-500")
                    ui.space()
                    ui.button("新增配置", icon="add", on_click=lambda: open_editor()).props("unelevated")
                with ui.column().classes("hermes-scroll flex-1 w-full p-5"):
                    resource_table()

    ui.run_with(
        fastapi_app,
        storage_secret=settings.session_secret,
        title=settings.name,
        favicon="🤖",
    )


def _render_naming_page(service, visitor_id: str) -> None:
    from nicegui import ui

    async def submit() -> None:
        try:
            await service.name_visitor(visitor_id, username.value or "")
            ui.navigate.reload()
        except ValueError as error:
            ui.notify(str(error), type="warning")

    with ui.column().classes("h-screen w-full items-center justify-center"):
        with ui.card().classes("hermes-glass w-full max-w-md p-8 gap-5"):
            ui.icon("hub", size="44px").classes("text-blue-600")
            ui.label("欢迎来到 Hermes 智维").classes("text-2xl font-semibold")
            ui.label("面向企业知识、数据与流程协同的智能工作台").classes("text-slate-500")
            username = ui.input("你的名字").props("outlined autofocus").classes("w-full")
            ui.button("进入工作台", on_click=submit).props("unelevated").classes("w-full")


def _render_workspace_navigation(active: str) -> None:
    from nicegui import ui

    items = (
        ("chat", "AI 对话", "forum", "/"),
        ("knowledge", "企业知识库", "library_books", "/knowledge"),
        ("images", "图片生成", "image", "/images"),
        ("data", "数据分析", "analytics", "/data"),
        ("admin", "平台配置", "settings", "/admin"),
    )
    with ui.column().classes("w-full gap-1 px-3 pb-3"):
        for key, label, icon, target in items:
            button = ui.button(
                label,
                icon=icon,
                on_click=lambda path=target: ui.navigate.to(path),
            ).props("flat no-caps align=left")
            button.classes("w-full justify-start " + ("hermes-nav-active" if key == active else "text-slate-600"))


def _render_mobile_navigation(active: str) -> None:
    from nicegui import ui

    items = (
        ("chat", "AI 对话", "forum", "/"),
        ("knowledge", "企业知识库", "library_books", "/knowledge"),
        ("images", "图片生成", "image", "/images"),
        ("data", "数据分析", "analytics", "/data"),
        ("admin", "平台配置", "settings", "/admin"),
    )
    with ui.row().classes("hermes-mobile-nav hermes-glass w-full items-center justify-around border-b border-slate-200 px-1 py-2"):
        for key, label, icon, target in items:
            button = ui.button(
                icon=icon,
                on_click=lambda path=target: ui.navigate.to(path),
            ).props("flat round dense")
            button.classes("hermes-nav-active" if key == active else "text-slate-600").tooltip(label)


def _render_image_run(run) -> None:
    from nicegui import ui

    status_color = "positive" if run.status == "SUCCESS" else "negative" if run.status == "FAILED" else "warning"
    with ui.row().classes("w-full items-center"):
        ui.badge(run.status, color=status_color).props("outline")
        ui.label("参考图编辑" if run.mode == "edits" else "文生图").classes("text-xs text-slate-500")
        ui.space()
        ui.label(run.started_at.strftime("%Y-%m-%d %H:%M")).classes("text-xs text-slate-400")
    if run.error_message:
        ui.label(run.error_message).classes("text-sm text-red-600 whitespace-pre-wrap")
        return
    file_info = run.output.get("fileInfo", []) if isinstance(run.output, dict) else []
    urls = []
    for item in file_info if isinstance(file_info, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("domainUrl") or item.get("previewUrl") or item.get("ossUrl") or item.get("downloadUrl")
        if url:
            urls.append((str(url), str(item.get("downloadUrl") or item.get("ossUrl") or url)))
    if not urls:
        summary = run.output.get("data", "任务已完成，但没有可展示的图片地址。") if isinstance(run.output, dict) else ""
        ui.label(str(summary)).classes("text-sm text-slate-600 whitespace-pre-wrap")
        return
    with ui.row().classes("w-full gap-3 flex-wrap"):
        for preview_url, download_url in urls:
            with ui.card().classes("w-[260px] p-2"):
                ui.image(preview_url).classes("w-full aspect-square rounded-lg object-cover")
                ui.button(
                    "下载",
                    icon="download",
                    on_click=lambda url=download_url: ui.navigate.to(url, new_tab=True),
                ).props("flat no-caps").classes("w-full")


def _render_admin_locked(message: str) -> None:
    from nicegui import ui

    with ui.column().classes("h-screen w-full items-center justify-center"):
        with ui.card().classes("w-full max-w-lg p-8 items-center text-center"):
            ui.icon("lock", size="48px").classes("text-amber-600")
            ui.label("平台配置已锁定").classes("text-xl font-semibold")
            ui.label(message).classes("text-slate-600")
