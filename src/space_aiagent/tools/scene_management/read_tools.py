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
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.biz_schemas import ScenarioInfo

_NAMESPACE = "scene_tools"


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


@tool
async def open_scenario(
    runtime: ToolRuntime,
    is_save_on_change: bool | None = None,
    scene_name: str | None = None,
) -> Command:
    """
    打开场景

    args:
        is_save_on_change(bool|None): 如果存在已打开的场景，且存在为保存的变更是否需要保存
            is_save_on_change=true 表示用户确定要保存变更
            is_save_on_change=false 表示用户确定不需要保存变更
            is_save_on_change=None 表示未知用户是否需要保存变更
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
