import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Tuple

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage

from space_aiagent.bridge.response_renderer import ResponseRenderer
from space_aiagent.bridge.response_shortcut import _SHORTCUT_RESPONSES
from space_aiagent.infrastructure.llm import build_flash_model
from space_aiagent.models.response_schema import response_util,response_constants
from langgraph.prebuilt.tool_node import ToolCallRequest
from space_aiagent.infrastructure.utils import string_util,message_util,collection_util
from space_aiagent.agents.subagents_util import resolve_subagent_type

logger = logging.getLogger(__name__)

orchestrator_task_streak_var: ContextVar[int] = ContextVar(
    "orchestrator_task_streak_var",
    default=0,
)


class PrimaryAgentMiddleware(AgentMiddleware):
    """主控 Agent 级运行时护栏 + 意图追踪与自动续接

    职责:
    1. TASK_LOOP_GUARD: 连续 task 死循环兜底（现有）
    2. 意图捕获: Orchestrator 返回 NO_SCENE 时，把用户原始意图写入
       AIMessage.additional_kwargs["pending_intent"]，由 checkpointer 跨轮持久化；
       同时若 task 历史中能找到 subagent_type，一并写入 pending_subagent
    3. 自动续接: 前置条件满足后（默认 SCENE_CREATED），若 messages 中存在
       pending_intent，按以下优先级解析 subagent_type 并委派 task：
       (a) captured_subagent（来自 task 历史，覆盖流程 2 主路径）
       (b) LLM 路由分类（流程 1 fallback：orchestrator 直接 NO_SCENE 未调 task）
    """

    # 用户确认短句——不视为原始意图（避免覆盖上一轮的真实意图）
    _CONFIRMATION_PHRASES = frozenset({
        "好的", "好", "好啊", "好吧", "行", "可以", "没问题", "确认",
        "ok", "okay", "yes", "是", "是的", "嗯", "嗯嗯", "继续",
    })

    def __init__(
        self,
        thread_id: str = "",
        task_loop_threshold: int = 20,
    ) -> None:
        self.thread_id = thread_id
        self._threshold = max(1, int(task_loop_threshold))
        self._model = build_flash_model()


    @staticmethod
    def _build_shortcut_response() -> ModelResponse:
        shortcut = _SHORTCUT_RESPONSES["task_loop_guard"]
        display = ResponseRenderer().render(shortcut)
        return message_util.build_primary_agent_response(display, shortcut, "call_primary_agent_guard")

    # ── 中间件钩子 ────────────────────────────────────────────

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:

        logger.debug(
            "[model call before] thread=%s, 上下文 %d 条消息。最近消息: %s",
            self.thread_id, len(request.messages),
            [message_util.msg_preview(m) for m in collection_util.trim_list(request.messages, -3)],
        )
        response = await handler(request)

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
                    "检测到 orchestrator 连续决策调用 task %d 次，改写为结构化短路响应",
                    streak,
                )
                return self._build_shortcut_response()

        # ── 职责 2: 意图捕获 ──
        code = response_util.getAgentResponseCodeFromModelResponse(response)
        if code and code in response_constants.INTENTION_TO_CATCH_CODES:
            existing_intent, existing_subagent, _ = message_util.extract_last_existing_intent(request.messages)
            intent, _ = message_util.extract_last_human_intent(
                request.messages,
                ignore_messages=list(self._CONFIRMATION_PHRASES),
            )
            # 优先沿用历史 pending_intent：避免本轮「好的」「创建测试场景」等
            # 确认/推进型输入覆盖上一轮真实意图（注意顺序：existing 在前）
            intent = existing_intent or intent
            if intent:
                subagent_name,_,_ = message_util.extract_last_task(request.messages)
                subagent_name = subagent_name or existing_subagent
                logger.info(
                    "[model call after][捕获意图]: %s, subagent: %s（触发 code=%s）",
                    intent, subagent_name, code,
                )
                for msg in response.result:
                    if not isinstance(msg, AIMessage) or not msg.tool_calls:
                        continue
                    if any(tc.get("name") == "AgentResponse" for tc in msg.tool_calls):
                        additional = msg.additional_kwargs or {}
                        additional["pending_intent"] = intent
                        if subagent_name:
                            additional["pending_subagent"] = subagent_name
                        msg.additional_kwargs = additional
                        logger.info("[model call after][捕获意图], 保存到状态: %s",msg)
                        break

        # ── 职责 3: 自动续接 ──
        if code and code in response_constants.INTENTION_RESUME_TRIGGER_CODES:
            pending, captured_subagent, _ = message_util.extract_last_existing_intent(request.messages)
            if pending:
                # 清除历史 messages 中的 pending_intent/pending_subagent，防止后续重复续接
                for msg in request.messages:
                    if isinstance(msg, AIMessage) and msg.additional_kwargs:
                        msg.additional_kwargs.pop("pending_intent", None)
                        msg.additional_kwargs.pop("pending_subagent", None)
                # 手动的恢复一下用户意图
                subagent_type = await resolve_subagent_type(pending, captured_subagent, self._model)
                return message_util.build_task_response(pending, subagent_type, "call_pending_intent_auto")

        for msg in response.result:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    logger.info(
                        "[model call after][决定调用工具]: %s, args: %s",
                        tc.get("name", "?"),
                        string_util.truncate(tc.get("args", {}), 200),
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
            "[tool call before] thread_id: %s, tool name: %s, 参数: %s",
            self.thread_id,
            tool_name,
            string_util.truncate(tool_args, 300),
        )

        result = await handler(request)

        logger.info(
            "[tool call after] thread_id: %s, tool name: %s, 结果: %s",
            self.thread_id,
            tool_name,
            string_util.truncate(result, 200),
        )

        return result
