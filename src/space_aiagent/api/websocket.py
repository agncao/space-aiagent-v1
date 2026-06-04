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

from space_aiagent.bridge import SessionManager
from space_aiagent.models.enums import WSMessageType
from space_aiagent.models.messages import (
    AIMessage,
    EndMessage,
    ErrorMessage,
    ToolResultMessage,
    UserInputMessage,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 全局会话管理器
session_manager = SessionManager()


@router.websocket("/ws/space")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket 主处理函数

    步骤:
    1. 接受连接
    2. 等待前端发送 user_input 获取 thread_id
    3. 注册到 SessionManager
    4. 进入消息循环:
       a. 收到 user_input → 调用 Orchestrator Agent
       b. Agent 执行过程中:
          - 生成 ai_message → 发送到前端
          - 调用工具 → 发送 tool_call 到前端
       c. 收到 tool_result → resolve bridge future → Agent 继续
       d. Agent 完成 → 发送 end
    5. 连接断开 → 清理

    TODO: 完整实现消息循环
    """
    await websocket.accept()
    logger.info("WebSocket 连接已建立")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == WSMessageType.USER_INPUT:
                # TODO: 处理用户输入
                # 1. 解析为 UserInputMessage
                # 2. 获取或创建 thread 的 Agent
                # 3. 执行 Agent，流式处理输出
                # 4. 发送 ai_message / tool_call / end
                pass

            elif msg_type == WSMessageType.TOOL_RESULT:
                # TODO: 处理工具执行结果
                # 1. 解析为 ToolResultMessage
                # 2. 通过 SessionManager 获取 bridge
                # 3. bridge.resolve_tool_result(result)
                pass

            else:
                logger.warning(f"未知消息类型: {msg_type}")

    except WebSocketDisconnect:
        logger.info("WebSocket 连接已断开")
        # TODO: 清理会话
    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
        try:
            error_msg = ErrorMessage(
                thread_id="",
                message=str(e),
            )
            await websocket.send_json(error_msg.model_dump())
        except Exception:
            pass
