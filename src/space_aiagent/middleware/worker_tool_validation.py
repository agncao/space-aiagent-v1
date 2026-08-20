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
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span, set_span_io
from space_aiagent.infrastructure.utils import message_util
from space_aiagent.models.response_schema.worker_response import WorkerResponse
from space_aiagent.tools.contracts import get_workflow_tool_contract
from space_aiagent.workflow.execution_context import (
    StepAlreadyCompletedError,
    StepExecutionLimitError,
    StepNoSceneError,
    step_execution_context_var,
)

logger = get_logger(__name__)


class WorkerToolValidationMiddleware(AgentMiddleware):
    """Worker 工具权限、前置条件与执行循环保护。"""

    state_schema = AgentState
    # Worker 的结构化返回与 Skill 加载工具不属于领域工具契约。
    _EXEMPT_TOOLS: ClassVar[set[str]] = {
        WorkerResponse.__name__,
        "read_file",
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

        logger.info(
            "tool call before",
            agent=self._agent_name,
            thread_id=thread_id,
            tool_name=tool_name,
            args=request.tool_call.get("args", {}),
        )

        # V2 Todo 执行上下文：工具权限来自 workers.yaml，工具自身契约负责声明
        # requires/effects；这里同时限制总调用数和相同参数的无进展循环。
        execution_context = step_execution_context_var.get()
        execution_signature: str | None = None
        request_tool = getattr(request, "tool", None)
        tool_contract = get_workflow_tool_contract(request_tool) if request_tool is not None else None
        if execution_context is not None and tool_name not in self._EXEMPT_TOOLS:
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

            # 缺场景是确定性用户前置条件：直接短路终止步骤，提示用户打开或新建场景，
            # 不与 LLM 协商 requirement（避免引擎自动插入建场景步骤替用户做选择）。
            if (
                tool_contract is not None
                and "scene.opened" in tool_contract.requires
                and "scene.opened" not in execution_context.facts
            ):
                logger.warning(
                    "workflow.no_scene_shortcut",
                    run_id=execution_context.run_id,
                    step_id=execution_context.step_id,
                    tool_name=tool_name,
                )
                raise StepNoSceneError(tool_name)

            missing_facts = sorted((tool_contract.requires if tool_contract else set()) - execution_context.facts)
            if missing_facts:
                for fact in missing_facts:
                    execution_context.missing_requirements[fact] = {
                        "key": fact,
                        "description": f"执行 {tool_name} 前需要先满足 {fact}",
                        "context": {"tool_name": tool_name, "worker": self._agent_name},
                    }
                return ToolMessage(
                    content=json.dumps(
                        {
                            "success": False,
                            "code": "REQUIREMENT_UNSATISFIED",
                            "message": "当前 Todo 缺少跨 Worker 前置条件",
                            "requirements": [execution_context.missing_requirements[fact] for fact in missing_facts],
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
                    and (payload := _extract_success_payload(result)) is not None
                ):
                    execution_context.signature_results[execution_signature] = payload
                    execution_context.successful_tool_names.append(tool_name)
                    if tool_contract is not None:
                        execution_context.effects.update(tool_contract.effects)
                        execution_context.invalidates.update(tool_contract.invalidates)
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
