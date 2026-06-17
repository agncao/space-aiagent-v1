"""
工具调用前置校验中间件

在工具执行前统一校验前置条件，避免每个工具函数重复样板检查。
当前校验项:
- bridge 注入：所有远程工具都需要 bridge 实例。失败时返回 ToolMessage（系统级错误，
  让 LLM 兜底回复）
- 场景上下文：除白名单外，工具调用时必须有 current_scene_name。失败时返回
  Command(goto=END)，update 里塞一条携带 NO_SCENE 错误的 ToolMessage——
  ToolMessage 是 LLM API 协议层的"关闭 tool_call"动作（不能省），Command(goto=END)
  则强制终止子 Agent 图，跳过"解释工具结果"那次 LLM 调用。状态由 LangGraph
  自动持久化到 checkpointer，多轮对话能正确恢复上下文。

未来可扩展: 参数校验、权限、限流、审计等
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from space_aiagent.bridge import bridge_var, current_scene_name_var
from space_aiagent.bridge.response_shortcut import _SHORTCUT_RESPONSES

logger = logging.getLogger(__name__)


class ToolValidationMiddleware(AgentMiddleware):
    """工具调用前置条件统一校验"""

    state_schema = AgentState

    # 不需要场景上下文的工具白名单
    # - create_scenario: 场景入口工具，本身用于建立场景上下文
    # - AgentResponse: 结构化输出伪工具，非真实工具调用
    # - task: 子 Agent 调度工具，本身不操作场景
    _SCENE_EXEMPT_TOOLS = frozenset({
        "create_scenario",
        "AgentResponse",
        "task",
    })

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> ToolMessage | Command[Any] | Any:
        tool_name = request.tool_call.get("name", "")
        tool_call_id = request.tool_call.get("id", "")

        # 校验 1: bridge 注入（所有远程工具都需要）
        if bridge_var.get() is None:
            logger.error("%s 校验失败: bridge 未注入", tool_name)
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
        if tool_name not in self._SCENE_EXEMPT_TOOLS and not current_scene_name_var.get():
            logger.warning("%s 校验失败: 无场景上下文", tool_name)
            shortcut = _SHORTCUT_RESPONSES["no_scene"]
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

        return await handler(request)
