"""
场景管理 — 读工具

仅响应用户查询，无前后置流程。
"""

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var


@tool
async def query_scenario(scene_name: str | None = None) -> dict:
    """
    查询场景信息。不传参数时查询当前场景。

    参数:
    - scene_name: 可选，指定场景名称查询。不传则查询当前场景。
    """
    bridge = bridge_var.get()
    args: dict = {}
    if scene_name:
        args["sceneName"] = scene_name

    return await bridge.send_tool_call(
        tool_func="queryScenario",
        args=args,
    )

