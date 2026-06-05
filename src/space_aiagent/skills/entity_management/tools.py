"""
实体管理工具

在场景中添加各类实体（卫星、地面站、传感器等）。
工具通过远程桥接发送指令到前端 Cesium 执行。

桥接注入: 使用 bridge.bridge_var (ContextVar) 在会话级别注入 bridge 实例，
         由 websocket handler 在创建 Agent 前设置，工具函数通过 get() 获取。

前置条件: 场景必须已创建（前端会校验，未创建场景会返回错误）
"""

import logging

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var
from space_aiagent.models.enums import EntityType
from space_aiagent.models.schemas import EntityConfig, EntityPosition

logger = logging.getLogger(__name__)


@tool(args_schema=EntityConfig)
async def add_point_entity(
    entity_type: EntityType,
    name: str,
    position: EntityPosition,
    properties: dict | None = None,
) -> dict:
    """
    在场景中添加点实体（卫星、地面站、飞机、传感器等）。

    支持的实体类型: place(地点), target(目标点), facility(地面站),
                   aircraft(飞机), missile(导弹), satellite(卫星),
                   sensor(传感器), groundVehicle(地面车), ship(船),
                   launchVehicle(火箭), lineTarget(线目标), areaTarget(区域目标)

    前置条件: 必须先有场景，否则前端会返回错误。
    """
    # 获取当前会话的 bridge 实例
    bridge = bridge_var.get()
    if bridge is None:
        logger.error("bridge 未注入，无法发送 addPointEntity 指令")
        return {"success": False, "message": "bridge 未注入，无法发送指令"}

    # 构建前端 addPointEntity 所需参数（camelCase 与前端对接）
    args = {
        "entityType": entity_type.value if isinstance(entity_type, EntityType) else entity_type,
        "name": name,
        "position": {
            "longitude": position.longitude,
            "latitude": position.latitude,
            "height": position.height,
        },
    }
    if properties:
        args["properties"] = properties

    # 通过 bridge 发送 tool_call 到前端，await Future 等待执行结果
    # 前端对应方法: SceneTools.addPointEntity(input)
    # 前端内部调用: ProtoTreeData.addEntityByData(data) 创建 CZML 兼容实体
    result = await bridge.send_tool_call(
        tool_func="addPointEntity",
        args=args,
    )
    return result


@tool
async def query_scenario_entities() -> dict:
    """
    查询当前场景中的所有实体名称列表。
    """
    # 获取当前会话的 bridge 实例
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
