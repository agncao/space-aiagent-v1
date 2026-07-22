"""POST /chat SSE 端点测试

通过 httpx.ASGITransport 直连 FastAPI app，mock 掉 _get_or_create_agent
注入 fake agent，验证：
- test_chat_streams_done：fake agent 输出 on_chain_end + structured_response，
  断言 SSE 帧含 event: done 且流正确终止
- test_chat_concurrent_409：同 thread_id 已有活跃 session → 409
- test_chat_emits_token_frame：fake agent 输出 on_chat_model_stream chunk，
  断言 event: token 帧出现在 done 之前
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from space_aiagent.api import sse as sse_module
from space_aiagent.models.response_schema.agent_struct_response import (
    AgentResponse,
    ResponseCode,
)


class _FakeChunk:
    """模拟 langchain chunk：content 可能是 str 或 list[block]"""

    def __init__(self, content: Any) -> None:
        self.content = content


class _FakeAgent:
    """伪 Agent：按预设事件序列 yield astream_events"""

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def astream_events(self, *args: Any, **kwargs: Any):
        for ev in self._events:
            yield ev


async def _fake_factory_done(_thread_id: str) -> Any:
    """返回直接产出 AgentResponse on_chain_end 的 fake agent"""
    agent_response = AgentResponse(
        status="success",
        code=ResponseCode.ENTITY_CREATED,
        summary="已添加文昌地面站",
        suggestions=[],
    )
    return _FakeAgent(
        [
            {
                "event": "on_chain_end",
                "name": "orchestrator",
                "data": {"output": {"structured_response": agent_response}},
            }
        ]
    )


async def _fake_factory_token(_thread_id: str) -> Any:
    """返回先产出 token、再产出 AgentResponse 的 fake agent"""
    agent_response = AgentResponse(
        status="success",
        code=ResponseCode.ENTITY_CREATED,
        summary="已添加文昌地面站",
        suggestions=[],
    )
    return _FakeAgent(
        [
            {
                "event": "on_chat_model_stream",
                "name": "scene-agent",
                "data": {"chunk": _FakeChunk("正在")},
            },
            {
                "event": "on_chat_model_stream",
                "name": "scene-agent",
                "data": {"chunk": _FakeChunk("处理")},
            },
            {
                "event": "on_chain_end",
                "name": "orchestrator",
                "data": {"output": {"structured_response": agent_response}},
            },
        ]
    )


def _parse_sse_frames(body: str) -> list[tuple[str, dict]]:
    """把 SSE 流体文本解析为 (event, data_dict) 列表"""
    import json

    frames: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        ev = ""
        data_line = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                data_line = line[len("data: "):]
        if ev:
            frames.append((ev, json.loads(data_line) if data_line else {}))
    return frames


@pytest.fixture
def chat_request_body() -> dict:
    return {
        "content": "添加文昌地面站",
        "thread_id": "test-thread-1",
        "message_id": "m-1",
        "current_scene_name": None,
    }


@pytest.fixture(autouse=True)
def _clear_session():
    """每个测试前清空 session_manager，避免互相干扰"""
    sse_module.session_manager._bridges.clear()
    yield
    sse_module.session_manager._bridges.clear()


async def test_chat_streams_done(monkeypatch, chat_request_body: dict) -> None:
    """fake agent 产出 AgentResponse → SSE 流以 done 帧终止"""
    monkeypatch.setattr(sse_module, "_get_or_create_agent", _fake_factory_done)

    from space_aiagent.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/chat", json=chat_request_body)

    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.text)
    events = [ev for ev, _ in frames]
    assert "done" in events, f"未在帧中找到 done，实际事件: {events}"
    # done 帧应是最后一个
    assert events[-1] == "done"
    # done data 携带 rendered content
    done_idx = events.index("done")
    assert "已添加文昌地面站" in frames[done_idx][1]["content"]
    # 流结束后 session 应已注销
    assert sse_module.session_manager.get_bridge(chat_request_body["thread_id"]) is None


async def test_chat_concurrent_409(monkeypatch, chat_request_body: dict) -> None:
    """同 thread_id 已有活跃 session → 第二个 POST 返 409"""
    monkeypatch.setattr(sse_module, "_get_or_create_agent", _fake_factory_done)

    from space_aiagent.main import app

    # 预先注册一个活跃 session（模拟 agent 正在跑）
    sse_module.session_manager.register(chat_request_body["thread_id"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/chat", json=chat_request_body)

    assert resp.status_code == 409


async def test_chat_emits_token_frame(monkeypatch, chat_request_body: dict) -> None:
    """fake agent 输出 on_chat_model_stream chunk → SSE 流含 event: token 帧"""
    monkeypatch.setattr(sse_module, "_get_or_create_agent", _fake_factory_token)

    from space_aiagent.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/chat", json=chat_request_body)

    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.text)
    events = [ev for ev, _ in frames]

    # token 帧应出现且在 done 之前
    assert "token" in events, f"未在帧中找到 token，实际事件: {events}"
    assert events.index("token") < events.index("done")
    # token 内容拼接应还原
    token_contents = [
        frames[i][1]["content"] for i, ev in enumerate(events) if ev == "token"
    ]
    assert "".join(token_contents) == "正在处理"


async def test_chat_token_filters_tool_call_chunks(monkeypatch, chat_request_body: dict) -> None:
    """spike 结论验证：结构化输出的 tool_call 碎片（content 空）不发 token，只发自由文本 content。

    spike（2026-07-21 真 LLM）显示 orchestrator/子 agent 的结构化输出以
    tool_call_chunks 流式（content 为空），自由文本以 content 流式。fake agent
    产出 content文本 → tool_call碎片(content空) → content文本 → done，断言
    只有两个 token 帧（碎片被过滤），且 source 非空（无 metadata 时 "agent" 兜底）。
    """
    agent_response = AgentResponse(
        status="success",
        code=ResponseCode.ENTITY_CREATED,
        summary="已创建",
        suggestions=[],
    )

    class _ToolCallChunk(_FakeChunk):
        """模拟结构化输出的 tool_call 流式碎片：content 空，带 tool_call_chunks"""

        def __init__(self) -> None:
            super().__init__("")
            self.tool_call_chunks = [
                {"name": "create_scenario", "args": '{"scene_name":', "id": "", "index": 0}
            ]

    async def _factory(_thread_id: str) -> Any:
        return _FakeAgent(
            [
                {"event": "on_chat_model_stream", "name": "ChatOpenAI",
                 "metadata": {"langgraph_node": "model"},
                 "data": {"chunk": _FakeChunk("我将")}},
                {"event": "on_chat_model_stream", "name": "ChatOpenAI",
                 "metadata": {"langgraph_node": "model"},
                 "data": {"chunk": _ToolCallChunk()}},
                {"event": "on_chat_model_stream", "name": "ChatOpenAI",
                 "metadata": {"langgraph_node": "model"},
                 "data": {"chunk": _FakeChunk("为您创建")}},
                {"event": "on_chain_end", "name": "orchestrator",
                 "data": {"output": {"structured_response": agent_response}}},
            ]
        )

    monkeypatch.setattr(sse_module, "_get_or_create_agent", _factory)
    from space_aiagent.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/chat", json=chat_request_body)

    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.text)
    token_frames = [d for ev, d in frames if ev == "token"]
    # tool_call 碎片被过滤，只余两个 content token
    assert len(token_frames) == 2, f"tool_call 碎片未被过滤，token 帧数: {len(token_frames)}"
    assert "".join(d["content"] for d in token_frames) == "我将为您创建"
    # source 从 metadata.langgraph_node 解析（spike 实测统一为 "model"）
    assert all(d["source"] == "model" for d in token_frames), "source 应取 langgraph_node='model'"


async def test_chat_emits_error_on_exception(monkeypatch, chat_request_body: dict) -> None:
    """fake agent 抛异常 → SSE 流以 error 帧终止"""

    async def _failing_factory(_thread_id: str) -> Any:
        async def _astream(*a: Any, **kw: Any):
            raise RuntimeError("boom")
            yield  # 占位：使函数成为 async generator（执行不到）

        class _A:
            astream_events = _astream

        return _A()

    monkeypatch.setattr(sse_module, "_get_or_create_agent", _failing_factory)

    from space_aiagent.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/chat", json=chat_request_body)

    assert resp.status_code == 200  # 流本身成功建立
    frames = _parse_sse_frames(resp.text)
    events = [ev for ev, _ in frames]
    assert events[-1] == "error"
    assert "boom" in frames[-1][1]["message"]


# ── POST /tool-result 测试 ────────────────────────────────────────────────


async def test_tool_result_resolves_pending_future() -> None:
    """POST /tool-result → 定位 bridge → resolve 对应 Future

    验证：手动在 bridge._pending 中放入已知 id 的 pending Future，
    POST 携带匹配的 tool_call_id + thread_id 后，Future 应被 set_result，
    且结果字段排除 type/thread_id/tool_call_id/tool_func（与 resolve_tool_result 契约一致）。
    """
    from space_aiagent.main import app

    thread_id = "tr-thread-1"
    tool_call_id = "tcid-1234"

    # 注册活跃 session（POST /tool-result 前提条件）
    bridge = sse_module.session_manager.register(thread_id)

    # 手动构造 pending Future（模拟 send_tool_call 内部 await 的 Future）
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    bridge._pending[tool_call_id] = future

    body = {
        "tool_func": "createScenario",
        "args": {"sceneName": "测试场景"},
        "tool_call_id": tool_call_id,
        "thread_id": thread_id,
        "success": True,
        "message": "ok",
        "data": {"sceneId": 42},
        "code": "SCENE_CREATED",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/tool-result", json=body)

    # 响应应立即返回 {ok: true}
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}

    # Future 应已 resolve（done），携带排除 type/thread_id/tool_call_id/tool_func 的字段
    assert future.done(), "Future 未被 resolve"
    result = future.result()
    assert result["success"] is True
    assert result["message"] == "ok"
    assert result["data"] == {"sceneId": 42}
    assert result["code"] == "SCENE_CREATED"
    assert result["args"] == {"sceneName": "测试场景"}
    # resolve_tool_result 契约：排除 type/thread_id/tool_call_id/tool_func
    assert "type" not in result
    assert "thread_id" not in result
    assert "tool_call_id" not in result
    assert "tool_func" not in result

    # bridge._pending 应已弹出对应 id
    assert tool_call_id not in bridge._pending


async def test_tool_result_no_session_404() -> None:
    """POST /tool-result 对无活跃会话的 thread_id → 404"""
    from space_aiagent.main import app

    body = {
        "tool_func": "createScenario",
        "args": {},
        "tool_call_id": "tcid-none",
        "thread_id": "non-existent-thread",
        "success": True,
        "message": "",
        "data": None,
        "code": "",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/tool-result", json=body)

    assert resp.status_code == 404
    assert "无活跃会话" in resp.json()["detail"]


# ── POST /chat/{thread_id}/resume 测试（interrupt 协议占位）────────────────


async def test_resume_returns_501() -> None:
    """POST /chat/{thread_id}/resume — interrupt 未实现，返 501 NotImplemented"""
    from space_aiagent.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/space/chat/some-thread/resume",
            json={"resume": {"supplement": "补充信息"}},
        )

    assert resp.status_code == 501
    assert "interrupt" in resp.json()["detail"]
