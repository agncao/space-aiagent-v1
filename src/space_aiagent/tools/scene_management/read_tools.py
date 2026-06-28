"""
场景管理 — 读工具

仅响应用户查询，无前后置流程。

query_scenario 成功后通过 Command 把当前场景名同步到 state（用户查询某场景时
前端会切换激活，state 跟着更新），替代旧版 current_scene_name_var.set。
"""
import inspect
import json

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.utils import string_util


@tool
async def query_scenario(runtime: ToolRuntime, scene_name: str | None = None) -> Command:
    """
    查询场景信息。不传参数时查询当前场景(即当前打开的场景)。
    """
    tool_func = inspect.currentframe().f_code.co_name  # → "query_scenario"
    args: dict = string_util.args_to_camel(query_scenario, locals())

    bridge = bridge_var.get()
    result = await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )

    update: dict = {
        "messages": [
            ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=runtime.tool_call_id,
            )
        ]
    }
    if result["success"]:
        data = result.get("data")
        resolved_scene_name: str | None = None
        if isinstance(data, dict):
            data = data or {}
            resolved_scene_name = data.get("scene_name")
        elif isinstance(data, list) and data:
            resolved_scene_name = (data[0] or {}).get("scene_name")
        if resolved_scene_name:
            update["current_scene_name"] = resolved_scene_name
    return Command(update=update)
