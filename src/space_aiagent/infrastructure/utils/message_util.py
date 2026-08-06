import json
from typing import Any

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.messages.tool import ToolCall

from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse


def extract_last_task(
    messages: list[BaseMessage],
) -> tuple[str | None, str | None, ToolCall | None]:
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
                return (subagent_name, msg.content, tc)
    return (None, None, None)


def extract_last_human_intent(
    messages: list[BaseMessage], ignore_messages: list[str] = []
) -> tuple[str | None, HumanMessage | None]:
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


def build_task_response(description: str, subagent_type: str, id: str) -> ModelResponse:
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


def build_primary_agent_response(content: str, resp: AgentResponse, id: str) -> ModelResponse:
    return ModelResponse(
        result=[
            AIMessage(
                content=content,
                tool_calls=[
                    {
                        "name": "AgentResponse",
                        "args": resp.model_dump(mode="json"),
                        "id": id,
                        "type": "tool_call",
                    }
                ],
            )
        ],
        structured_response=resp,
    )

def serialize_messages(
    messages: list[BaseMessage],
    content_max_len: int = 500,
    args_max_len: int = 300,
) -> list[dict[str, Any]]:
    """序列化消息列表为可读结构（供 Langfuse span input 使用）。

    每条：{type, content(截断), tool_calls?: [{name, args(截断 JSON)}]}
    比 BaseMessage.model_dump 精简，比 msg_preview 信息更全（保留 tool_call args）。
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        item: dict[str, Any] = {
            "type": getattr(msg, "type", "?"),
            "content": string_util.truncate(str(getattr(msg, "content", "")), max_len=content_max_len),
        }
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            item["tool_calls"] = [
                {
                    "name": tc.get("name", "?"),
                    "args": string_util.truncate(
                        json.dumps(tc.get("args", {}), ensure_ascii=False, default=str),
                        max_len=args_max_len,
                    ),
                }
                for tc in tool_calls
            ]
        out.append(item)
    return out


def serialize_model_response(response: ModelResponse) -> dict[str, Any]:
    """序列化 ModelResponse 为可读 dict（供 Langfuse span output 使用）。

    提取 response.result 各 AIMessage 的 content + tool_calls；
    命中 AgentResponse 时额外带 code/status/summary，便于在 Langfuse 直接看决策结果。
    """
    payload: dict[str, Any] = {
        "messages": serialize_messages(response.result) if response.result else [],
    }
    for tc in extract_tool_calls(response):
        if tc.get("name") != "AgentResponse":
            continue
        args = tc.get("args", {})
        payload["agent_response"] = {
            "code": args.get("code"),
            "status": args.get("status"),
            "summary": args.get("summary"),
        }
        break
    return payload
