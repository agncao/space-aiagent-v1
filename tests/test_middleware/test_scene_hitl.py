"""SceneAgentHitlMiddleware 单测

覆盖 open_scenario SKILL.md 的两个条件性 HITL 中断点（中间件驱动）：

中断点 1 — query_scenario 命中多个场景（>=2）：
  - 0/1 个场景不中断，原样放行
  - >=2 个场景调 interrupt() 暂停图（is_custom=True / hitl_select / 候选列表）
  - resume 返回选中场景名 → 写回 ToolMessage（selected_scene），保留 scenario_query_results

中断点 2 — open_scenario 返回 SCENE_UNSAVED_CHANGES：
  - 成功 / 其它码不中断，原样放行
  - SCENE_UNSAVED_CHANGES 调 interrupt() 暂停（is_custom=True / hitl_yn）
  - resume 返回 save_on_change → 经 bridge 带 isSaveOnChange 重试 openScenario，
    成功时同步 current_scene_name
  - bridge 未注入 → 返回原始结果（系统级降级）

interrupt() 在图运行时由 LangGraph 接管（首次抛出暂停 / resume 返回 payload），
单测里 patch ``space_aiagent.middleware.scene_hitl.interrupt`` 分别模拟「暂停」
与「resume 完成」两条路径。
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.middleware.scene_hitl import SceneAgentHitlMiddleware

# 工具名（与 read_tools 的 @tool name 对齐，中间件用 tools.query_scenario.name 比较）
_QUERY = "query_scenario"
_OPEN = "open_scenario"


@pytest.fixture(autouse=True)
def _stub_get_config():
    """get_config() 在无 LangGraph runnable 上下文时会 raise RuntimeError，
    单测里 stub 成固定 thread_id 的 config（中间件仅用于日志归因）。"""
    with patch(
        "space_aiagent.middleware.scene_hitl.get_config",
        return_value={"configurable": {"thread_id": "t-test"}},
    ):
        yield


def _make_request(
    tool_name: str,
    args: dict | None = None,
    scene_name: str | None = None,
    tool_call_id: str = "call_test",
):
    """构造 mock ToolCallRequest（ducktyping：需 .tool_call / .state）。"""
    return type(
        "Req",
        (),
        {
            "tool_call": {"name": tool_name, "args": args or {}, "id": tool_call_id},
            "state": {"current_scene_name": scene_name},
        },
    )()


def _query_command(scenarios: list[dict], tool_call_id: str = "call_q") -> Command:
    """模拟 query_scenario 的返回：Command(update=scenario_query_results + messages)。"""
    payload = {"success": True, "code": "OK", "message": "", "data": scenarios}
    return Command(
        update={
            "scenario_query_results": scenarios,
            "messages": [
                ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def _open_command(
    code: str,
    tool_call_id: str = "call_o",
    success: bool = False,
    current_scene_name: str | None = None,
) -> Command:
    """模拟 open_scenario 的返回：Command(update=messages)，content 为前端 result JSON。"""
    payload: dict = {"success": success, "code": code, "message": ""}
    if current_scene_name:
        payload["current_scene_name"] = current_scene_name
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False),
                    tool_call_id=tool_call_id,
                )
            ]
        }
    )


def _tool_message(result) -> ToolMessage:
    """从结果里取唯一的 ToolMessage。"""
    if isinstance(result, Command):
        msgs = result.update.get("messages", [])
        assert msgs, "update.messages 为空"
        return msgs[0]
    assert isinstance(result, ToolMessage)
    return result


# ── 中断点 1：query_scenario 多场景选择 ──────────────────────────────────


async def test_query_single_scene_no_interrupt():
    """命中唯一场景 → 不中断，原样返回。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        original = _query_command([{"scene_name": "唯一场景"}], "call_q")
        mw = SceneAgentHitlMiddleware()

        result = await mw.awrap_tool_call(
            _make_request(_QUERY, tool_call_id="call_q"),
            AsyncMock(return_value=original),
        )

        assert result is original
        intr.assert_not_called()


