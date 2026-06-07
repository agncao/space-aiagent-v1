"""
WebSocket 远程工具桥接

核心机制:
1. 工具函数调用 send_tool_call() 发送指令到前端
2. 创建一个 asyncio.Future 并缓存（key = tool_call_id）
3. 当 WebSocket 收到前端的 tool_result 时，调用 resolve_tool_result()
4. resolve_tool_result() 根据 tool_call_id 找到 Future，设置结果
5. 工具函数的 await future 返回结果

时序:
    Agent调用工具 → send_tool_call() → WebSocket发送指令 → 前端执行
                                                                  ↓
    Agent得到结果 ← await future ← resolve_tool_result() ← WebSocket收到结果
"""

import asyncio
import logging
import uuid

from fastapi import WebSocket

from space_aiagent.models.messages import (
    AIMessage,
    EndMessage,
    ErrorMessage,
    ToolCallMessage,
    ToolResultMessage,
)

logger = logging.getLogger(__name__)


class WSBridge:
    """WebSocket 远程工具桥接"""

    def __init__(self, websocket: WebSocket, thread_id: str) -> None:
        self._ws = websocket
        self._thread_id = thread_id
        # tool_call_id -> Future
        self._pending: dict[str, asyncio.Future] = {}
        # 默认超时时间（秒）
        self._timeout = 60

    async def send_tool_call(
        self,
        tool_func: str,
        args: dict,
        timeout: float = 60,
    ) -> dict:
        """
        发送工具调用指令到前端，等待执行结果

        Args:
            tool_func: 前端工具函数名（如 "createScenario"）
            args: 工具参数
            timeout: 等待超时时间（秒）

        Returns:
            前端执行结果 dict，格式: {"success": bool, "message": str, "data": ...}
        """
        # 生成唯一调用 ID，绑定到 Future
        tool_call_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[tool_call_id] = future

        # 构建 ToolCallMessage 并发送到前端
        message = ToolCallMessage(
            thread_id=self._thread_id,
            tool_func=tool_func,
            tool_func_args=args,
            tool_call_id=tool_call_id,
        )
        await self._ws.send_json(message.model_dump())
        logger.debug("发送 tool_call: %s(%s), id=%s, thread_id=%s", tool_func, args, tool_call_id, self._thread_id)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.debug("收到 tool_result: id=%s, success=%s", tool_call_id, result.get("success"))
            return result
        except TimeoutError:
            self._pending.pop(tool_call_id, None)
            logger.warning("tool_call 超时: %s, id=%s", tool_func, tool_call_id)
            return {"success": False, "message": f"工具调用超时: {tool_func}"}

    def resolve_tool_result(self, result: ToolResultMessage) -> None:
        """
        解析前端返回的工具执行结果

        由 WebSocket handler 在收到 tool_result 消息时调用，
        根据 tool_call_id 找到对应的 Future 并 resolve。
        """
        tool_call_id = result.tool_call_id
        future = self._pending.pop(tool_call_id, None)
        if future and not future.done():
            future.set_result(
                {
                    "success": result.success,
                    "message": result.message,
                    "data": result.data,
                }
            )
        else:
            logger.warning("收到未知 tool_result: id=%s", tool_call_id)

    async def send_ai_message(self, content: str) -> None:
        """发送 AI 文本消息到前端"""
        message = AIMessage(
            thread_id=self._thread_id,
            content=content,
        )
        await self._ws.send_json(message.model_dump())

    async def send_end(self) -> None:
        """发送对话轮次结束信号"""
        message = EndMessage(thread_id=self._thread_id)
        await self._ws.send_json(message.model_dump())

    async def send_error(self, message: str) -> None:
        """发送错误消息"""
        msg = ErrorMessage(
            thread_id=self._thread_id,
            message=message,
        )
        await self._ws.send_json(msg.model_dump())

    def cleanup(self) -> None:
        """
        清理所有 pending futures

        在 WebSocket 断开连接时调用，对所有未完成的 Future 设置异常。
        """
        for _, future in self._pending.items():
            if not future.done():
                future.set_exception(ConnectionError("WebSocket 连接已断开"))
        count = len(self._pending)
        self._pending.clear()
        logger.debug("bridge cleanup: 已清理 %d 个 pending futures", count)
