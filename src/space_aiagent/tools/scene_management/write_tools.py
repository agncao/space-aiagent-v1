"""
场景管理 — 写工具

直接执行，无前后置流程。
"""

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var, current_scene_name_var
from space_aiagent.models.schemas import ScenarioConfig


@tool(args_schema=ScenarioConfig)
async def create_scenario(
    scene_name: str = "新建场景",
    central_body: str = "Earth",
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
) -> dict:
    """
    创建航天场景。场景是所有实体的容器，添加卫星、地面站等实体前必须先创建场景。

    参数说明:
    - name: 场景名称，默认"新建场景"
    - central_body: 中心天体，默认"Earth"
    - start_time/end_time: 可选的时间范围（ISO 8601）
    - description: 可选的场景描述

    注意：本工具用于初始化场景。创建成功后，下一轮对话（前端再次携带 current_scene_name）
    才会建立场景上下文，本轮内不能立即调用其他场景操作工具（如 add_point_entity）。
    """
    bridge = bridge_var.get()
    args: dict = {
        "sceneName": scene_name,
        "centralBody": central_body,
    }
    if start_time:
        args["startTime"] = start_time
    if end_time:
        args["endTime"] = end_time
    if description:
        args["description"] = description

    result  = await bridge.send_tool_call(
        tool_func="createScenario",
        args=args,
    )
    if result["success"]:
        data : dict =result.get("data") or {}
        current_scene_name_var.set(data.get("scene_name"))

    return result


@tool
async def rename_scenario(scene_name: str) -> dict:
    """
    重命名当前场景。

    参数:
    - scene_name: 新的场景名称
    """
    bridge = bridge_var.get()
    result = await bridge.send_tool_call(
        tool_func="renameScenario",
        args={"sceneName": scene_name},
    )
    if result["success"]:
        data : dict =result.get("data") or {}
        current_scene_name_var.set(data.get("scene_name"))
    return result


@tool
async def delete_scene() -> dict:
    """
    清除当前场景的所有内容，包括场景本身和其中所有实体。
    """
    bridge = bridge_var.get()
    result  = await bridge.send_tool_call(
        tool_func="deleteScene",
        args={},
    )
    if result["success"]:
        current_scene_name_var.set(None)
    return result


@tool
async def clear_entities() -> dict:
    """
    清除当前场景中的所有实体，但保留场景本身。
    """
    bridge = bridge_var.get()
    return await bridge.send_tool_call(
        tool_func="clearEntities",
        args={},
    )
