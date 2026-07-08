"""
Agent 执行日志中间件

通过 AgentMiddleware 的 wrap_model_call / wrap_tool_call 钩子，
在 LLM 调用和工具执行层面记录详细日志，实现可观测性。

基于 deepagents 中间件模式：https://docs.langchain.com/oss/python/langchain/agents/middleware
"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.utils import collection_util, message_util, string_util

logger = get_logger(__name__)


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

        logger.debug(
            "LLM 调用",
            step=self.step_count,
            thread=self.thread_id,
            msg_count=len(request.messages),
            recent_messages=message_util.serialize_messages(
                collection_util.trim_list(request.messages, -3), content_max_len=300, args_max_len=300
            ),
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
                        "LLM 决定调用工具",
                        step=self.step_count,
                        tool_name=tc.get("name", "?"),
                        args=string_util.truncate(tc.get("args", {}), 200),
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
            "工具开始",
            step=self.tool_call_count,
            tool_name=tool_name,
            args=string_util.truncate(tool_args, 300),
        )

        result = await handler(request)

        logger.info(
            "工具完成",
            step=self.tool_call_count,
            tool_name=tool_name,
            result=string_util.truncate(result, 200),
        )

        return result
