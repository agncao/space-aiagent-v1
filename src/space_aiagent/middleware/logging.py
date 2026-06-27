"""
Agent 执行日志中间件

通过 AgentMiddleware 的 wrap_model_call / wrap_tool_call 钩子，
在 LLM 调用和工具执行层面记录详细日志，实现可观测性。

基于 deepagents 中间件模式：https://docs.langchain.com/oss/python/langchain/agents/middleware
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from space_aiagent.infrastructure.utils import string_util

logger = logging.getLogger(__name__)

def _msg_preview(msg: BaseMessage, max_len: int = 120) -> dict:
    """提取消息预览信息"""
    content = str(getattr(msg, "content", ""))
    preview: dict[str, Any] = {
        "type": getattr(msg, "type", "?"),
        "content": content[:max_len] + ("..." if len(content) > max_len else ""),
    }
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        preview["tool_calls"] = [tc.get("name", "?") for tc in tool_calls]
    return preview


class LoggingMiddleware(AgentMiddleware):
    """记录 Agent 执行过程的中间件

    - awrap_model_call: 记录 LLM 输入上下文和工具调用决策
    - awrap_tool_call: 记录工具名称、参数和返回结果
    - 维护 step_count / tool_call_count 计数器
    """

    state_schema = AgentState

    def __init__(self, thread_id: str = "") -> None:
        super().__init__()
        self.thread_id = thread_id
        self.step_count = 0
        self.tool_call_count = 0

    # ---- 异步模型调用包装 ----

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        self.step_count += 1
        messages = request.messages
        last_msgs = messages[-3:] if len(messages) > 3 else messages

        logger.debug(
            "[步骤 %d] LLM 调用, thread=%s, 上下文 %d 条消息。最近消息: %s",
            self.step_count, self.thread_id, len(messages),
            [_msg_preview(m) for m in last_msgs],
        )

        response = await handler(request)

        # 解析 LLM 输出的工具调用决策
        if isinstance(response, ModelResponse):
            result_messages = response.result
        elif isinstance(response, AIMessage):
            result_messages = [response]
        else:
            result_messages = []

        for msg in result_messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    logger.info(
                        "[步骤 %d] LLM 决定调用工具: %s(%s)",
                        self.step_count,
                        tc.get("name", "?"),
                        string_util.truncate(tc.get("args", {}), 200),
                    )

        return response

    # ---- 异步工具调用包装 ----

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        self.tool_call_count += 1
        tool_name = request.tool_call.get("name", "?")
        tool_args = request.tool_call.get("args", {})

        logger.info(
            "[工具 %d] 开始: %s, 参数: %s",
            self.tool_call_count,
            tool_name,
            string_util.truncate(tool_args, 300),
        )

        result = await handler(request)

        logger.info(
            "[工具 %d] 完成: %s, 结果: %s",
            self.tool_call_count,
            tool_name,
            string_util.truncate(result, 200),
        )

        return result
