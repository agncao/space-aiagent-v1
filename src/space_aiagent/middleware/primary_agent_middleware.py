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
from langchain_core.messages import AIMessage

from space_aiagent.bridge.response_shortcut import _SHORTCUT_RESPONSES
from space_aiagent.bridge.response_renderer import ResponseRenderer

logger = logging.getLogger(__name__)

orchestrator_task_streak_var: ContextVar[int] = ContextVar(
    "orchestrator_task_streak_var",
    default=0,
)


class PrimaryAgentMiddleware(AgentMiddleware):
    """主控 Agent 级运行时护栏"""

    state_schema = AgentState

    def __init__(self, task_loop_threshold: int = 20) -> None:
        self._threshold = max(1, int(task_loop_threshold))

    @staticmethod
    def _extract_tool_calls(response: ModelResponse) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        for msg in response.result:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                tool_calls.extend(msg.tool_calls)
        return tool_calls

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

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        tool_calls = self._extract_tool_calls(response)
        task_call_count = sum(1 for tc in tool_calls if tc.get("name") == "task")

        if task_call_count == 0:
            orchestrator_task_streak_var.set(0)
            return response

        streak = orchestrator_task_streak_var.get() + task_call_count
        orchestrator_task_streak_var.set(streak)
        if streak < self._threshold:
            return response

        logger.warning("检测到 orchestrator 连续决策调用 task %d 次，改写为结构化短路响应", streak)
        return self._build_shortcut_response()
