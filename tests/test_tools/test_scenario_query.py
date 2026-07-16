"""场景查询结果规范化与渲染测试。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage

from space_aiagent.bridge import bridge_var
from space_aiagent.models.response_schema import response_util
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse, ResponseCode
from space_aiagent.models.schemas import ScenarioInfo
from space_aiagent.tools.scene_management.read_tools import _normalize_scenario_query_result, query_scenario


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


async def test_query_tool_writes_sanitized_results_to_state_and_tool_message() -> None:
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

    assert [item["scene_name"] for item in command.update["scenario_query_results"]] == ["场景1001", "场景1000"]
    tool_message = command.update["messages"][0]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.tool_call_id == "call-query"
    assert "password" not in tool_message.content
    assert "salt" not in tool_message.content
    assert "场景1001" in tool_message.content
    assert "场景1000" in tool_message.content


def test_scene_query_renders_complete_table_even_when_model_uses_wrong_code() -> None:
    response = AgentResponse(
        status="info",
        code=ResponseCode.ENTITIES_LIST,
        summary='只找到了3个名为"场景"的场景',
    )
    scenarios = [
        {
            "scene_name": "场景1001",
            "update_time": "2026-07-15 15:56:29",
            "file_url": "admin/场景1001/场景1001.czml",
            "uploader_name": "系统管理员",
        },
        {
            "scene_name": "场景0942_ 1个火箭",
            "update_time": "2024-11-22 16:38:31",
            "file_url": "admin/场景0942_ 1个火箭/场景0942.czml",
            "uploader_name": "系统管理员",
        },
        {
            "scene_name": "场景2",
            "update_time": "2023-05-03 17:56:33",
            "file_url": "zhangpc/场景2/场景2.czml",
            "uploader_name": "张鹏程",
        },
        {
            "scene_name": "场景1",
            "update_time": "2023-05-03 17:56:19",
            "file_url": "zhangpc/场景1/场景1.czml",
            "uploader_name": "张鹏程",
        },
    ]

    rendered = response_util.render(response, scenario_infos=scenarios)

    assert "共找到 4 个场景" in rendered
    assert "| 场景名 | 更新时间 | 上传人 |" in rendered
    assert "场景1001" in rendered
    assert "场景0942_ 1个火箭" in rendered
    assert "场景2" in rendered
    assert "场景1" in rendered
    assert "%E5%9C%BA%E6%99%AF1001" in rendered
    assert "只找到了3个" not in rendered


def test_scene_query_empty_result_has_deterministic_message() -> None:
    response = AgentResponse(status="info", code=ResponseCode.SCENE_QUERIED, summary="查询完成")

    assert response_util.render(response, scenario_infos=[]) == "未查询到符合条件的场景。"


def test_scene_query_can_render_scenarios_from_agent_response_args() -> None:
    response = AgentResponse(
        status="info",
        code=ResponseCode.SCENE_QUERIED,
        summary="查询完成",
        args={
            "scenarios": [
                {
                    "scene_name": "场景1001",
                    "update_time": "2026-07-15 15:56:29",
                    "file_url": "admin/场景1001/场景1001.czml",
                    "uploader_name": "系统管理员",
                }
            ]
        },
    )

    rendered = response_util.render(response)

    assert "共找到 1 个场景" in rendered
    assert "场景1001" in rendered
