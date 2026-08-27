"""实体查询与定位工具契约测试。"""

from unittest.mock import AsyncMock

from space_aiagent.bridge import bridge_var
from space_aiagent.agents.workers import load_workers
from space_aiagent.tools.contracts import get_workflow_tool_contract
from space_aiagent.tools.entity_management.tools import (
    delete_entities,
    delete_entities_interrupt_description,
    query_entities,
    zoom_to,
)
from space_aiagent.tools.registry import get_tools


def test_zoom_to_is_discoverable_and_requires_open_scene() -> None:
    tools = {item.name: item for item in get_tools(["entity_management"])}

    assert tools["zoom_to"] is zoom_to
    assert get_workflow_tool_contract(zoom_to).requires == {"scene.opened"}


def test_delete_entities_is_discoverable_and_declares_selective_delete_effect() -> None:
    tools = {item.name: item for item in get_tools(["entity_management"])}

    assert tools["delete_entities"] is delete_entities
    contract = get_workflow_tool_contract(delete_entities)
    assert contract.requires == {"scene.opened"}
    assert contract.effects == {"entity.deleted"}


def test_delete_entities_interrupt_description_by_name_filter() -> None:
    """entity_name 非空时，中断文案须指明匹配名称。"""
    description = delete_entities_interrupt_description(
        {"name": "delete_entities", "args": {"entity_name": "LEO2LTO"}},
        state={},
        runtime={},
    )

    assert "LEO2LTO" in description
    assert "全部实体" not in description


def test_delete_entities_interrupt_description_empty_name_warns_full_wipe() -> None:
    """entity_name 为空（含缺省/纯空白）时，中断文案须提示删除全部实体。"""
    for args in ({}, {"entity_name": ""}, {"entity_name": "  "}):
        description = delete_entities_interrupt_description(
            {"name": "delete_entities", "args": args},
            state={},
            runtime={},
        )

        assert "全部实体" in description


def test_load_workers_injects_dynamic_interrupt_description() -> None:
    """load_workers 须把 YAML 静态描述替换为按入参动态生成的工厂。"""
    workers = {item["name"]: item for item in load_workers(None)}

    config = workers["entity-agent"]["interrupt_on"]["delete_entities"]
    assert callable(config["description"])
    assert config["allowed_decisions"] == ["approve", "reject"]


async def test_delete_entities_forwards_name_filter_to_frontend() -> None:
    bridge = AsyncMock()
    bridge.send_tool_call.return_value = {
        "success": True,
        "code": "ENTITIES_DELETED",
        "data": {"count": 2},
    }
    token = bridge_var.set(bridge)
    try:
        result = await delete_entities.coroutine(entity_name="LEO")
    finally:
        bridge_var.reset(token)

    assert result["code"] == "ENTITIES_DELETED"
    bridge.send_tool_call.assert_awaited_once_with(
        namespace="entity_tools",
        tool_func="deleteEntities",
        args={"entityName": "LEO"},
    )


async def test_query_entities_forwards_name_filter_to_frontend() -> None:
    bridge = AsyncMock()
    bridge.send_tool_call.return_value = {
        "success": True,
        "code": "ENTITIES_LIST",
        "data": {"entities": [], "count": 0},
    }
    token = bridge_var.set(bridge)
    try:
        result = await query_entities.coroutine(entity_name="LEO2LTO")
    finally:
        bridge_var.reset(token)

    assert result["code"] == "ENTITIES_LIST"
    bridge.send_tool_call.assert_awaited_once_with(
        namespace="entity_tools",
        tool_func="queryEntities",
        args={"entityType": "satellite", "entityName": "LEO2LTO"},
    )


async def test_zoom_to_forwards_selected_entity_name_to_frontend() -> None:
    bridge = AsyncMock()
    bridge.send_tool_call.return_value = {
        "success": True,
        "code": "ZOOM_TO_SUCCESS",
        "data": {"entity_name": "LEO2LTO"},
    }
    token = bridge_var.set(bridge)
    try:
        result = await zoom_to.coroutine(entity_name="LEO2LTO")
    finally:
        bridge_var.reset(token)

    assert result["code"] == "ZOOM_TO_SUCCESS"
    bridge.send_tool_call.assert_awaited_once_with(
        namespace="entity_tools",
        tool_func="zoomTo",
        args={"entityName": "LEO2LTO"},
    )
