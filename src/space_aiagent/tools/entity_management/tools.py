"""
实体管理工具

在场景中添加实体。
工具通过远程桥接发送指令到前端 Cesium 执行。

桥接注入: 使用 bridge.bridge_var (ContextVar) 在会话级别注入 bridge 实例，
         由 websocket handler 在创建 Agent 前设置，工具函数通过 get() 获取。

前置条件: 场景必须已创建（由 ToolValidationMiddleware 在工具调用前校验）
"""

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var
from space_aiagent.models.enums import EntityType
from space_aiagent.models.schemas import EntityConfig, EntityPosition


@tool(args_schema=EntityConfig)
async def add_point_entity(
    entity_type: EntityType,
    name: str,
    position: EntityPosition,
    properties: dict | None = None,
) -> dict:
    """
    在场景中添加实体。支持的实体类型: 
        地点(place)、目标点(target)、地面站(facility)、飞机(aircraft)、
        导弹(missile)、卫星(satellite)、传感器(sensor)、地面车/地面车辆(groundVehicle)、
        船(ship)、火箭(launchVehicle)、线目标(lineTarget)、区域目标(areaTarget)、链路(chain)。
    """
    bridge = bridge_var.get()

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
    return await bridge.send_tool_call(
        tool_func="addPointEntity",
        args=args,
    )


@tool
async def query_entities() -> dict:
    """
    查询/统计当前场景中的所有实体名称列表及总数。
    """
    bridge = bridge_var.get()
    return await bridge.send_tool_call(
        tool_func="queryEntities",
        args={},
    )

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
