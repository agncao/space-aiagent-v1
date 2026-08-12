"""场景工具结果规范化与 Worker State 边界测试。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage

from space_aiagent.bridge import bridge_var
from space_aiagent.models.biz_schemas import ScenarioInfo
from space_aiagent.tools.scene_management.read_tools import _normalize_scenario_query_result, query_scenario
from space_aiagent.tools.scene_management.write_tools import _build_command


def test_scenario_info_only_keeps_display_fields() -> None:
    scenario = ScenarioInfo.from_frontend(
        {
            "name": "场景1001",
            "updateTime": "2026-07-15 15:56:29",
            "fileUrl": "admin/场景1001/场景1001.czml",
            "uploader": {
                "name": "系统管理员",
                "loginName": "admin",
                "password": "secret",
                "salt": "sensitive",
            },
        }
    )

    assert scenario is not None
    serialized = json.dumps(scenario.model_dump(), ensure_ascii=False)
    assert scenario.uploader_name == "系统管理员"
    assert "password" not in serialized
    assert "salt" not in serialized
    assert "secret" not in serialized


def test_query_result_normalization_keeps_every_valid_scenario() -> None:
    normalized, scenarios = _normalize_scenario_query_result(
        {
            "success": True,
            "code": "SCENE_QUERIED",
            "message": "查询场景成功",
            "data": [
                {"name": "场景1001", "updateTime": "2026-07-15", "fileUrl": "a.czml", "uploader": {"name": "甲"}},
                {"name": "场景1000", "updateTime": "2024-12-19", "fileUrl": "b.czml", "uploader": {"name": "乙"}},
                {"name": "场景0942", "updateTime": "2024-11-22", "fileUrl": "c.czml", "uploader": {"name": "丙"}},
            ],
        }
    )

    assert scenarios is not None
    assert [item["scene_name"] for item in scenarios] == ["场景1001", "场景1000", "场景0942"]
    assert normalized["data"] == scenarios


def test_query_result_normalization_preserves_scene_name_verbatim() -> None:
    normalized, scenarios = _normalize_scenario_query_result(
        {
            "success": True,
            "code": "SCENE_QUERIED",
            "message": "查询场景成功",
            "data": [
                {"name": "场景0942_ 1个火箭_1个卫星关节动画"},
                {"name": "A地球-CZ4C火箭Demon"},
            ],
        }
    )

    expected_names = ["场景0942_ 1个火箭_1个卫星关节动画", "A地球-CZ4C火箭Demon"]
    assert scenarios is not None
    assert [item["scene_name"] for item in scenarios] == expected_names
    assert [item["scene_name"] for item in normalized["data"]] == expected_names


async def test_query_tool_writes_sanitized_results_only_to_tool_message() -> None:
    bridge = AsyncMock()
    bridge.send_tool_call.return_value = {
        "success": True,
        "code": "SCENE_QUERIED",
        "message": "查询场景成功",
        "data": [
            {
                "name": "场景1001",
                "updateTime": "2026-07-15 15:56:29",
                "fileUrl": "admin/场景1001/场景1001.czml",
                "uploader": {"name": "系统管理员", "password": "secret", "salt": "sensitive"},
            },
            {
                "name": "场景1000",
                "updateTime": "2024-12-19 19:34:46",
                "fileUrl": "admin/场景1000/场景1000.czml",
                "uploader": {"name": "系统管理员", "password": "secret", "salt": "sensitive"},
            },
        ],
    }
    token = bridge_var.set(bridge)
    try:
        command = await query_scenario.coroutine(
            runtime=SimpleNamespace(tool_call_id="call-query"),
            scene_name="场景",
        )
    finally:
        bridge_var.reset(token)

    assert set(command.update) == {"messages"}
    tool_message = command.update["messages"][0]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.tool_call_id == "call-query"
    assert "password" not in tool_message.content
    assert "salt" not in tool_message.content
    assert "场景1001" in tool_message.content
    assert "场景1000" in tool_message.content


def test_scene_write_tool_command_does_not_create_domain_state_channel() -> None:
    command = _build_command(
        {"success": True, "code": "SCENE_CREATED", "current_scene_name": "场景A"},
        SimpleNamespace(tool_call_id="call-create"),
    )

    assert set(command.update) == {"messages"}
    assert json.loads(command.update["messages"][0].content)["current_scene_name"] == "场景A"
