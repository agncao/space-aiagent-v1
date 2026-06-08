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

from space_aiagent.agents.subagents import load_subagents
from space_aiagent.agents.orchestrator import create_orchestrator
from space_aiagent.bridge import SessionManager, bridge_var
from space_aiagent.infrastructure.database import get_db
from space_aiagent.models.enums import WSMessageType
from space_aiagent.models.messages import (
    ErrorMessage,
    ToolResultMessage,
    UserInputMessage,
)
from space_aiagent.skills import SkillLoader, SkillRegistry

logger = logging.getLogger(__name__)

router = APIRouter()

session_manager = SessionManager()

# Agent 实例缓存: thread_id -> compiled graph
_agent_cache: dict[str, object] = {}

# Skill 加载器（全局共享）
_registry: SkillRegistry | None = None
_skill_loader: SkillLoader | None = None

# 数据库 checkpointer（全局共享，SQLite 持久化）
_checkpointer = None


def _get_skill_loader() -> SkillLoader:
    """获取全局 SkillLoader（延迟初始化）"""
    global _registry, _skill_loader
    if _skill_loader is None:
        _registry = SkillRegistry()
        _registry.discover()
        _skill_loader = SkillLoader(_registry)
    return _skill_loader


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

    loader = _get_skill_loader()
    subagents = load_subagents(loader)
    checkpointer = await _get_checkpointer()
    agent = create_orchestrator(subagents, loader, checkpointer)
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
        """后台执行 agent，不阻塞消息接收循环"""
        token = bridge_var.set(bridge)
        try:
            agent = await _get_or_create_agent(user_msg.thread_id)
            logger.info("收到用户请求: %s",user_msg)
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_msg.content)]},
                config={"configurable": {"thread_id": user_msg.thread_id}},
            )

            messages = result.get("messages", [])
            for msg in reversed(messages):
                if hasattr(msg, "content") and msg.type == "ai":
                    await bridge.send_ai_message(msg.content)
                    break

            await bridge.send_end()

        except Exception as e:
            logger.exception("Agent 执行出错: thread_id=%s", user_msg.thread_id)
            await bridge.send_error(str(e))
        finally:
            bridge_var.reset(token)

    try:
        while True:
            raw = await websocket.receive_text()
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
