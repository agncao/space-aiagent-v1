"""
工具调用前置校验中间件

在工具执行前统一校验前置条件，避免每个工具函数重复样板检查。
当前校验项:
- bridge 注入：所有远程工具都需要 bridge 实例。失败时返回 ToolMessage（系统级错误，
  让 LLM 兜底回复）

附加职责:
- suggestion 候选集注入：awrap_model_call 在每次 LLM 调用前把当前 agent 工具组
  对应的候选集写入 ContextVar，供 AgentResponse.suggestions validator 反向校验。

未来可扩展: 参数校验、权限、限流、审计等
"""

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

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
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse, ResponseCode
from space_aiagent.tools.registry import (
    current_suggestion_candidates_var,
    get_suggestion_candidates,
)
from space_aiagent.tools.scene_management import tools

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


class SubagentToolValidationMiddleware(AgentMiddleware):
    """工具调用前置条件统一校验 + suggestion 候选集注入"""

    state_schema = AgentState
    # 不需要场景上下文的工具白名单
    # - create_scenario: 场景入口工具，本身用于建立场景上下文
    # - query_scenario: 查询场景信息，可确认当前是否已经打开场景，可建立场景上下文
    # - AgentResponse: 结构化输出伪工具，非真实工具调用
    # - task: 子 Agent 调度工具，本身不操作场景
    _SCENE_EXEMPT_TOOLS = {
        AgentResponse.__name__,
        "task",
        "read_file",
        tools.create_scenario.name,
        tools.query_scenario.name,
        tools.open_scenario.name,
    }

    def __init__(
        self,
        tool_groups: list[str] | None = None,
        agent_name: str = "unknown",
    ) -> None:
        """初始化middleware

        Args:
            tool_groups: 当前 agent 绑定的工具组名列表（如 ["scene_management"]）。
                用于预生成 suggestion 候选集。None 或空列表时跳过候选集注入
                （如 orchestrator 不直接生成 AgentResponse，不需要）。
            agent_name: 当前 middleware 所属的 agent 名称，用于日志追踪。
        """
        super().__init__()
        self._tool_groups = tool_groups or []
        self._agent_name = agent_name
        # 启动期一次性生成候选集（避免每次 model call 都重新提取 description）
        self._suggestion_candidates = (
            frozenset(get_suggestion_candidates(self._tool_groups)) if self._tool_groups else frozenset()
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """LLM 调用前注入 suggestion 候选集到 ContextVar

        AgentResponse.suggestions validator 会从 ContextVar 读取候选集，
        过滤掉能力范围外的越界建议。必须在 LLM 产出 AgentResponse 之前 set，
        awrap_tool_call 太晚（在 LLM 之后）。
        """
        thread_id = get_config().get("configurable", {}).get("thread_id", "")

        if self._suggestion_candidates:
            current_suggestion_candidates_var.set(self._suggestion_candidates)

        logger.info(
            "model call before ",
            agent=self._agent_name,
            thread_id=thread_id,
        )
        start_ts = time.perf_counter()
        with optional_span(
            "subagent.llm",
            **{
                "agent.thread_id": thread_id,
                "subagent.name": self._agent_name,
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
            request = request.override(
                tool_call={**request.tool_call, "args": normalized_args}
            )

        logger.info(
            "tool call before",
            agent=self._agent_name,
            thread_id=thread_id,
            tool_name=tool_name,
            args=request.tool_call.get("args", {}),
        )

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

        # 校验 2: 场景上下文（白名单外）→ 返回 Command(goto=END) 终止子 Agent 图
        # ToolMessage 关闭 AI 的 tool_call（LLM API 协议要求），Command(goto=END)
        # 跳过子 Agent 后续 LLM 调用，state 含 NO_SCENE ToolMessage 持久化
        # current_scene_name 通过 state_schema 双向同步
        state_scene = (
            request.state.get("current_scene_name")
            if isinstance(request.state, dict)
            else getattr(request.state, "current_scene_name", None)
        )
        # 增加 1==0，表示让是否打开场景的校验实效，统一交给前端接口校验，
        # 如果需要则个校验，把1==0 给删除掉就行
        if 1==0 and tool_name not in self._SCENE_EXEMPT_TOOLS and not state_scene:
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
                "subagent.name": self._agent_name,
            },
        ) as span:
            set_span_io(span, input=request.tool_call.get("args", {}))
            try:
                result = await handler(request)
                span.set_attribute("tool.success", True)
                set_span_io(span, output=result)
                return result
            except Exception:
                span.set_attribute("tool.success", False)
                raise
            finally:
                span.set_attribute("tool.latency_ms", int((time.perf_counter() - start_ts) * 1000))
