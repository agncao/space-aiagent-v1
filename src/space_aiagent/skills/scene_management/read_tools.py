"""
场景管理 — 读工具

仅响应用户查询，无前后置流程。
"""

import logging

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var

logger = logging.getLogger(__name__)


@tool
async def query_scenario(scene_name: str | None = None) -> dict:
    """
    查询场景信息。不传参数时查询当前场景。

    参数:
    - scene_name: 可选，指定场景名称查询。不传则查询当前场景。
    """
    bridge = bridge_var.get()
    if bridge is None:
        logger.error("bridge 未注入，无法发送 queryScenario 指令")
        return {"success": False, "message": "bridge 未注入，无法发送指令"}

    args: dict = {}
    if scene_name:
        args["sceneName"] = scene_name

    result = await bridge.send_tool_call(
        tool_func="queryScenario",
        args=args,
    )
    return result


@tool
async def query_scenario_entities() -> dict:
    """
    查询当前场景中的所有实体名称列表。
    """
    bridge = bridge_var.get()
    if bridge is None:
        logger.error("bridge 未注入，无法发送 queryScenarioEntities 指令")
        return {"success": False, "message": "bridge 未注入，无法发送指令"}

    result = await bridge.send_tool_call(
        tool_func="queryScenarioEntities",
        args={},
    )
    return result
