"""
场景管理 — 读工具

仅响应当前步骤的查询或打开动作。Command 只写 ToolMessage；场景事实由
WorkflowEngine 根据持久化工具回告更新到 WorkflowRun.scene_context。
"""

import inspect
import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command, interrupt

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.biz_schemas import ScenarioInfo
from space_aiagent.tools.contracts import workflow_tool

_NAMESPACE = "scene_tools"

def _parse_decision(value: Any) -> bool | None:
    """把 interrupt resume 值收敛为bool值"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"yes", "true", "是"}:
            return True
        if normalized in {"no", "false", "否"}:
            return False
        return None
    if isinstance(value, dict):
        for key in ("decision", "value", "content", "user_input"):
            if key in value:
                return _parse_decision(value[key])
    return None


def _normalize_scenario_query_result(
    result: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]] | None]:
    """把前端场景对象收敛为安全、稳定的查询协议。"""
    normalized: dict[str, Any] = {
        "success": bool(result.get("success")),
        "code": result.get("code") or "",
        "message": result.get("message") or "",
    }
    current_scene_name = result.get("current_scene_name")
    if current_scene_name:
        normalized["current_scene_name"] = current_scene_name

    if not normalized["success"]:
        return normalized, None

    raw_data = result.get("data")
    if isinstance(raw_data, dict):
        # 前端查询结果统一为 {"list": [...], "count": N}；兼容旧的单对象返回
        inner_list = raw_data.get("list")
        if isinstance(inner_list, list):
            raw_items = inner_list
        else:
            raw_items = [raw_data]
    elif isinstance(raw_data, list):
        raw_items = raw_data
    else:
        raw_items = []

    scenarios = [
        scenario
        for item in raw_items
        if isinstance(item, dict) and (scenario := ScenarioInfo.from_frontend(item)) is not None
    ]
    serialized = [scenario.model_dump() for scenario in scenarios]
    normalized["data"] = serialized
    return normalized, serialized


@tool
async def query_scenario(runtime: ToolRuntime, scene_name: str | None = None) -> Command:
    """
    查询场景信息。

    args:
        scene_name(str|None) :  要匹配的场景名。
    """
    tool_func = inspect.currentframe().f_code.co_name  # → "query_scenario"
    args: dict = string_util.args_to_camel(query_scenario, locals())

    bridge = bridge_var.get()
    result = await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
    normalized_result, _ = _normalize_scenario_query_result(result)

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=json.dumps(normalized_result, ensure_ascii=False),
                    tool_call_id=runtime.tool_call_id,
                )
            ]
        }
    )


@workflow_tool(
    effects={"scene.opened"},
    invalidates={"scene.none"},
)
@tool
async def open_scenario(
    runtime: ToolRuntime,
    scene_name: str | None = None,
) -> Command:
    """
    打开场景

    args:
        scene_name: 想打开的场景
    """
    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(open_scenario, locals())

    bridge = bridge_var.get()
    result = await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )

    if result.get("code") == "SCENE_UNSAVED_CHANGES":
        prompt = {
            "description": result.get("message") or "当前场景有未保存的改动，是否保存？",
            "options": ["yes", "no"],
        }
        decision = _parse_decision(interrupt(prompt))
        while decision is None:
            decision = _parse_decision(interrupt(prompt))
        resumed_args = {**args, "isSaveOnChange": decision}
        result = await bridge.send_tool_call(
            namespace=_NAMESPACE,
            tool_func=string_util.snake_to_camel(tool_func),
            args=resumed_args,
        )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=runtime.tool_call_id,
                )
            ]
        }
    )
