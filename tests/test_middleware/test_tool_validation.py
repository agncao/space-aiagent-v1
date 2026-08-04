"""ToolValidationMiddleware 单测

验证中间件对工具调用的前置条件校验:
- bridge 注入校验：失败时返回 ToolMessage（deepagents FilesystemMiddleware 要求）
- 场景上下文校验：失败时返回 Command(goto=END)，终止子 Agent 图，
  ToolMessage 携带 NO_SCENE 错误；状态由 LangGraph 持久化到 checkpointer

current_scene_name 通过 SpaceAgentState 双向同步（替代 ContextVar），
测试在 mock ToolCallRequest.state 中设置该字段。
"""

import json
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.middleware.subagent_tool_validation import SubagentToolValidationMiddleware


def _make_request(
    tool_name: str,
    tool_call_id: str = "call_test",
    scene_name: str | None = None,
):
    """构造 mock ToolCallRequest（ducktyping，需要 tool_call 和 state 属性）

    Args:
        scene_name: state.current_scene_name 的值；None 表示无场景
    """
    return type(
        "Req",
        (),
        {
            "tool_call": {"name": tool_name, "args": {}, "id": tool_call_id},
            "state": {"current_scene_name": scene_name},
        },
    )()


def _parse_tool_message(result) -> dict:
    """断言返回是 ToolMessage 并解析其 content 为 dict"""
    assert isinstance(result, ToolMessage), f"期望 ToolMessage，实际 {type(result)}"
    return json.loads(result.content)


def _assert_no_scene_command(result, expected_tool_call_id: str = "call_test") -> None:
    """断言 result 是 Command(goto=END)，update.messages[0] 是携带 NO_SCENE 的 ToolMessage"""
    assert isinstance(result, Command), f"期望 Command，实际 {type(result)}"
    assert result.goto == END
    msgs = result.update.get("messages", [])
    assert len(msgs) == 1, f"期望 update.messages 长度 1，实际 {len(msgs)}"
    msg = msgs[0]
    assert isinstance(msg, ToolMessage), f"期望 ToolMessage，实际 {type(msg)}"
    assert msg.tool_call_id == expected_tool_call_id
    payload = json.loads(msg.content)
    assert payload["code"] == "NO_SCENE"
    assert payload["status"] == "error"
    assert payload["success"] is False


async def test_no_scene_returns_terminal_command():
    """无场景上下文 → 返回 Command(goto=END)，跳过子 Agent LLM 调用，状态持久化"""
    bridge_var.set(AsyncMock())

    handler = AsyncMock(return_value={"success": True})
    mw = SubagentToolValidationMiddleware()

    result = await mw.awrap_tool_call(_make_request("add_point_entity"), handler)

    _assert_no_scene_command(result)
    handler.assert_not_called()


async def test_no_scene_command_for_unknown_tool():
    """未知工具名（不在白名单）→ 同样返回 Command(goto=END)"""
    bridge_var.set(AsyncMock())

    handler = AsyncMock()
    mw = SubagentToolValidationMiddleware()

    result = await mw.awrap_tool_call(_make_request("some_future_tool"), handler)

    _assert_no_scene_command(result)
    handler.assert_not_called()


async def test_no_scene_command_preserves_tool_call_id():
    """Command 里的 ToolMessage 必须保留原 tool_call_id（协议要求）"""
    bridge_var.set(AsyncMock())

    handler = AsyncMock()
    mw = SubagentToolValidationMiddleware()

    result = await mw.awrap_tool_call(
        _make_request("delete_scene", tool_call_id="call_abc_123"),
        handler,
    )

    _assert_no_scene_command(result, expected_tool_call_id="call_abc_123")


async def test_create_scenario_exempt_from_scene_check():
    """create_scenario 在白名单内，无场景也能调用"""
    bridge_var.set(AsyncMock())

    handler = AsyncMock(return_value={"success": True})
    mw = SubagentToolValidationMiddleware()

    result = await mw.awrap_tool_call(_make_request("create_scenario"), handler)

    assert result == {"success": True}
    handler.assert_called_once()


