import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from space_aiagent.agents.subagents_util import load_subagents_yaml_config
from space_aiagent.infrastructure.llm import build_flash_model
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span, set_span_io
from space_aiagent.infrastructure.utils import collection_util, message_util, string_util
from space_aiagent.models.response_schema import response_constants, response_util
from space_aiagent.models.response_schema.agent_struct_response import ResponseCode
from space_aiagent.models.schemas import SubagentClassification

logger = get_logger(__name__)

orchestrator_task_streak_var: ContextVar[int] = ContextVar(
    "orchestrator_task_streak_var",
    default=0,
)


def _last_message_is_human(messages: list) -> bool:
    """request.messages 最后一条是否为 HumanMessage

    用于 DELEGATION_GUARD 判定「是否在直接回应用户新消息」：task 返回后的总结轮
    最后一条是 ToolMessage，不会触发兜底，避免误改写与无限循环。
    """
    return bool(messages) and getattr(messages[-1], "type", None) == "human"


def _extract_last_human_content(messages: list) -> str | None:
    """从消息列表倒序找最近一条 HumanMessage 的文本内容（去空白后为空则返回 None）"""
    for msg in reversed(messages):
        if getattr(msg, "type", None) != "human":
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            # content blocks 模式：拼接所有 text 块
            text = " ".join(
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            text = str(content)
        text = text.strip()
        return text or None
    return None


class PrimaryAgentMiddleware(AgentMiddleware):
    """主控 Agent 级运行时护栏 + 意图追踪与自动续接

    职责:
    1. TASK_LOOP_GUARD: 连续 task 死循环兜底（现有）
    2. DELEGATION_GUARD: 自由文本漏委派兜底——去除 AgentResponse 结构化输出后，
       orchestrator 可直接用自然语言结束本轮。当 LLM 在回应用户新消息时未调用
       任何工具（既没 task 也没别的），用 Flash LLM 判断该意图是否应委派给子 Agent，
       命中则改写为 task(description, subagent_type)；闲聊/非领域意图放行原响应。
    """


    def __init__(
        self,
        thread_id: str = "",
        task_loop_threshold: int = 20,
    ) -> None:
        self.thread_id = thread_id
        self._threshold = max(1, int(task_loop_threshold))
        # Flash LLM 自建（专供路由分类等轻量调用），避免 orchestrator 启动期与 yaml 耦合
        self._flash_model = build_flash_model()

    @staticmethod
    def _build_shortcut_response() -> ModelResponse:
        shortcut = response_constants.SHORTCUT_RESPONSES[ResponseCode.TASK_LOOP_GUARD]
        return ModelResponse(
            result=[AIMessage(content=shortcut.summary)],
            structured_response=shortcut,
        )

    @staticmethod
    def _build_task_delegation(intent: str, subagent_type: str) -> ModelResponse:
        """构造一个 task(description, subagent_type) 的 ModelResponse，用于强制委派"""
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"description": intent, "subagent_type": subagent_type},
                            "id": f"deleg_{uuid4().hex[:12]}",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
        )

    async def _classify_delegation_target(self, intent: str) -> str | None:
        """用 Flash LLM 判断用户意图应否委派给某子 Agent

        返回子 Agent name 表示应委派；返回 None 表示闲聊/非领域/无需委派（放行原响应）。
        复用 SubagentClassification（subagent_type 为 Literal[...] | None，可空），
        prompt 含 'json' 字样以满足 LLM 提供商 response_format 要求。分类异常或返回
        非法 name 时一律返回 None（宁可放行也不误委派）。
        """
        subagents = load_subagents_yaml_config().get("agents", [])
        if not subagents:
            return None
        valid_names = {s["name"] for s in subagents}
        agents_desc = "\n".join(f"- {s['name']}: {s['description']}" for s in subagents)
        prompt = (
            "你是航天分析平台的路由分类器。判断用户意图是否应交给某个子 Agent 处理。\n"
            "- 若属于场景/实体/轨道相关操作，返回对应子 Agent name。\n"
            "- 若是闲聊、问候、致谢、或明显超出平台能力的非领域请求，返回 null。\n\n"
            f"可用子 Agent:\n{agents_desc}\n\n"
            f"用户意图: {intent}\n\n"
            '请输出 json: {"subagent_type": "<子 agent name 或 null>"}。'
        )
        try:
            result = await self._flash_model.with_structured_output(
                SubagentClassification
            ).ainvoke(prompt)
        except Exception:
            logger.exception("委派兜底路由分类失败", thread_id=self.thread_id, intent=intent)
            return None
        name = getattr(result, "subagent_type", None)
        if name not in valid_names:
            logger.info(
                "委派兜底分类为无需委派",
                thread_id=self.thread_id,
                intent=intent,
                classified=name,
            )
            return None
        return name

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

        # ── 职责 2: DELEGATION_GUARD（自由文本漏委派兜底）──
        # 触发条件：本轮 LLM 既没调 task 也没调任何工具，且是在直接回应用户的新消息
        # （request.messages 最后一条是 HumanMessage）。这种「直接聊天」在去除
        # AgentResponse 结构化输出后会成为漏委派的主路径。用 Flash LLM 判定该意图
        # 是否应委派：命中子 Agent → 改写为 task；闲聊/非领域 → 放行原响应。
        # 仅在「最后一条是 human」时触发，确保 task 回来后的总结轮（最后一条是
        # ToolMessage）不会被误改写，也避免无限循环。
        tool_calls = message_util.extract_tool_calls(response)
        if not tool_calls and _last_message_is_human(request.messages):
            intent = _extract_last_human_content(request.messages)
            if intent:
                subagent_type = await self._classify_delegation_target(intent)
                if subagent_type:
                    logger.warning(
                        "orchestrator 自由文本漏委派，强制改写为 task 委派",
                        thread_id=self.thread_id,
                        intent=intent,
                        subagent_type=subagent_type,
                    )
                    response = self._build_task_delegation(intent, subagent_type)

        # ── 职责 1: TASK_LOOP_GUARD ──
        # 注意：上面 DELEGATION_GUARD 可能已把 response 改写为 task 调用，此处重新
        # 提取 tool_calls，让 streak 统计把这次强制委派也计入，避免兜底路径绕过死循环防护。
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
