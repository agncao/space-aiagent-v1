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