async def test_no_bridge_failfast():
    """bridge 未注入 → 返回 ToolMessage（系统级错误，让 LLM 兜底）"""
    bridge_var.set(None)

    handler = AsyncMock()
    mw = SubagentToolValidationMiddleware()

    result = await mw.awrap_tool_call(
        _make_request("create_scenario", scene_name="场景A"),
        handler,
    )

    data = _parse_tool_message(result)
    assert data["success"] is False
    assert "bridge" in data["message"]
    handler.assert_not_called()


async def test_no_bridge_returns_tool_message_with_call_id():
    """bridge 失败的 ToolMessage 必须带 tool_call_id（FilesystemMiddleware 要求）"""
    bridge_var.set(None)

    handler = AsyncMock()
    mw = SubagentToolValidationMiddleware()

    result = await mw.awrap_tool_call(
        _make_request("delete_scene", tool_call_id="call_abc_123", scene_name="场景A"),
        handler,
    )

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call_abc_123"


async def test_all_checks_pass_call_handler():
    """场景 + bridge 都就绪 → 正常调用 handler"""
    bridge_var.set(AsyncMock())

    handler = AsyncMock(return_value={"success": True, "data": "ok"})
    mw = SubagentToolValidationMiddleware()

    result = await mw.awrap_tool_call(
        _make_request("add_point_entity", scene_name="场景A"),
        handler,
    )

    assert result["data"] == "ok"
    handler.assert_called_once()


# ---------------------------------------------------------------------------
# 字符串化 null 归一化（_normalize_null_args）
# ---------------------------------------------------------------------------

from space_aiagent.middleware.subagent_tool_validation import _normalize_null_args  # noqa: E402


def test_normalize_null_args_converts_string_none():
    """字符串 "None" / "null" / "" → Python None（顶层 str 值）"""
    assert _normalize_null_args({"scene_name": "None"}) == {"scene_name": None}
    assert _normalize_null_args({"scene_name": "null"}) == {"scene_name": None}
    assert _normalize_null_args({"scene_name": ""}) == {"scene_name": None}


def test_normalize_null_args_case_insensitive_and_stripped():
    """大小写不敏感 + 容忍前后空白"""
    assert _normalize_null_args({"scene_name": "NONE"}) == {"scene_name": None}
    assert _normalize_null_args({"scene_name": " Null "}) == {"scene_name": None}


def test_normalize_null_args_preserves_real_values():
    """真实场景名与非 str 值原样保留"""
    assert _normalize_null_args({"scene_name": "测试场景"}) == {"scene_name": "测试场景"}
    assert _normalize_null_args({"is_save_on_change": True}) == {"is_save_on_change": True}
    assert _normalize_null_args({"count": 0}) == {"count": 0}


def test_normalize_null_args_non_dict_passthrough():
    """非 dict 入参原样返回（防御）"""
    assert _normalize_null_args(None) is None
    assert _normalize_null_args("None") == "None"


def test_normalize_null_args_does_not_mutate_input():
    """不就地修改原 dict"""
    original = {"scene_name": "None"}
    _normalize_null_args(original)
    assert original == {"scene_name": "None"}


class _OverrideableRequest:
    """支持 override() 的 mock ToolCallRequest（ducktyping）"""

    def __init__(self, tool_call: dict, state: dict | None = None) -> None:
        self.tool_call = tool_call
        self.state = state or {}

    def override(self, **changes) -> "_OverrideableRequest":
        return _OverrideableRequest(
            tool_call={**self.tool_call, **changes.get("tool_call", {})},
            state=changes.get("state", self.state),
        )


async def test_middleware_forwards_normalized_args_to_handler():
    """LLM 吐 scene_name="None" → handler 收到的是 None（白名单工具，跳过场景校验）"""
    bridge_var.set(AsyncMock())

    captured: dict = {}

    async def handler(req):
        captured["args"] = req.tool_call.get("args")
        return {"success": True}

    mw = SubagentToolValidationMiddleware()
    request = _OverrideableRequest(
        tool_call={"name": "open_scenario", "args": {"scene_name": "None"}, "id": "call_1"},
        state={"current_scene_name": "场景A"},
    )

    await mw.awrap_tool_call(request, handler)

    assert captured["args"] == {"scene_name": None}
