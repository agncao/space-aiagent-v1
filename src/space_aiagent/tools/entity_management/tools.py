"""
实体管理工具

在场景中添加、查询、删除和定位实体。
工具通过远程桥接发送指令到前端 Cesium 执行。

桥接注入: V2 SSE handler 在启动 WorkflowRun 前设置 bridge_var，Worker 工具通过 get() 获取。

前置条件: 场景必须已打开（由 @workflow_tool 契约和 WorkerToolValidationMiddleware 校验）
"""

import inspect

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.biz_schemas import EntityConfig, EntityPosition
from space_aiagent.models.enums import EntityType
from space_aiagent.tools.contracts import workflow_tool

_NAMESPACE: str = "entity_tools"


@workflow_tool(
    requires={"scene.opened"},
    effects={"entity.created"},
)
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
        namespace=_NAMESPACE,
        tool_func="addPointEntity",
        args=args,
    )


@workflow_tool(requires={"scene.opened"})
@tool
async def query_entities(entity_name: str = "", entity_type: EntityType = EntityType.SATELLITE) -> dict:
    """按名称模糊匹配，查询并统计当前已打开场景中的实体及总数。

    Args:
        entity_name: 模糊匹配的实体名，传空字符串表示查询全部实体。

    Returns:
        匹配到的实体名称列表及实体总数。
    """

    tool_func = inspect.currentframe().f_code.co_name
    args: dict = {"entityType": entity_type.value, "entityName": entity_name}

    bridge = bridge_var.get()
    return await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )


def delete_entities_interrupt_description(tool_call: dict, state: dict, runtime: dict) -> str:
    """HITL 中断文案：``entity_name`` 为空提示全量清空，非空提示按名删除。"""

    entity_name = str((tool_call.get("args") or {}).get("entity_name") or "").strip()
    if entity_name:
        return f"你确认要删除名称匹配「{entity_name}」的实体吗？此操作不可撤销。"
    return "你确认要删除当前场景内的全部实体吗？此操作不可撤销。"


@workflow_tool(
    requires={"scene.opened"},
    effects={"entity.deleted"},
)
@tool
async def delete_entities(entity_name: str = "") -> dict:
    """按名称删除当前场景中的实体，保留场景本身。

    ``entity_name`` 非空时，删除名称模糊匹配的所有实体；仅当用户明确要求
    “删除全部实体”或“清空场景实体”时才传空字符串。不得因缺少实体名称而默认
    传空字符串，以免误删场景中的全部实体。

    Args:
        entity_name: 用于模糊匹配的实体名称；空字符串表示删除当前场景中的全部实体。

    Returns:
        删除操作的执行结果。
    """
    bridge = bridge_var.get()

    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(delete_entities, locals())

    return await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )


@workflow_tool(requires={"scene.opened"})
@tool
async def zoom_to(entity_name: str) -> dict:
    """将 Cesium 视图定位到当前场景中名称完全匹配的实体。

    Args:
        entity_name: 从 query_entities 返回候选中选定的真实实体名称。

    Returns:
        前端定位实体后的执行结果。
    """
    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(zoom_to, locals())

    bridge = bridge_var.get()
    return await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
