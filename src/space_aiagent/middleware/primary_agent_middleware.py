import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span, set_span_io
from space_aiagent.infrastructure.utils import collection_util, message_util, string_util
from space_aiagent.models.response_schema import response_constants, response_util
from space_aiagent.models.response_schema.agent_struct_response import ResponseCode

logger = get_logger(__name__)

orchestrator_task_streak_var: ContextVar[int] = ContextVar(
    "orchestrator_task_streak_var",
    default=0,
)


class PrimaryAgentMiddleware(AgentMiddleware):
    """主控 Agent 级运行时护栏 + 意图追踪与自动续接

    职责:
    1. TASK_LOOP_GUARD: 连续 task 死循环兜底（现有）
    """


    def __init__(
        self,
        thread_id: str = "",
        task_loop_threshold: int = 20,
    ) -> None:
        self.thread_id = thread_id
        self._threshold = max(1, int(task_loop_threshold))

    @staticmethod
    def _build_shortcut_response() -> ModelResponse:
        shortcut = response_constants.SHORTCUT_RESPONSES[ResponseCode.TASK_LOOP_GUARD]
        return ModelResponse(
            result=[AIMessage(content=shortcut.summary)],
            structured_response=shortcut,
        )

    # ── 中间件钩子 ────────────────────────────────────────────

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:

        logger.debug(
            "model call before",
            thread_id=self.thread_id,
            msg_count=len(request.messages),
            recent_messages=message_util.serialize_messages(
                collection_util.trim_list(request.messages, -3), content_max_len=300, args_max_len=300
            ),
        )

        start_ts = time.perf_counter()
        with optional_span("orchestrator.llm", **{"agent.thread_id": self.thread_id}) as span:
            set_span_io(span, input=message_util.serialize_messages(request.messages))
            response = await handler(request)
            latency_ms = int((time.perf_counter() - start_ts) * 1000)
            span.set_attribute("llm.latency_ms", latency_ms)
            code = response_util.parse_code_by_model_response(response)
            if code:
                span.set_attribute("response.code", code)
            set_span_io(span, output=message_util.serialize_model_response(response))

        # ── 职责 1: TASK_LOOP_GUARD ──
        tool_calls = message_util.extract_tool_calls(response)
        task_call_count = sum(1 for tc in tool_calls if tc.get("name") == "task")

        if task_call_count == 0:
            orchestrator_task_streak_var.set(0)
        else:
            streak = orchestrator_task_streak_var.get() + task_call_count
            orchestrator_task_streak_var.set(streak)
            if streak >= self._threshold:
                logger.warning(
                    "检测到 orchestrator 连续决策调用 task，改写为结构化短路响应",
                    streak=streak,
                )
                return self._build_shortcut_response()

        for msg in response.result:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    logger.info(
                        "model call after 决定调用工具",
                        tool_name=tc.get("name", "?"),
                        args=string_util.truncate(tc.get("args", {}), 200),
                    )

        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        tool_name = request.tool_call.get("name", "?")
        tool_args = request.tool_call.get("args", {})

        logger.info(
            "tool call before",
            thread_id=self.thread_id,
            tool_name=tool_name,
            args=string_util.truncate(tool_args, 300),
        )

        start_ts = time.perf_counter()
        span_name = "orchestrator.task" if tool_name == "task" else f"orchestrator.tool.{tool_name}"
        subagent_type = tool_args.get("subagent_type", "") if tool_name == "task" else ""
        result = None
        with optional_span(
            span_name,
            **{
                "agent.thread_id": self.thread_id,
                "tool.name": tool_name,
                **({"subagent.name": subagent_type} if subagent_type else {}),
            },
        ) as span:
            set_span_io(span, input=tool_args)
            try:
                result = await handler(request)
                span.set_attribute("tool.success", True)
                set_span_io(span, output=result)
                return result
            except Exception as ex:
                span.set_attribute("tool.success", False)
                logger.exception("主智能体 wrap_tool_call 异常", thread_id=self.thread_id,tool_name=tool_name)
                raise ex
            finally:
                latency_ms = int((time.perf_counter() - start_ts) * 1000)
                span.set_attribute("tool.latency_ms", latency_ms)
