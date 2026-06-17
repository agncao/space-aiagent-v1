"""
WebSocket 端点

核心通信通道，处理前端与 Agent 的实时双向通信。

消息流:
1. 前端发送 user_input → 创建/获取 Agent → 执行 Agent → 发送响应
2. Agent 调用工具 → 发送 tool_call 到前端
3. 前端执行后发送 tool_result → 恢复 Agent 执行
4. Agent 完成 → 发送 ai_message + end

WebSocket 路径: /ws/space

会话持久化:
使用 AsyncSqliteSaver（基于 SQLite）持久化 LangGraph checkpoint，
确保跨轮次会话记忆不丢失。与 MemorySaver 不同，SQLite 持久化不受
进程重启、热重载影响。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage

from space_aiagent.agents.orchestrator import create_orchestrator
from space_aiagent.agents.subagents import load_subagents
from space_aiagent.bridge import SessionManager, bridge_var, current_scene_name_var
from space_aiagent.bridge.response_renderer import ResponseRenderer, normalize
from space_aiagent.infrastructure.database import get_db
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.enums import WSMessageType
from space_aiagent.models.messages import (
    ErrorMessage,
    ToolResultMessage,
    UserInputMessage,
)
from space_aiagent.models.response_schema import AgentResponse

logger = logging.getLogger(__name__)

router = APIRouter()

session_manager = SessionManager()

# 工具名 → 用户可见的中文名称
_TOOL_DISPLAY: dict[str, str] = {
    "task": "正在分析并处理您的请求",
    "write_todos": "正在规划任务",
    "ls": "正在读取文件列表",
    "read_file": "正在读取文件",
    "write_file": "正在写入文件",
    "edit_file": "正在编辑文件",
    "glob": "正在搜索文件",
    "grep": "正在搜索代码",
}

# 子 Agent 类型 → 中文标签
_SUBTASK_LABELS: dict[str, str] = {
    "scene-agent": "正在调用场景管理",
    "entity-agent": "正在调用实体管理",
}

# 死循环兜底阈值：同一轮内 task 工具连续调用达到此值视为死循环，强制中断
# (A 方案 prompt 约束 orchestrator 不重复 task，B 方案在 LLM 偶发不遵守时硬兜底)
LOOP_THRESHOLD = 2


def _make_progress_message(tool_name: str, tool_input: dict) -> str | None:
    """根据工具名和参数生成用户可见的进度提示，无意义时返回 None"""
    pass
    # 暂时不用，如果需要把以下注解放开
    # # task 工具：提取子 Agent 类型，生成有意义的提示
    # if tool_name == "task":
    #     subagent_type = tool_input.get("subagent_type", "")
    #     description = tool_input.get("description", "")
    #     label = _SUBTASK_LABELS.get(subagent_type, "处理任务")
    #     if description:
    #         return f"{label}：{description[:80]}"
    #     return f"{label}..."
    #
    # return _TOOL_DISPLAY.get(tool_name)


# Agent 实例缓存: thread_id -> compiled graph
_agent_cache: dict[str, object] = {}

# 响应渲染器（全局共享）
_renderer: ResponseRenderer | None = None


def _get_renderer() -> ResponseRenderer:
    """获取全局 ResponseRenderer（延迟初始化）"""
    global _renderer
    if _renderer is None:
        _renderer = ResponseRenderer()
    return _renderer


# Skill 加载器（全局共享）
# 数据库 checkpointer（全局共享，SQLite 持久化）
_checkpointer = None


async def _get_checkpointer():
    """获取或初始化全局 AsyncSqliteSaver checkpointer（延迟初始化）"""
    global _checkpointer
    if _checkpointer is None:
        db = await get_db()
        _checkpointer = await db.get_checkpointer()
        logger.info("AsyncSqliteSaver checkpointer 已初始化（SQLite 持久化）")
    return _checkpointer


async def _get_or_create_agent(thread_id: str):
    """获取或创建指定 thread 的 Agent 实例"""
    if thread_id in _agent_cache:
        return _agent_cache[thread_id]

    subagents = load_subagents()
    checkpointer = await _get_checkpointer()
    agent = create_orchestrator(subagents, checkpointer, thread_id=thread_id)
    _agent_cache[thread_id] = agent
    logger.info("Agent 实例已创建: thread_id=%s", thread_id)
    return agent


@router.websocket("/ws/space")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket 主处理函数

    设计要点：agent 执行在后台 asyncio.Task 中进行，主循环只负责接收消息，
    避免 agent 等待 tool_result 时阻塞 receive_text() 造成死锁。

    消息流:
    1. 前端发送 user_input → 主循环收到后启动后台 agent_task
    2. agent 调用工具 → bridge.send_tool_call() → ws.send_json() 发送到前端
    3. 前端执行后发 tool_result → 主循环收到 → bridge.resolve_tool_result()
    4. agent_task 拿到结果继续执行 → 发送 ai_message + end 到前端
    """
    await websocket.accept()
    logger.info("WebSocket 连接已建立")

    current_thread_id: str | None = None
    agent_tasks: set[asyncio.Task] = set()

    async def run_agent(bridge, user_msg: UserInputMessage) -> None:
        """后台执行 agent（流式），不阻塞消息接收循环"""
        bridge_token = bridge_var.set(bridge)
        scene_token = current_scene_name_var.set(user_msg.current_scene_name)
        try:
            agent = await _get_or_create_agent(user_msg.thread_id)
            structured_response: AgentResponse | None = None

            # 循环检测：同一轮内 task 工具连续调用计数器
            task_call_count = 0

            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=user_msg.content)]},
                config={
                    "configurable": {"thread_id": user_msg.thread_id},
                    "recursion_limit": 100,
                },
                version="v2",
            ):
                kind = event["event"]
                name = event.get("name", "")
                data = event.get("data", {})

                if kind == "on_tool_start":
                    if name == "AgentResponse":
                        continue

                    # 循环检测：orchestrator 受 prompt 约束不应连续调用 task（A 方案），
                    # 偶发不遵守时这里硬兜底，避免 astream_events 死循环
                    if name == "task":
                        task_call_count += 1
                        if task_call_count >= LOOP_THRESHOLD:
                            logger.warning(
                                "检测到 task 连续调用 %d 次，疑似死循环，强制中断: thread_id=%s",
                                task_call_count,
                                user_msg.thread_id,
                            )
                            await bridge.send_ai_message(
                                "我多次尝试处理您的请求但似乎卡住了。"
                                "请提供更具体的信息，例如：要修改的实体名称、目标属性等。"
                            )
                            await bridge.send_end()
                            return

                    # 生成有意义的进度提示
                    tool_input = data.get("input", {})
                    display = _make_progress_message(name, tool_input)
                    if display:
                        logger.info("发送进度提示(send_ai_message): %s, %s", kind, display)
                        await bridge.send_ai_message(display)

                elif kind == "on_chat_model_end":
                    # ToolStrategy(AgentResponse) 在 model node 内部被 _handle_model_output
                    # 拦截，不会走 ToolNode，因此 on_tool_end 收不到。需要从模型输出的
                    # AIMessage.tool_calls 中提取。
                    output = data.get("output")
                    if hasattr(output, "tool_calls") and output.tool_calls:
                        for tc in output.tool_calls:
                            if tc.get("name") == "AgentResponse":
                                try:
                                    structured_response = normalize(AgentResponse(**tc["args"]))
                                    logger.info(
                                        "AgentResponse: status=%s, summary=%s",
                                        structured_response.status,
                                        string_util.truncate(structured_response.summary, 100),
                                    )
                                except Exception as e:
                                    logger.warning("解析 AgentResponse 失败: %s", e)

                elif kind == "on_tool_end":
                    if name == "AgentResponse":
                        output = data.get("output")
                        if isinstance(output, AgentResponse):
                            structured_response = normalize(output)
                        elif isinstance(output, dict):
                            structured_response = normalize(AgentResponse(**output))
                        logger.info(
                            "AgentResponse: status=%s, summary=%s",
                            structured_response.status if structured_response else "?",
                            string_util.truncate(structured_response.summary if structured_response else "", 100),
                        )

            # 渲染并发送最终回复
            if structured_response is not None:
                renderer = _get_renderer()
                content = renderer.render(structured_response)
                await bridge.send_ai_message(content)
            else:
                logger.warning("未获取到 structured_response")
                await bridge.send_ai_message("处理完成。")

            await bridge.send_end()

        except Exception as e:
            logger.exception("Agent 执行出错: thread_id=%s", user_msg.thread_id)
            await bridge.send_error(str(e))
        finally:
            bridge_var.reset(bridge_token)
            current_scene_name_var.reset(scene_token)

    try:
        while True:
            raw = await websocket.receive_text()
            logger.info("收到用户请求: %s", raw)
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == WSMessageType.USER_INPUT:
                user_msg = UserInputMessage(**data)
                current_thread_id = user_msg.thread_id

                bridge = session_manager.register(current_thread_id, websocket)

                # 后台执行 agent，不阻塞 receive 循环
                task = asyncio.create_task(run_agent(bridge, user_msg))
                agent_tasks.add(task)
                task.add_done_callback(agent_tasks.discard)

            elif msg_type == WSMessageType.TOOL_RESULT:
                tool_result = ToolResultMessage(**data)
                bridge = session_manager.get_bridge(tool_result.thread_id)
                if bridge:
                    bridge.resolve_tool_result(tool_result)
                else:
                    logger.warning("收到 tool_result 但无对应 bridge: thread_id=%s", tool_result.thread_id)

            else:
                logger.warning("未知消息类型: %s", msg_type)

    except WebSocketDisconnect:
        logger.info("WebSocket 连接已断开: thread_id=%s", current_thread_id)
    except Exception as e:
        logger.exception("WebSocket 错误: %s", e)
        try:
            error_msg = ErrorMessage(
                thread_id=current_thread_id or "",
                message=str(e),
            )
            await websocket.send_json(error_msg.model_dump())
        except Exception:
            pass
    finally:
        # 等待所有后台 agent 任务完成
        for task in agent_tasks:
            task.cancel()
        if agent_tasks:
            await asyncio.gather(*agent_tasks, return_exceptions=True)

        if current_thread_id:
            bridge = session_manager.get_bridge(current_thread_id)
            if bridge:
                bridge.cleanup()
            session_manager.unregister(current_thread_id)
            logger.info("会话已清理: thread_id=%s", current_thread_id)
