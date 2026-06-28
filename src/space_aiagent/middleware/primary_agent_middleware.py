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
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from space_aiagent.bridge.response_renderer import ResponseRenderer
from space_aiagent.bridge.response_shortcut import _SHORTCUT_RESPONSES
from space_aiagent.infrastructure.llm import build_model
from space_aiagent.models.response_schema import ResponseCode
from space_aiagent.models.schemas import SubagentClassification

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

    state_schema = AgentState

    # 前置条件不满足的 code，触发意图捕获
    _PRECONDITION_BLOCKED_CODES = frozenset({ResponseCode.NO_SCENE})

    # 前置条件已满足的 code，触发自动续接（默认值，可构造时覆盖）
    _DEFAULT_PRECONDITION_MET_CODES = frozenset({ResponseCode.SCENE_CREATED})

    # LLM 分类无可用 subagent_summaries 时的最终 fallback
    _FALLBACK_SUBAGENT = "entity-agent"

    # 用户确认短句——不视为原始意图（避免覆盖上一轮的真实意图）
    _CONFIRMATION_PHRASES = frozenset({
        "好的", "好", "好啊", "好吧", "行", "可以", "没问题", "确认",
        "ok", "okay", "yes", "是", "是的", "嗯", "嗯嗯", "继续",
    })

    def __init__(
        self,
        task_loop_threshold: int = 20,
        precondition_met_codes: frozenset[ResponseCode] | None = None,
        subagent_summaries: list[dict[str, str]] | None = None,
        model: BaseChatModel | None = None,
    ) -> None:
        self._threshold = max(1, int(task_loop_threshold))
        self._precondition_met_codes = (
            precondition_met_codes if precondition_met_codes is not None else self._DEFAULT_PRECONDITION_MET_CODES
        )
        self._subagent_summaries = subagent_summaries or []
        self._model = model or build_model()

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
                return tc.get("args", {}).get("code")
        return None

    @staticmethod
    def _extract_original_intent(messages: list) -> str | None:
        """从 messages 中提取用户原始意图：返回最新的非空、非确认短句的 HumanMessage content

        跳过「好的」「ok」等纯确认短句，避免下一轮用户简单确认时把原始意图覆盖丢失。
        """
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = str(msg.content).strip()
                if not content:
                    continue
                if content.lower() in PrimaryAgentMiddleware._CONFIRMATION_PHRASES:
                    continue
                return content
        return None

    @staticmethod
    def _extract_last_task_subagent(messages: list) -> str | None:
        """扫描 messages 找最近的 task tool_call 的 subagent_type

        覆盖流程 2（task → ToolValidationMiddleware NO_SCENE → AgentResponse）。
        流程 1（orchestrator 直接 NO_SCENE 不调 task）返回 None，由调用方走 LLM fallback。
        """
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.get("name") != "task":
                        continue
                    subagent = tc.get("args", {}).get("subagent_type")
                    if subagent:
                        return subagent
        return None

    @staticmethod
    def _extract_pending_metadata(messages: list) -> tuple[str | None, str | None]:
        """从 messages 中扫描最近的 pending_intent 和 pending_subagent

        Returns:
            (intent, subagent) — 两者都可能为 None
        """
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                additional = msg.additional_kwargs or {}
                pending = additional.get("pending_intent")
                if pending:
                    return pending, additional.get("pending_subagent")
        return None, None

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
    def _build_auto_continue_response(intent: str, subagent_type: str) -> ModelResponse:
        """构建自动续接响应：将 LLM 输出替换为 task 调用，委派给指定 subagent"""
        logger.info("自动续接原始意图: %s, subagent: %s", intent, subagent_type)
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": intent,
                                "subagent_type": subagent_type,
                            },
                            "id": "call_pending_intent_auto",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
        )

    # ── 实例方法 ──────────────────────────────────────────────

    def _build_classification_prompt(self, intent: str) -> str:
        """构建 LLM 路由分类 prompt

        必须包含 'json' 字样——LLM 提供商要求 response_format=json_object 时
        messages 中要出现该词，否则返回 400。
        """
        agents_desc = "\n".join(
            f"- {s['name']}: {s['description']}" for s in self._subagent_summaries
        )
        return (
            "你是航天分析平台的路由分类器。根据用户意图选择最合适的子 Agent，"
            "以 JSON 格式输出结果。\n\n"
            f"可用子 Agent:\n{agents_desc}\n\n"
            f"用户意图: {intent}\n\n"
            "请输出 json: {\"subagent_type\": \"<子 agent name>\"}。"
        )

    async def _resolve_subagent_type(
        self,
        intent: str,
        captured_subagent: str | None,
    ) -> str:
        """解析自动续接的 subagent_type

        1. 主路径：用 captured_subagent（来自 task 历史）
        2. Fallback：LLM 分类（流程 1）
        3. 校验：LLM 返回不在 subagent_summaries.name 集合时，fallback 到 summaries[0]
        """
        if captured_subagent:
            return captured_subagent

        valid_names = {s["name"] for s in self._subagent_summaries}
        if not valid_names:
            logger.warning(
                "无可用 subagent_summaries，无法 LLM 分类，回退 %s",
                self._FALLBACK_SUBAGENT,
            )
            return self._FALLBACK_SUBAGENT

        prompt = self._build_classification_prompt(intent)
        classifier = self._model.with_structured_output(SubagentClassification)
        try:
            result = await classifier.ainvoke(prompt)
        except Exception:
            logger.exception("LLM 路由分类失败，回退 %s", self._subagent_summaries[0]["name"])
            return self._subagent_summaries[0]["name"]

        if result.subagent_type not in valid_names:
            logger.warning(
                "LLM 分类返回无效 subagent_type=%s，valid=%s，回退 %s",
                result.subagent_type, valid_names, self._subagent_summaries[0]["name"],
            )
            return self._subagent_summaries[0]["name"]
        logger.info("LLM 路由分类: intent=%s → %s", intent, result.subagent_type)
        return result.subagent_type

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
            # 优先沿用历史 pending_intent：避免被本轮「好的」「创建测试场景」等
            # 确认/推进型输入覆盖上一轮真实意图
            existing_intent, existing_subagent = self._extract_pending_metadata(request.messages)
            intent = existing_intent or self._extract_original_intent(request.messages)
            if intent:
                subagent = existing_subagent or self._extract_last_task_subagent(request.messages)
                for msg in response.result:
                    if not isinstance(msg, AIMessage) or not msg.tool_calls:
                        continue
                    if any(tc.get("name") == "AgentResponse" for tc in msg.tool_calls):
                        additional = msg.additional_kwargs or {}
                        additional["pending_intent"] = intent
                        if subagent:
                            additional["pending_subagent"] = subagent
                        msg.additional_kwargs = additional
                        break
                logger.info(
                    "捕获原始意图: %s, subagent: %s（触发 code=%s, 沿用历史=%s）",
                    intent, subagent, code, bool(existing_intent),
                )

        # ── 职责 3: 自动续接 ──
        if code and code in self._precondition_met_codes:
            pending, captured_subagent = self._extract_pending_metadata(request.messages)
            if pending:
                # 清除历史 messages 中的 pending_intent/pending_subagent，防止后续重复续接
                for msg in request.messages:
                    if isinstance(msg, AIMessage) and msg.additional_kwargs:
                        msg.additional_kwargs.pop("pending_intent", None)
                        msg.additional_kwargs.pop("pending_subagent", None)
                subagent_type = await self._resolve_subagent_type(pending, captured_subagent)
                return self._build_auto_continue_response(pending, subagent_type)

        return response
