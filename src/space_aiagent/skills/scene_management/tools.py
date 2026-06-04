"""
场景管理工具

所有工具不直接执行操作，而是通过远程工具桥接（bridge）发送指令到前端 Cesium 执行。

桥接注入: 使用 bridge.bridge_var (ContextVar) 在会话级别注入 bridge 实例，
         由 websocket handler 在创建 Agent 前设置，工具函数通过 get() 获取。
"""
import logging

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var
from space_aiagent.models.schemas import ScenarioConfig

logger = logging.getLogger(__name__)


@tool(args_schema=ScenarioConfig)
async def create_scenario(
    name: str = "新建场景",
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
    """
    # 获取当前会话的 bridge 实例
    bridge = bridge_var.get()
    if bridge is None:
        logger.error("bridge 未注入，无法发送 createScenario 指令")
        return {"success": False, "message": "bridge 未注入，无法发送指令"}

    # 构建前端 createScenario 所需参数（camelCase 与前端对接）
    args: dict = {
        "name": name,
        "centralBody": central_body,
    }
    if start_time:
        args["startTime"] = start_time
    if end_time:
        args["endTime"] = end_time
    if description:
        args["description"] = description

    # 前端对应方法: SceneTools.createScenario(config)
    # 前端内部调用: yyastk.CurrentScenario.createScene({name, centralBody, startTime, endTime, description})
    result = await bridge.send_tool_call(
        tool_func="createScenario",
        args=args,
    )
    return result


@tool
async def rename_scenario(name: str) -> dict:
    """
    重命名当前场景。

    参数:
    - name: 新的场景名称
    """
    bridge = bridge_var.get()
    if bridge is None:
        logger.error("bridge 未注入，无法发送 renameScenario 指令")
        return {"success": False, "message": "bridge 未注入，无法发送指令"}

    # 前端对应方法: SceneTools.renameScenerio(arg)
    # 前端内部调用: yyastk.CurrentScenario.rename(name)
    result = await bridge.send_tool_call(
        tool_func="renameScenario",
        args={"name": name},
    )
    return result


@tool
async def clear_scene() -> dict:
    """
    清除当前场景的所有内容，包括场景本身和其中所有实体。
    """
    bridge = bridge_var.get()
    if bridge is None:
        logger.error("bridge 未注入，无法发送 clearScene 指令")
        return {"success": False, "message": "bridge 未注入，无法发送指令"}

    # 前端对应方法: SceneTools.clearScene()
    # 前端内部调用: yyastk.CurrentScenario.clearScene()
    result = await bridge.send_tool_call(
        tool_func="clearScene",
        args={},
    )
    return result


@tool
async def clear_entities() -> dict:
    """
    清除当前场景中的所有实体，但保留场景本身。
    """
    bridge = bridge_var.get()
    if bridge is None:
        logger.error("bridge 未注入，无法发送 clearEntities 指令")
        return {"success": False, "message": "bridge 未注入，无法发送指令"}

    # 前端对应方法: SceneTools.clearEntities()
    # 前端内部调用: yyastk.CurrentScenario.clearEntities()
    result = await bridge.send_tool_call(
        tool_func="clearEntities",
        args={},
    )
    return result


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

    # 前端对应方法: SceneTools.queryScenario(sceneName)
    # 前端内部调用: HTTP GET /m/scene/getScene?name=...&count=100
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

    # 前端对应方法: SceneTools.queryScenarioEntities(config)
    # 前端内部调用: 遍历 currentScenario.dataSource.entities.values 提取实体名称
    result = await bridge.send_tool_call(
        tool_func="queryScenarioEntities",
        args={},
    )
    return result
