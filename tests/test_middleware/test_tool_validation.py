"""ToolValidationMiddleware 单测

验证中间件对工具调用的前置条件校验:
- bridge 注入校验
- 场景上下文校验（白名单外）

校验失败时中间件返回 ToolMessage（与 @tool 函数返回 dict 后由 ToolNode 自动包装的
行为一致），避免裸 dict 触发 deepagents FilesystemMiddleware 的 AssertionError。

每个 async 测试函数由 pytest-asyncio 创建独立 task，
ContextVar 在测试函数体内 set，自动隔离。
"""

import json
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage

from space_aiagent.bridge import bridge_var, current_scene_name_var
from space_aiagent.middleware.tool_validation import ToolValidationMiddleware


def _make_request(tool_name: str, tool_call_id: str = "call_test"):
    """构造 mock ToolCallRequest（ducktyping，只需要 tool_call 属性）"""
    return type(
        "Req",
        (),
        {"tool_call": {"name": tool_name, "args": {}, "id": tool_call_id}},
    )()


def _parse_tool_message(result) -> dict:
    """断言返回是 ToolMessage 并解析其 content 为 dict"""
    assert isinstance(result, ToolMessage), f"期望 ToolMessage，实际 {type(result)}"
    return json.loads(result.content)


async def test_no_scene_failfast_for_entity_tool():
    """无场景上下文 → entity 工具被中间件拦截，handler 不被调用"""
    bridge_var.set(AsyncMock())
    current_scene_name_var.set(None)

    handler = AsyncMock(return_value={"success": True})
    mw = ToolValidationMiddleware()

    result = await mw.awrap_tool_call(_make_request("add_point_entity"), handler)

    data = _parse_tool_message(result)
    assert data["success"] is False
    assert "场景" in data["message"]
    handler.assert_not_called()


async def test_create_scenario_exempt_from_scene_check():
    """create_scenario 在白名单内，无场景也能调用"""
    bridge_var.set(AsyncMock())
    current_scene_name_var.set(None)

    handler = AsyncMock(return_value={"success": True})
    mw = ToolValidationMiddleware()

    result = await mw.awrap_tool_call(_make_request("create_scenario"), handler)

    # 白名单工具透传 handler 返回值，不包装成 ToolMessage
    assert result == {"success": True}
    handler.assert_called_once()


async def test_no_bridge_failfast():
    """bridge 未注入 → 任何工具都失败"""
    bridge_var.set(None)
    current_scene_name_var.set("场景A")

    handler = AsyncMock()
    mw = ToolValidationMiddleware()

    result = await mw.awrap_tool_call(_make_request("create_scenario"), handler)

    data = _parse_tool_message(result)
    assert data["success"] is False
    assert "bridge" in data["message"]
    handler.assert_not_called()


async def test_all_checks_pass_call_handler():
    """场景 + bridge 都就绪 → 正常调用 handler"""
    bridge_var.set(AsyncMock())
    current_scene_name_var.set("场景A")

    handler = AsyncMock(return_value={"success": True, "data": "ok"})
    mw = ToolValidationMiddleware()

    result = await mw.awrap_tool_call(_make_request("add_point_entity"), handler)

    assert result["data"] == "ok"
    handler.assert_called_once()


async def test_unknown_tool_name_still_validates():
    """未知工具名（不在白名单）→ 仍走场景校验"""
    bridge_var.set(AsyncMock())
    current_scene_name_var.set(None)

    handler = AsyncMock()
    mw = ToolValidationMiddleware()

    result = await mw.awrap_tool_call(_make_request("some_future_tool"), handler)

    data = _parse_tool_message(result)
    assert data["success"] is False
    assert "场景" in data["message"]
    handler.assert_not_called()


async def test_failfast_returns_tool_message_with_call_id():
    """校验失败必须返回带 tool_call_id 的 ToolMessage，否则 FilesystemMiddleware 会 AssertionError"""
    bridge_var.set(AsyncMock())
    current_scene_name_var.set(None)

    handler = AsyncMock()
    mw = ToolValidationMiddleware()

    result = await mw.awrap_tool_call(
        _make_request("delete_scene", tool_call_id="call_abc_123"),
        handler,
    )

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call_abc_123"
