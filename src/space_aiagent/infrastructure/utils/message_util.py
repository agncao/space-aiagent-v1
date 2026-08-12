"""Worker 消息的构造、序列化与观测辅助。"""

import json
from typing import Any

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, BaseMessage

from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.response_schema.worker_response import WorkerResponse


def extract_tool_calls(response: ModelResponse) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for message in response.result:
        if isinstance(message, AIMessage) and message.tool_calls:
            tool_calls.extend(message.tool_calls)
    return tool_calls


def build_worker_response(content: str, response: WorkerResponse, response_id: str) -> ModelResponse:
    """构造与 ToolStrategy 一致的确定性 Worker 降级响应。"""
    return ModelResponse(
        result=[
            AIMessage(
                content=content,
                tool_calls=[
                    {
                        "name": WorkerResponse.__name__,
                        "args": response.model_dump(mode="json"),
                        "id": response_id,
                        "type": "tool_call",
                    }
                ],
            )
        ],
        structured_response=response,
    )


def serialize_messages(
    messages: list[BaseMessage],
    content_max_len: int = 500,
    args_max_len: int = 300,
) -> list[dict[str, Any]]:
    """序列化消息列表为精简、可观测的结构。"""
    serialized: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {
            "type": getattr(message, "type", "?"),
            "content": string_util.truncate(str(getattr(message, "content", "")), max_len=content_max_len),
        }
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            item["tool_calls"] = [
                {
                    "name": tool_call.get("name", "?"),
                    "args": string_util.truncate(
                        json.dumps(tool_call.get("args", {}), ensure_ascii=False, default=str),
                        max_len=args_max_len,
                    ),
                }
                for tool_call in tool_calls
            ]
        serialized.append(item)
    return serialized


def serialize_model_response(response: ModelResponse) -> dict[str, Any]:
    """序列化 Worker 模型响应，并提取结构化步骤结果摘要。"""
    payload: dict[str, Any] = {
        "messages": serialize_messages(response.result) if response.result else [],
    }
    for tool_call in extract_tool_calls(response):
        if tool_call.get("name") != WorkerResponse.__name__:
            continue
        args = tool_call.get("args", {})
        payload["worker_response"] = {
            "code": args.get("code"),
            "status": args.get("status"),
            "summary": args.get("summary"),
        }
        break
    return payload