async def test_query_zero_scene_no_interrupt():
    """未命中场景 → 不中断，原样返回（LLM 自行告知未找到）。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        original = _query_command([], "call_q")
        mw = SceneAgentHitlMiddleware()

        result = await mw.awrap_tool_call(
            _make_request(_QUERY, tool_call_id="call_q"),
            AsyncMock(return_value=original),
        )

        assert result is original
        intr.assert_not_called()


async def test_query_multi_scene_triggers_interrupt():
    """>=2 个场景 → 调 interrupt() 暂停图，payload 是 is_custom 四件套 + 候选列表。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        # 模拟图运行时首次 interrupt 抛出暂停（单测里用异常表征「图在此挂起」）
        intr.side_effect = RuntimeError("PAUSED")
        scenarios = [
            {"scene_name": "测试场景A", "update_time": "2026-08-01", "uploader_name": "u1"},
            {"scene_name": "测试场景B", "update_time": "2026-08-02", "uploader_name": "u2"},
        ]
        mw = SceneAgentHitlMiddleware()

        with pytest.raises(RuntimeError, match="PAUSED"):
            await mw.awrap_tool_call(
                _make_request(_QUERY, tool_call_id="call_q"),
                AsyncMock(return_value=_query_command(scenarios, "call_q")),
            )

        intr.assert_called_once()
        payload = intr.call_args.args[0]
        assert payload["is_custom"] is True
        assert payload["interrupt_type"] == "hitl_select"
        assert payload["data"]["scene_info_list"] == [
            {"scene_name": "测试场景A", "update_time": "2026-08-01", "uploader_name": "u1"},
            {"scene_name": "测试场景B", "update_time": "2026-08-02", "uploader_name": "u2"},
        ]


async def test_query_multi_scene_resume_writes_back_selection():
    """resume 返回选中场景名 → 写回 ToolMessage（selected_scene），保留 scenario_query_results。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        intr.return_value = {"scene_name": "测试场景B"}
        scenarios = [
            {"scene_name": "测试场景A"},
            {"scene_name": "测试场景B"},
        ]
        mw = SceneAgentHitlMiddleware()

        result = await mw.awrap_tool_call(
            _make_request(_QUERY, tool_call_id="call_q"),
            AsyncMock(return_value=_query_command(scenarios, "call_q")),
        )

        # 选中场景写回 ToolMessage
        tm = _tool_message(result)
        content = json.loads(tm.content)
        assert content["selected_scene"] == "测试场景B"
        assert "测试场景B" in content["message"]
        assert tm.tool_call_id == "call_q"
        # scenario_query_results 保留（_replace_tool_message 只替换 messages）
        assert result.update["scenario_query_results"] == scenarios


async def test_query_multi_scene_resume_missing_scene_name_falls_back():
    """resume payload 缺 scene_name → 放行原始结果（兜底，不应发生）。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        intr.return_value = {}  # 缺 scene_name
        original = _query_command([{"scene_name": "A"}, {"scene_name": "B"}], "call_q")
        mw = SceneAgentHitlMiddleware()

        result = await mw.awrap_tool_call(
            _make_request(_QUERY, tool_call_id="call_q"),
            AsyncMock(return_value=original),
        )

        assert result is original


# ── 中断点 2：open_scenario 未保存变更确认 ──────────────────────────────


async def test_open_success_no_interrupt():
    """open_scenario 成功（非 SCENE_UNSAVED_CHANGES）→ 不中断，原样返回。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        original = _open_command(
            "SCENE_OPENED", tool_call_id="call_o", success=True, current_scene_name="目标场景"
        )
        mw = SceneAgentHitlMiddleware()
        bridge_var.set(AsyncMock())

        result = await mw.awrap_tool_call(
            _make_request(_OPEN, args={"scene_name": "目标场景"}, tool_call_id="call_o"),
            AsyncMock(return_value=original),
        )

        assert result is original
        intr.assert_not_called()


async def test_open_unsaved_triggers_interrupt():
    """返回 SCENE_UNSAVED_CHANGES → 调 interrupt() 暂停，payload 含当前/目标场景名。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        intr.side_effect = RuntimeError("PAUSED")
        bridge_var.set(AsyncMock())
        mw = SceneAgentHitlMiddleware()

        with pytest.raises(RuntimeError, match="PAUSED"):
            await mw.awrap_tool_call(
                _make_request(
                    _OPEN,
                    args={"scene_name": "目标场景"},
                    scene_name="当前场景",
                    tool_call_id="call_o",
                ),
                AsyncMock(return_value=_open_command("SCENE_UNSAVED_CHANGES", "call_o")),
            )

        intr.assert_called_once()
        payload = intr.call_args.args[0]
        assert payload["is_custom"] is True
        assert payload["interrupt_type"] == "hitl_yn"
        assert payload["data"]["scene_name"] == "当前场景"
        assert payload["data"]["target_scene_name"] == "目标场景"


async def test_open_unsaved_resume_save_true_retries_via_bridge():
    """resume save_on_change=True → 经 bridge 带 isSaveOnChange=True 重试，成功时同步场景名。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        intr.return_value = {"save_on_change": True}
        bridge = AsyncMock()
        bridge.send_tool_call.return_value = {
            "success": True,
            "code": "SCENE_OPENED",
            "current_scene_name": "目标场景",
        }
        bridge_var.set(bridge)
        mw = SceneAgentHitlMiddleware()

        result = await mw.awrap_tool_call(
            _make_request(
                _OPEN,
                args={"scene_name": "目标场景"},
                scene_name="当前场景",
                tool_call_id="call_o",
            ),
            AsyncMock(return_value=_open_command("SCENE_UNSAVED_CHANGES", "call_o")),
        )

        # 重试经 bridge：tool_func=camelCase，args 含 isSaveOnChange
        bridge.send_tool_call.assert_awaited_once()
        kwargs = bridge.send_tool_call.await_args.kwargs
        assert kwargs["namespace"] == "scene_tools"
        assert kwargs["tool_func"] == "openScenario"
        assert kwargs["args"] == {"sceneName": "目标场景", "isSaveOnChange": True}

        # 最终结果替换 ToolMessage，content 为 bridge 返回，current_scene_name 同步
        tm = _tool_message(result)
        assert json.loads(tm.content)["current_scene_name"] == "目标场景"
        assert result.update["current_scene_name"] == "目标场景"


async def test_open_unsaved_resume_save_false_retries_via_bridge():
    """resume save_on_change=False → bridge 带 isSaveOnChange=False 重试。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        intr.return_value = {"save_on_change": False}
        bridge = AsyncMock()
        bridge.send_tool_call.return_value = {"success": True, "code": "SCENE_OPENED"}
        bridge_var.set(bridge)
        mw = SceneAgentHitlMiddleware()

        await mw.awrap_tool_call(
            _make_request(
                _OPEN,
                args={"scene_name": "目标场景"},
                scene_name="当前场景",
                tool_call_id="call_o",
            ),
            AsyncMock(return_value=_open_command("SCENE_UNSAVED_CHANGES", "call_o")),
        )

        kwargs = bridge.send_tool_call.await_args.kwargs
        assert kwargs["args"]["isSaveOnChange"] is False


async def test_open_unsaved_no_bridge_returns_original():
    """bridge 未注入（resume 续跑时异常）→ 返回原始结果，不抛异常（系统级降级）。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        intr.return_value = {"save_on_change": True}
        bridge_var.set(None)
        original = _open_command("SCENE_UNSAVED_CHANGES", "call_o")
        mw = SceneAgentHitlMiddleware()

        result = await mw.awrap_tool_call(
            _make_request(
                _OPEN,
                args={"scene_name": "目标场景"},
                scene_name="当前场景",
                tool_call_id="call_o",
            ),
            AsyncMock(return_value=original),
        )

        assert result is original


# ── 其它工具不误触发 ────────────────────────────────────────────────────


async def test_unrelated_tool_passthrough():
    """非 query_scenario/open_scenario 的工具 → 原样放行，不中断。"""
    with patch("space_aiagent.middleware.scene_hitl.interrupt") as intr:
        bridge_var.set(AsyncMock())
        mw = SceneAgentHitlMiddleware()
        original = {"success": True}

        result = await mw.awrap_tool_call(
            _make_request("add_point_entity", scene_name="场景A"),
            AsyncMock(return_value=original),
        )

        assert result is original
        intr.assert_not_called()
