"""
场景管理 — 读工具

仅响应用户查询，无前后置流程。
"""
import inspect

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.utils import string_util


@tool
async def query_scenario(scene_name: str | None = None) -> dict:
    """
    查询场景信息。不传参数时查询当前场景。

    参数:
    - scene_name: 可选，指定场景名称查询。不传则查询当前场景。
    """


    tool_func = inspect.currentframe().f_code.co_name  # → "query_scenario"
    args: dict = string_util.args_to_camel(query_scenario,locals())


    bridge = bridge_var.get()
    return await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )

