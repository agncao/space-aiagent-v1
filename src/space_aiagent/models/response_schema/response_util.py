from typing import Any

from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse

logger = get_logger(__name__)


def find_agent_response_tool_call(response: ModelResponse) -> dict[str, Any] | None:
    """在 ModelResponse 中查找名为 AgentResponse 的 tool_call，找不到返回 None"""
    for msg in response.result:
        if not isinstance(msg, AIMessage) or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc.get("name") != "AgentResponse":
                continue
            return tc
    return None


def parse_code_by_model_response(response: ModelResponse) -> str | None:
    """
    从 ModelResponse 中提取 AgentResponse tool_call 的 code 字段
    """
    agent_tc = find_agent_response_tool_call(response)
    if not agent_tc:
        return None
    return agent_tc.get("args", {}).get("code")


def render(response: AgentResponse) -> str:
    """将结构化响应渲染为自然语言"""

    def _fallback_text() -> str:
        parts = [response.summary]
        if response.suggestions:
            parts.append(" **接下来您可以：**\n")
            parts.append("\n".join(f"- {s}" for s in response.suggestions))
        return "\n\n".join(parts)

    return _fallback_text()
