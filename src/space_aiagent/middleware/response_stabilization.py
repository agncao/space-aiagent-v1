
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from space_aiagent.bridge import tools_results_var
from space_aiagent.bridge.response_renderer import ResponseRenderer
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.response_schema import AgentResponse

logger = logging.getLogger(__name__)


class ResponseStabilizationMiddleware(AgentMiddleware):
    """AgentResponse 稳定化中间件
    """

    state_schema = AgentState
    _NON_SUBAGENT_TOOLS = frozenset({AgentResponse.__name__, "task"})

    def __init__(self, agent_name: str = "unknown") -> None:
        super().__init__()
        self.agent_name = agent_name

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> ToolMessage | Command[Any] | Any:
        """工具调用后累积结构化记录到 tools_results_var（仅子 Agent）"""
        tool_name = request.tool_call.get("name", "")
        handle_result = await handler(request)

        # Orchestrator 不直接调业务工具（task/AgentResponse 也跳过），不写入
        if self.agent_name == "orchestrator" or tool_name in self._NON_SUBAGENT_TOOLS:
            return handle_result

        if not isinstance(handle_result, ToolMessage):
            return handle_result

        content = handle_result.content
        result = json.loads(content) if isinstance(content, str) else content

        success: bool = result.get("success", False)
        # 工具返回的 args 通常不带 scene_name（如 queryEntities 只返回 entities/count），
        # 但 ENTITIES_LIST/ENTITIES_EMPTY/ENTITY_CREATED/SCENE_CREATED 模板都要 scene_name。
        # 从 state 兜底补全（current_scene_name 通过 SpaceAgentState 同步）。
        args = string_util.keys_to_snake(result.get("args", {}))
        if "scene_name" not in args.keys():
            state_scene = (
                request.state.get("current_scene_name")
                if isinstance(request.state, dict)
                else getattr(request.state, "current_scene_name", None)
            )
            if state_scene:
                args["scene_name"] = state_scene
        tool_record: dict[str, Any] = {
            "status": "success",
            "code": result.get("code", ""),
            "summary": result.get("message", ""),
            "args": args,
            "tool_func": tool_name,
            "data": result.get("data", {}),
        }
        if not success:
            code = str(result.get("code", ""))
            code_upper = code.upper()
            if code_upper.endswith("_FAILED") or code_upper.endswith("_ERROR"):
                tool_record["status"] = "error"
            else:
                tool_record["status"] = "info"

        # 累积到 ContextVar（websocket handler 在每轮 user_input 时 set([]) 重置）
        existing_results = tools_results_var.get()
        if existing_results is None:
            existing_results = []

        existing_results.append(tool_record)
        tools_results_var.set(existing_results)

        return handle_result

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """LLM 产出 AgentResponse 后从 tools_results_var 覆盖（suggestions 保留 LLM）"""

        return await handler(request)

        # try:
        #     return self._stabilize(response)
        # except Exception:
        #     logger.warning("AgentResponse 稳定化失败，原样返回 LLM 响应", exc_info=True)
        #     return response

    def _stabilize(self, response: ModelResponse) -> ModelResponse:
        """对 response 中含 AgentResponse tool_call 的 AIMessage 做稳定化

        策略：渲染模板，把渲染文本写回 AIMessage.content。
        - AIMessage.content 会被 astream_events 推到 websocket（出口读 output.content）
        - AIMessage.content 会被 checkpointer 持久化（下一轮 LLM 看到，cross-turn 上下文）
        原 tool_calls 保留，ToolStrategy 结构化输出协议不变。
        """
        new_messages: list[BaseMessage] = []
        modified = False
        new_structured_response = response.structured_response

        for msg in response.result:
            if not isinstance(msg, AIMessage) or not msg.tool_calls:
                new_messages.append(msg)
                continue

            agent_response_tc = next(
                (tc for tc in msg.tool_calls if tc.get("name") == "AgentResponse"),
                None,
            )
            if agent_response_tc is None:
                new_messages.append(msg)
                continue

            response_renderer = ResponseRenderer()
            display_content = response_renderer.render(new_structured_response)

            # 重建 AIMessage：content 写渲染文本，tool_calls 原样保留
            new_msg = AIMessage(
                content=display_content,
                tool_calls=msg.tool_calls,
                additional_kwargs=msg.additional_kwargs,
                response_metadata=msg.response_metadata,
                name=msg.name,
                id=msg.id,
            )
            new_messages.append(new_msg)
            modified = True

        if not modified:
            return response

        return ModelResponse(
            result=new_messages,
            structured_response=new_structured_response,
        )
