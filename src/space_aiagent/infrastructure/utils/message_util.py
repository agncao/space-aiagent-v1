from langchain.agents.middleware import ModelResponse

from typing import Any
from langchain_core.messages.tool import ToolCall
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse


def msg_preview(msg: BaseMessage, max_len: int = 120) -> dict:
    """提取消息预览信息"""
    content = str(getattr(msg, "content", ""))
    preview: dict[str, Any] = {
        "type": getattr(msg, "type", "?"),
        "content": content[:max_len] + ("..." if len(content) > max_len else ""),
    }
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        preview["tool_calls"] = [tc.get("name", "?") for tc in tool_calls]
    return preview

def extract_last_task(
    messages: list[BaseMessage],
) -> tuple[str|None, str | None, ToolCall|None]:
    """
    扫描 messages 找最近的 task tool_call 的 subagent_type

    Returns:
        (subagent_name, task_content, task_tool_call) — 三者都可能为 None
    """
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") != "task":
                    continue
                subagent_name = tc.get("args", {}).get("subagent_type")
                return ( subagent_name, msg.content, tc)
    return (None, None, None)


def extract_last_human_intent(messages: list[BaseMessage], ignore_messages: list[str] = []) -> tuple[str|None, HumanMessage | None]:
    """
    从 messages 中提取用户原始意图：返回最新的非空、非确认短句的 HumanMessage content
    ignore_messages: 跳过这些，例如：「好的」「ok」等纯确认短句，避免下一轮用户简单确认时把原始意图覆盖丢失。

    Returns:
        (intent, human_message) — 两者都可能为 None
    """
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            intent = str(msg.content).strip()
            if not intent:
                continue
            if intent.lower() in ignore_messages:
                continue
            return (intent, msg)
    return (None, None)

def extract_last_existing_intent(messages: list[BaseMessage]) -> tuple[str | None, str | None, AIMessage | None]:
    """从 messages 中扫描最近的 pending_intent 和 pending_subagent

    Returns:
        (intent, subagent, ai_message) — 三者都可能为 None
    """
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            additional = msg.additional_kwargs or {}
            intent = additional.get("pending_intent")
            if intent:
                return (intent, additional.get("pending_subagent"), msg)
    return (None, None, None)

def extract_tool_calls(response: ModelResponse) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for msg in response.result:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
    return tool_calls


def build_task_response(description: str, subagent_type: str,id: str) -> ModelResponse:
    return ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": description,
                            "subagent_type": subagent_type,
                        },
                        "id": id,
                        "type": "tool_call",
                    }
                ],
            )
        ],
    )


def build_primary_agent_response(content: str, resp:AgentResponse, id: str) -> ModelResponse:
    return ModelResponse(
        result=[
            AIMessage(
                content=content,
                tool_calls=[
                    {
                        "name": "AgentResponse",
                        "args":  resp.model_dump(mode="json"),
                        "id": id,
                        "type": "tool_call",
                    }
                ],
            )
        ],
        structured_response=resp,
    )