"""
工具调用前置校验中间件

在工具执行前统一校验前置条件，避免每个工具函数重复样板检查。
当前校验项:
- bridge 注入：所有远程工具都需要 bridge 实例
- 场景上下文：除白名单外，工具调用时必须有 current_scene_name

校验失败时返回 ToolMessage（与 @tool 函数返回 dict 后由 ToolNode 自动包装的行为一致）。
不能返回裸 dict —— deepagents 的 FilesystemMiddleware 等基础栈中间件只接受
ToolMessage | Command，裸 dict 会触发 _aintercept_large_tool_result 的 AssertionError。

未来可扩展: 参数校验、权限、限流、审计等
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from space_aiagent.bridge import bridge_var, current_scene_name_var

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
    ) -> ToolMessage | Any:
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

        # 校验 2: 场景上下文（白名单外）
        if tool_name not in self._SCENE_EXEMPT_TOOLS and not current_scene_name_var.get():
            logger.warning("%s 校验失败: 无场景上下文", tool_name)
            return ToolMessage(
                content=json.dumps(
                    {
                        "success": False,
                        "message": "当前会话没有场景上下文，请先新建或打开一个场景",
                    },
                    ensure_ascii=False,
                ),
                tool_call_id=tool_call_id,
            )

        # 未来扩展点: 参数校验、权限、限流等

        return await handler(request)
