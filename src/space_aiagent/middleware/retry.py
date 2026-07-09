"""失败恢复中间件（Phase 1B）

挂在 orchestrator 和子 Agent 链最内层（紧贴真实 LLM/工具调用）。
- LLM: 可重试异常(429/超时/5xx/连接)退避重试，耗尽/不可重试降级 LLM_UNAVAILABLE
- 工具: 仅 TimeoutError 退避重试，耗尽转 ToolMessage 给 LLM；
  其他异常原样冒泡（连接中断不处理，当前无重连机制）

可观测性：复用 optional_span 子 span + before_sleep 回调写 retry.* attribute。
observability.enabled=false 时 span 是 NoOp，set_attribute 无副作用，retry 仍正常工作。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from opentelemetry.trace.span import Span
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from space_aiagent.infrastructure.config import RetryConfig
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span
from space_aiagent.infrastructure.utils import message_util
from space_aiagent.models.response_schema import response_constants, response_util

logger = get_logger(__name__)

# LLM 可重试异常基础集合（retry_on_parse_error=true 时追加 ValidationError）
_BASE_RETRYABLE_LLM_ERRORS: tuple[type[Exception], ...] = (
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


def _build_retryable_llm_errors(retry_on_parse_error: bool) -> tuple[type[Exception], ...]:
    if retry_on_parse_error:
        return (*_BASE_RETRYABLE_LLM_ERRORS, ValidationError)
    return _BASE_RETRYABLE_LLM_ERRORS


def _make_before_sleep(span: Span) -> Callable[[Any], None]:
    """构造 before_sleep 回调：写 retry.* 到 span + warning 日志"""

    def _log(retry_state: Any) -> None:
        attempt = getattr(retry_state, "attempt_number", 0)
        span.set_attribute("retry.attempt", attempt)
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is not None:
            span.set_attribute("retry.last_error", type(exc).__name__)
        logger.warning("重试", attempt=attempt, error=type(exc).__name__ if exc else None)

    return _log


class RetryMiddleware(AgentMiddleware):
    """LLM/工具调用重试 + 降级"""

    def __init__(self, config: RetryConfig) -> None:
        self._config = config
        self._retryable_llm_errors = _build_retryable_llm_errors(config.llm.retry_on_parse_error)

    def _degrade_llm(self) -> ModelResponse:
        """复用 task_loop_guard 改写模式：构造 LLM_UNAVAILABLE 降级 ModelResponse"""
        shortcut = response_constants.SHORTCUT_RESPONSES["llm_unavailable"]
        display = response_util.render(shortcut)
        return message_util.build_primary_agent_response(display, shortcut, "call_llm_unavailable")

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if not self._config.enabled:
            return await handler(request)

        with optional_span("llm.retry") as span:
            retrying = AsyncRetrying(
                stop=stop_after_attempt(self._config.llm.max_attempts),
                wait=wait_exponential_jitter(initial=self._config.llm.base_delay, max=self._config.llm.max_delay),
                retry=retry_if_exception_type(self._retryable_llm_errors),
                before_sleep=_make_before_sleep(span),
            )
            try:
                return await retrying(handler, request)
            except RetryError:
                span.set_attribute("retry.outcome", "exhausted")
                logger.warning("LLM 重试耗尽，降级 LLM_UNAVAILABLE")
                return self._degrade_llm()
            except (openai.APIError, ValidationError) as e:
                # 不可重试异常：tenacity 不重试直接抛（不包装成 RetryError）
                span.set_attribute("retry.outcome", "non_retryable")
                span.set_attribute("retry.error", type(e).__name__)
                logger.warning("LLM 不可重试异常，降级 LLM_UNAVAILABLE", error=type(e).__name__)
                return self._degrade_llm()

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        if not self._config.enabled:
            return await handler(request)

        with optional_span("tool.retry") as span:
            retrying = AsyncRetrying(
                stop=stop_after_attempt(self._config.tool.max_attempts),
                wait=wait_exponential_jitter(initial=self._config.tool.base_delay, max=self._config.tool.max_delay),
                retry=retry_if_exception_type(TimeoutError),
                before_sleep=_make_before_sleep(span),
            )
            try:
                return await retrying(handler, request)
            except RetryError:
                # TimeoutError 重试耗尽 → 转 ToolMessage 给 LLM 消化（不短路，不新增 code）
                span.set_attribute("retry.outcome", "exhausted")
                tool_call_id = request.tool_call.get("id", "")
                logger.warning("工具超时重试耗尽，转 ToolMessage", tool_call_id=tool_call_id)
                return ToolMessage(
                    content=json.dumps(
                        {"success": False, "message": "工具调用超时，请稍后重试"},
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                )
            # 非 TimeoutError 异常 tenacity 不重试、原样 reraise，不在此 catch，冒泡到 websocket handler
