"""
场景管理 — 读工具

仅响应用户查询，无前后置流程。
"""
import inspect

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var,current_scene_name_var
from space_aiagent.infrastructure.utils import string_util


@tool
async def query_scenario(scene_name: str | None = None) -> dict:
    """
    查询场景信息。不传参数时查询当前场景(即当前打开的场景)。
    """


    tool_func = inspect.currentframe().f_code.co_name  # → "query_scenario"
    args: dict = string_util.args_to_camel(query_scenario,locals())


    bridge = bridge_var.get()
    result = await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )

    if result["success"]:
        data = result.get("data")
        if isinstance(data,dict):
            data = data or {}
            current_scene_name_var.set(data.get("scene_name"))
        elif isinstance(data,list):
            data = data[0] or {}
            current_scene_name_var.set(data.get("scene_name"))
    return result