"""场景工具结果规范化与 Worker State 边界测试。"""

import inspect
import json
from types import SimpleNamespace
from typing import Annotated, TypedDict
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.models.biz_schemas import ScenarioInfo
from space_aiagent.tools.scene_management import read_tools
from space_aiagent.tools.scene_management.read_tools import (
    _normalize_scenario_query_result,
    open_scenario,
    query_scenario,
)
from space_aiagent.tools.scene_management.write_tools import _build_command


class _ToolState(TypedDict):
    messages: Annotated[list, add_messages]


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


def test_query_result_normalization_unpacks_frontend_list_payload() -> None:
    """前端查询结果统一为 data:{list, count}，归一化层应解包 list 而非当作单场景。"""
    normalized, scenarios = _normalize_scenario_query_result(
        {
            "success": True,
            "code": "SCENE_QUERIED",
            "message": "查询场景成功",
            "data": {
                "list": [
                    {"name": "场景1001", "updateTime": "2026-07-15", "fileUrl": "a.czml", "uploader": {"name": "甲"}},
                    {"name": "场景0942", "updateTime": "2024-11-22", "fileUrl": "c.czml", "uploader": {"name": "丙"}},
                ],
                "count": 2,
            },
        }
    )

    assert scenarios is not None
    assert [item["scene_name"] for item in scenarios] == ["场景1001", "场景0942"]
    assert normalized["data"] == scenarios


def test_query_result_normalization_treats_empty_list_as_empty() -> None:
    normalized, scenarios = _normalize_scenario_query_result(
        {
            "success": True,
            "code": "SCENE_QUERIED",
            "message": "查询场景成功",
            "data": {"list": [], "count": 0},
        }
    )

    assert scenarios == []
    assert normalized["data"] == []


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


def test_open_scenario_does_not_expose_save_decision_argument() -> None:
    parameters = inspect.signature(open_scenario.coroutine).parameters

    assert "scene_name" in parameters
    assert "is_save_on_change" not in parameters


@pytest.mark.parametrize(("resume_value", "expected"), [("yes", True), ({"decision": "no"}, False)])
async def test_open_scenario_interrupts_for_unsaved_changes_and_retries(
    monkeypatch,
    resume_value,
    expected,
) -> None:
    bridge = AsyncMock()
    bridge.send_tool_call.side_effect = [
        {
            "success": False,
            "code": "SCENE_UNSAVED_CHANGES",
            "message": "当前场景有未保存的改动，请确认是否需要保存",
        },
        {"success": True, "code": "SCENE_OPENED", "message": "场景打开成功"},
    ]
    interrupt_values = []

    def fake_interrupt(value):
        interrupt_values.append(value)
        return resume_value

    monkeypatch.setattr(read_tools, "interrupt", fake_interrupt)
    token = bridge_var.set(bridge)
    try:
        command = await open_scenario.coroutine(
            runtime=SimpleNamespace(tool_call_id="call-open"),
            scene_name="火箭测试",
        )
    finally:
        bridge_var.reset(token)

    assert interrupt_values == [
        {
            "description": "当前场景有未保存的改动，请确认是否需要保存",
            "options": ["yes", "no"],
        }
    ]
    assert bridge.send_tool_call.await_args_list[0].kwargs["args"] == {"sceneName": "火箭测试"}
    assert bridge.send_tool_call.await_args_list[1].kwargs["args"] == {
        "sceneName": "火箭测试",
        "isSaveOnChange": expected,
    }
    payload = json.loads(command.update["messages"][0].content)
    assert payload["code"] == "SCENE_OPENED"


async def test_open_scenario_resumes_at_interrupt_in_langgraph() -> None:
    bridge = AsyncMock()
    unsaved = {
        "success": False,
        "code": "SCENE_UNSAVED_CHANGES",
        "message": "当前场景有未保存的改动，请确认是否需要保存",
    }
    bridge.send_tool_call.side_effect = [
        unsaved,
        unsaved,
        {"success": True, "code": "SCENE_OPENED", "message": "场景打开成功"},
    ]

    async def open_node(_state):
        return await open_scenario.coroutine(
            runtime=SimpleNamespace(tool_call_id="call-open"),
            scene_name="火箭测试",
        )

    builder = StateGraph(_ToolState)
    builder.add_node("open", open_node)
    builder.add_edge(START, "open")
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "open-unsaved-test"}}

    token = bridge_var.set(bridge)
    try:
        interrupted = await graph.ainvoke({"messages": []}, config=config)
        resumed = await graph.ainvoke(Command(resume={"decision": "yes"}), config=config)
    finally:
        bridge_var.reset(token)

    assert interrupted["__interrupt__"][0].value == {
        "description": "当前场景有未保存的改动，请确认是否需要保存",
        "options": ["yes", "no"],
    }
    assert "__interrupt__" not in resumed
    assert bridge.send_tool_call.await_args_list[0].kwargs["args"] == {"sceneName": "火箭测试"}
    # LangGraph 恢复时节点从头执行；生产 StreamBridge 会按幂等键复用这次失败回告。
    assert bridge.send_tool_call.await_args_list[1].kwargs["args"] == {"sceneName": "火箭测试"}
    assert bridge.send_tool_call.await_args_list[2].kwargs["args"] == {
        "sceneName": "火箭测试",
        "isSaveOnChange": True,
    }
    assert json.loads(resumed["messages"][-1].content)["code"] == "SCENE_OPENED"


def test_scene_write_tool_command_does_not_create_domain_state_channel() -> None:
    command = _build_command(
        {"success": True, "code": "SCENE_CREATED", "current_scene_name": "场景A"},
        SimpleNamespace(tool_call_id="call-create"),
    )

    assert set(command.update) == {"messages"}
    assert json.loads(command.update["messages"][0].content)["current_scene_name"] == "场景A"
