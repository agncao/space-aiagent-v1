import json
from typing import Any

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, BaseMessage

from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse


def extract_tool_calls(response: ModelResponse) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for msg in response.result:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
    return tool_calls


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
