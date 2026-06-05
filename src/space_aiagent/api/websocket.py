"""
WebSocket 端点

核心通信通道，处理前端与 Agent 的实时双向通信。

消息流:
1. 前端发送 user_input → 创建/获取 Agent → 执行 Agent → 发送响应
2. Agent 调用工具 → 发送 tool_call 到前端
3. 前端执行后发送 tool_result → 恢复 Agent 执行
4. Agent 完成 → 发送 ai_message + end

WebSocket 路径: /ws/space
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage

from space_aiagent.agents.subagents import load_subagents
from space_aiagent.agents.orchestrator import create_orchestrator
from space_aiagent.bridge import SessionManager, bridge_var
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


def _get_skill_loader() -> SkillLoader:
    """获取全局 SkillLoader（延迟初始化）"""
    global _registry, _skill_loader
    if _skill_loader is None:
        _registry = SkillRegistry()
        _registry.discover()
        _skill_loader = SkillLoader(_registry)
    return _skill_loader


def _get_or_create_agent(thread_id: str):
    """获取或创建指定 thread 的 Agent 实例"""
    if thread_id in _agent_cache:
        return _agent_cache[thread_id]

    loader = _get_skill_loader()
    subagents = load_subagents(loader)
    agent = create_orchestrator(subagents, loader)
    _agent_cache[thread_id] = agent
    return agent


@router.websocket("/ws/space")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket 主处理函数

    步骤:
    1. 接受连接
    2. 等待前端发送 user_input 获取 thread_id
    3. 注册到 SessionManager
    4. 进入消息循环:
       a. 收到 user_input → 注入 bridge → 调用 Agent
       b. 收到 tool_result → resolve bridge future → Agent 继续
    5. 连接断开 → 清理
    """
    await websocket.accept()
    logger.info("WebSocket 连接已建立")

    current_thread_id: str | None = None

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == WSMessageType.USER_INPUT:
                user_msg = UserInputMessage(**data)
                current_thread_id = user_msg.thread_id

                # 创建 bridge 并注入到 ContextVar
                bridge = session_manager.register(current_thread_id, websocket)
                token = bridge_var.set(bridge)

                try:
                    agent = _get_or_create_agent(current_thread_id)

                    # 调用 Agent
                    result = await agent.ainvoke(
                        {"messages": [HumanMessage(content=user_msg.content)]},
                        config={"configurable": {"thread_id": current_thread_id}},
                    )

                    # 提取最终 AI 回复
                    messages = result.get("messages", [])
                    # 找到最后一条 AI 消息
                    for msg in reversed(messages):
                        if hasattr(msg, "content") and msg.type == "ai":
                            await bridge.send_ai_message(msg.content)
                            break

                    await bridge.send_end()

                except Exception as e:
                    logger.exception("Agent 执行出错: thread_id=%s", current_thread_id)
                    await bridge.send_error(str(e))
                finally:
                    bridge_var.reset(token)

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
        if current_thread_id:
            bridge = session_manager.get_bridge(current_thread_id)
            if bridge:
                bridge.cleanup()
            session_manager.unregister(current_thread_id)
            logger.info("会话已清理: thread_id=%s", current_thread_id)
