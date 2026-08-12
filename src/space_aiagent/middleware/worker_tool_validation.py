"""
工具调用前置校验中间件

在工具执行前统一校验前置条件，避免每个工具函数重复样板检查。
当前校验项:
- bridge 注入：所有远程工具都需要 bridge 实例。失败时返回 ToolMessage（系统级错误，
  让 LLM 兜底回复）

未来可扩展: 参数校验、权限、限流、审计等
"""

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span, set_span_io
from space_aiagent.infrastructure.utils import message_util
from space_aiagent.models.response_schema import response_constants
from space_aiagent.models.response_schema.worker_response import ResponseCode, WorkerResponse
from space_aiagent.tools.scene_management import tools
from space_aiagent.workflow.execution_context import (
    StepAlreadyCompletedError,
    StepExecutionLimitError,
    step_execution_context_var,
)

logger = get_logger(__name__)

# LLM 偶尔把可选参数的「无值」输出成字符串化的 null 字面量（JSON 里没有 Python
# 的 None，LLM 会吐 "None" / "null" / ""），而不是 JSON null。统一归一化回
# Python None，让下游 args_to_camel(skip_none=True) 直接丢弃该参数，避免把
# 字符串 "None" 当真实值发给前端（如 sceneName="None" 导致前端查不到结果）。
_NULL_STRING_LITERALS = frozenset({"none", "null", ""})


def _normalize_null_args(args: Any) -> Any:
    """把字符串化的 null 字面量（"None"/"null"/""）归一化为 Python None。

    仅处理顶层 str 值；非 dict 原样返回。返回值始终是新 dict（不就地修改）。
    """
    if not isinstance(args, dict):
        return args
    return {
        key: (None if isinstance(val, str) and val.strip().lower() in _NULL_STRING_LITERALS else val)
        for key, val in args.items()
    }


