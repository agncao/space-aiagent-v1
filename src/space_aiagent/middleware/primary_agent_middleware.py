import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage

from space_aiagent.bridge.response_renderer import ResponseRenderer
from space_aiagent.bridge.response_shortcut import _SHORTCUT_RESPONSES
from space_aiagent.models.response_schema import ResponseCode

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
       AIMessage.additional_kwargs["pending_intent"]，由 checkpointer 跨轮持久化
    3. 自动续接: 前置条件满足后（默认 SCENE_CREATED），若 messages 中存在
       pending_intent，直接委派 task 到 entity-agent 执行原始意图，
       无需用户重复输入
    """

    state_schema = AgentState

    # 前置条件不满足的 code，触发意图捕获
    _PRECONDITION_BLOCKED_CODES = frozenset({ResponseCode.NO_SCENE})

    # 前置条件已满足的 code，触发自动续接（默认值，可构造时覆盖）
    _DEFAULT_PRECONDITION_MET_CODES = frozenset({ResponseCode.SCENE_CREATED})

    def __init__(
        self,
        task_loop_threshold: int = 20,
        precondition_met_codes: frozenset[ResponseCode] | None = None,
    ) -> None:
        self._threshold = max(1, int(task_loop_threshold))
        self._precondition_met_codes = (
            precondition_met_codes if precondition_met_codes is not None else self._DEFAULT_PRECONDITION_MET_CODES
        )

    # ── 静态工具方法 ──────────────────────────────────────────

    @staticmethod
    def _extract_tool_calls(response: ModelResponse) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        for msg in response.result:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                tool_calls.extend(msg.tool_calls)
        return tool_calls

    @staticmethod
    def _extract_agent_response_code(response: ModelResponse) -> str | None:
        """从 ModelResponse 中提取 AgentResponse tool_call 的 code 字段"""
        for msg in response.result:
            if not isinstance(msg, AIMessage) or not msg.tool_calls:
                continue
            for tc in msg.tool_calls:
                if tc.get("name") != "AgentResponse":
                    continue
                args = tc.get("args", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        continue
                return args.get("code")
        return None

    @staticmethod
    def _extract_original_intent(messages: list) -> str | None:
        """从 messages 中提取用户原始意图：返回最新的非空 HumanMessage content

        说明：不做 ACK 跳过判断。正常路径下 NO_SCENE 触发时 messages 最新就是
        用户原始意图；异常路径（LLM 在用户 ACK 后再次输出 NO_SCENE）应通过
        prompt 修复 LLM 行为，而非后端做 ACK 适配。
        """
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = str(msg.content).strip()
                if content:
                    return content
        return None

    @staticmethod
    def _extract_pending_intent(messages: list) -> str | None:
        """从 messages 中扫描最近的 pending_intent（写在 AIMessage.additional_kwargs）"""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                additional = msg.additional_kwargs or {}
                pending = additional.get("pending_intent")
                if pending:
                    return pending
        return None

    @staticmethod
    def _build_shortcut_response() -> ModelResponse:
        shortcut = _SHORTCUT_RESPONSES["task_loop_guard"]
        display = ResponseRenderer().render(shortcut)
        return ModelResponse(
            result=[
                AIMessage(
                    content=display,
                    tool_calls=[
                        {
                            "name": "AgentResponse",
                            "args": shortcut.model_dump(mode="json"),
                            "id": "call_primary_agent_guard",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            structured_response=shortcut,
        )

    @staticmethod
    def _build_auto_continue_response(intent: str) -> ModelResponse:
        """构建自动续接响应：将 LLM 输出替换为 task 调用，委派给 entity-agent

        不设置 structured_response —— 这不是 AgentResponse，chain 会继续执行 task，
        task 完成后 orchestrator 会再次调用 LLM 生成最终 AgentResponse。
        """
        logger.info("自动续接原始意图: %s", intent)
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": intent,
                                "subagent_type": "entity-agent",
                            },
                            "id": "call_pending_intent_auto",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
        )

    # ── 中间件钩子 ────────────────────────────────────────────

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)

        # ── 职责 1: TASK_LOOP_GUARD ──
        tool_calls = self._extract_tool_calls(response)
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
        code = self._extract_agent_response_code(response)
        if code and code in self._PRECONDITION_BLOCKED_CODES:
            intent = self._extract_original_intent(request.messages)
            if intent:
                for msg in response.result:
                    if not isinstance(msg, AIMessage) or not msg.tool_calls:
                        continue
                    if any(tc.get("name") == "AgentResponse" for tc in msg.tool_calls):
                        additional = msg.additional_kwargs or {}
                        additional["pending_intent"] = intent
                        msg.additional_kwargs = additional
                        break
                logger.info("捕获原始意图: %s（触发 code=%s）", intent, code)

        # ── 职责 3: 自动续接 ──
        if code and code in self._precondition_met_codes:
            pending = self._extract_pending_intent(request.messages)
            if pending:
                # 清除历史 messages 中的 pending_intent，防止后续重复续接
                for msg in request.messages:
                    if isinstance(msg, AIMessage) and msg.additional_kwargs:
                        msg.additional_kwargs.pop("pending_intent", None)
                return self._build_auto_continue_response(pending)

        return response
