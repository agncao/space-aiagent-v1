"""实体查询与定位工具契约测试。"""

from unittest.mock import AsyncMock

from space_aiagent.bridge import bridge_var
from space_aiagent.tools.contracts import get_workflow_tool_contract
from space_aiagent.tools.entity_management.tools import query_entities, zoom_to
from space_aiagent.tools.registry import get_tools


def test_zoom_to_is_discoverable_and_requires_open_scene() -> None:
    tools = {item.name: item for item in get_tools(["entity_management"])}

    assert tools["zoom_to"] is zoom_to
    assert get_workflow_tool_contract(zoom_to).requires == {"scene.opened"}


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
        args={"entityName": "LEO2LTO"},
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