class WorkerToolValidationMiddleware(AgentMiddleware):
    """Worker 工具权限、前置条件与执行循环保护。"""

    state_schema = AgentState
    # 不需要场景上下文的工具白名单
    # - create_scenario: 场景入口工具，本身用于建立场景上下文
    # - query_scenario: 查询场景信息，可确认当前是否已经打开场景，可建立场景上下文
    # - WorkerResponse: 结构化输出伪工具，非真实工具调用
    _SCENE_EXEMPT_TOOLS: ClassVar[set[str]] = {
        WorkerResponse.__name__,
        "read_file",
        tools.create_scenario.name,
        tools.query_scenario.name,
        tools.open_scenario.name,
    }

    def __init__(
        self,
        agent_name: str = "unknown",
    ) -> None:
        """初始化 Worker 中间件。"""
        super().__init__()
        self._agent_name = agent_name

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """记录单步骤 Worker 模型调用的输入、输出与耗时。"""
        thread_id = get_config().get("configurable", {}).get("thread_id", "")

        logger.info(
            "model call before ",
            agent=self._agent_name,
            thread_id=thread_id,
        )
        start_ts = time.perf_counter()
        with optional_span(
            "worker.llm",
            **{
                "agent.thread_id": thread_id,
                "worker.name": self._agent_name,
            },
        ) as span:
            set_span_io(span, input=message_util.serialize_messages(request.messages))
            response = await handler(request)
            span.set_attribute("llm.latency_ms", int((time.perf_counter() - start_ts) * 1000))
            set_span_io(span, output=message_util.serialize_model_response(response))
            return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> ToolMessage | Command[Any] | Any:
        tool_name = request.tool_call.get("name", "")
        tool_call_id = request.tool_call.get("id", "")
        thread_id = get_config().get("configurable", {}).get("thread_id", "")

        # 校验 0: 归一化字符串化的 null 字面量（"None"/"null"/"" → None）
        raw_args = request.tool_call.get("args", {})
        normalized_args = _normalize_null_args(raw_args)
        if normalized_args != raw_args:
            request = request.override(tool_call={**request.tool_call, "args": normalized_args})

        logger.info(
            "tool call before",
            agent=self._agent_name,
            thread_id=thread_id,
            tool_name=tool_name,
            args=request.tool_call.get("args", {}),
        )

        # V2 步骤执行上下文：工具权限由 ActionCatalog 决定，模型不能越权扩展动作；
        # 同时限制总调用数和相同参数的无进展循环。read_file 是 Skill 渐进加载工具，
        # 不属于领域动作，允许继续使用。
        execution_context = step_execution_context_var.get()
        execution_signature: str | None = None
        if execution_context is not None and tool_name not in {"read_file", WorkerResponse.__name__}:
            if tool_name not in execution_context.allowed_tools:
                logger.warning(
                    "workflow.tool_not_allowed",
                    run_id=execution_context.run_id,
                    step_id=execution_context.step_id,
                    tool_name=tool_name,
                )
                return ToolMessage(
                    content=json.dumps(
                        {
                            "success": False,
                            "code": "ACTION_TOOL_NOT_ALLOWED",
                            "message": f"当前步骤不允许调用工具 {tool_name}",
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                )

            execution_context.tool_call_count += 1
            if execution_context.tool_call_count > execution_context.max_tool_calls:
                raise StepExecutionLimitError(
                    f"步骤 {execution_context.step_id} 工具调用超过 {execution_context.max_tool_calls} 次"
                )
            execution_signature = execution_context.signature(tool_name, request.tool_call.get("args", {}))
            signature_count = execution_context.signature_counts.get(execution_signature, 0) + 1
            execution_context.signature_counts[execution_signature] = signature_count
            if execution_signature in execution_context.signature_results:
                raise StepAlreadyCompletedError(
                    tool_name,
                    execution_context.signature_results[execution_signature],
                )
            if signature_count > 2:
                raise StepExecutionLimitError(f"步骤 {execution_context.step_id} 相同工具和参数连续调用超过 2 次")

        # 校验 1: bridge 注入（所有远程工具都需要）
        if bridge_var.get() is None:
            logger.error("校验失败 bridge 未注入", tool_name=tool_name)
            return ToolMessage(
                content=json.dumps(
                    {"success": False, "message": "bridge 未注入，无法发送指令"},
                    ensure_ascii=False,
                ),
                tool_call_id=tool_call_id,
            )

        # 校验 2: 场景上下文（白名单外）→ 返回 Command(goto=END) 终止 Worker 图
        # ToolMessage 关闭 AI 的 tool_call（LLM API 协议要求），Command(goto=END)
        # 跳过 Worker 后续 LLM 调用。场景事实来自 WorkflowRun 的只读步骤投影。
        # Scheduler 已做前置条件判断；Worker 侧继续 fail-fast，形成纵深保护。
        if (
            execution_context is not None
            and tool_name not in self._SCENE_EXEMPT_TOOLS
            and not execution_context.scene_opened
        ):
            logger.warning("校验失败 无场景上下文", tool_name=tool_name)
            shortcut = response_constants.SHORTCUT_RESPONSES[ResponseCode.NO_SCENE]
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "success": False,
                                    "code": shortcut.code,
                                    "status": shortcut.status,
                                    "message": shortcut.summary,
                                },
                                ensure_ascii=False,
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
                goto=END,
            )

        # 未来扩展点: 参数校验、权限、限流等

        start_ts = time.perf_counter()
        with optional_span(
            f"tool.{tool_name}",
            **{
                "agent.thread_id": thread_id,
                "tool.name": tool_name,
                "worker.name": self._agent_name,
            },
        ) as span:
            set_span_io(span, input=request.tool_call.get("args", {}))
            try:
                result = await handler(request)
                if (
                    execution_context is not None
                    and execution_signature is not None
                    and tool_name in execution_context.completion_tools
                    and (payload := _extract_success_payload(result)) is not None
                ):
                    execution_context.signature_results[execution_signature] = payload
                span.set_attribute("tool.success", True)
                set_span_io(span, output=result)
                return result
            except Exception:
                span.set_attribute("tool.success", False)
                raise
            finally:
                span.set_attribute("tool.latency_ms", int((time.perf_counter() - start_ts) * 1000))


def _extract_success_payload(result: Any) -> dict[str, Any] | None:
    """从 dict/ToolMessage/Command 中提取成功工具结果，供 V2 完成调用去重。"""
    if isinstance(result, dict):
        return result if result.get("success") is True else None
    if isinstance(result, ToolMessage):
        messages = [result]
    elif isinstance(result, Command):
        update = result.update if isinstance(result.update, dict) else {}
        messages = update.get("messages", [])
    else:
        return None
    for message in messages:
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("success") is True:
            return payload
    return None
