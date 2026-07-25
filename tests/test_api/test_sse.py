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
import contextlib
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
                ev = line[len("event: ") :]
            elif line.startswith("data: "):
                data_line = line[len("data: ") :]
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
    token_contents = [frames[i][1]["content"] for i, ev in enumerate(events) if ev == "token"]
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
            self.tool_call_chunks = [{"name": "create_scenario", "args": '{"scene_name":', "id": "", "index": 0}]

    async def _factory(_thread_id: str) -> Any:
        return _FakeAgent(
            [
                {
                    "event": "on_chat_model_stream",
                    "name": "ChatOpenAI",
                    "metadata": {"langgraph_node": "model"},
                    "data": {"chunk": _FakeChunk("我将")},
                },
                {
                    "event": "on_chat_model_stream",
                    "name": "ChatOpenAI",
                    "metadata": {"langgraph_node": "model"},
                    "data": {"chunk": _ToolCallChunk()},
                },
                {
                    "event": "on_chat_model_stream",
                    "name": "ChatOpenAI",
                    "metadata": {"langgraph_node": "model"},
                    "data": {"chunk": _FakeChunk("为您创建")},
                },
                {
                    "event": "on_chain_end",
                    "name": "orchestrator",
                    "data": {"output": {"structured_response": agent_response}},
                },
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


# ── 客户端断开取消 + 工具超时（T8）─────────────────────────────────────────


async def test_event_generator_cleans_up_on_consumer_cancel(monkeypatch) -> None:
    """客户端断开（消费方取消）→ event_generator finally 兜底清理

    验证契约（T3 deferred / T8 补齐）：当 SSE 消费方在流中途断开（Starlette 在
    下一次 yield 抛 CancelledError）时，event_generator 的 finally 必须执行：
    - agent_task 被 cancel（不挂死）
    - bridge.cleanup() 执行（_closed=True）
    - session_manager.unregister 执行（get_bridge(thread_id) is None）
    - ContextVar bridge_var 被重置（bridge_var.get() is NOT 指向该 bridge）

    方案：直接把 event_generator 当 async generator 驱动（避开 httpx 中途断流
    的 fiddly 细节）。monkeypatch _get_or_create_agent 为「永远 sleeping 的 fake
    agent」（astream_events 不 yield 任何事件），使 run_agent 在 agent_task 内
    阻塞、event_generator 阻塞在 bridge._queue.get()。然后取消消费方 task，
    断言 finally 清理副作用。
    """

    async def _sleeping_factory(_thread_id: str) -> Any:
        """永不产出事件的 fake agent：astream_events 进入即 sleep，run_agent
        因此阻塞在 async for，bridge._queue 永远没有事件 → event_generator
        阻塞在 await bridge._queue.get()，给消费方取消以可观察窗口。"""

        async def _astream(*a: Any, **kw: Any):
            await asyncio.sleep(3600)  # 远超测试时长，确保取消窗口稳定
            yield  # 占位：使函数成为 async generator（执行不到）

        class _A:
            astream_events = _astream

        return _A()

    monkeypatch.setattr(sse_module, "_get_or_create_agent", _sleeping_factory)
    from space_aiagent.bridge import bridge_var

    thread_id = "disconnect-thread-1"
    bridge = sse_module.session_manager.register(thread_id)
    chat_req = sse_module.ChatRequest(
        content="添加文昌地面站",
        thread_id=thread_id,
        message_id="m-disc",
        current_scene_name=None,
    )

    gen = sse_module.event_generator(bridge, chat_req)

    async def _consume() -> list[str]:
        """拉取 generator 帧直到被取消"""
        frames: list[str] = []
        async for frame in gen:
            frames.append(frame)
        return frames

    consumer = asyncio.create_task(_consume())
    # 让 consumer 进入 generator 内部：ContextVar 已 set、agent_task 已 create、
    # 并阻塞在 await bridge._queue.get()（run_agent 内 fake agent 在 sleep）
    # 用桥接的 bridge_var.get 作为「ContextVar 已注入」的探针（必须在 consumer
    # task 上下文外检查不到，因 ContextVar 由 generator 内 set 在 consumer 上下文）
    await asyncio.sleep(0.1)

    # 取消消费方（模拟 Starlette 检测到客户端断开后取消 body 迭代）
    consumer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer

    # finally 副作用断言
    # 1. session 已注销（unregister 已执行）
    assert sse_module.session_manager.get_bridge(thread_id) is None, "客户端断开后 session 未注销，会话泄漏"
    # 2. bridge 已 cleanup（_closed=True）
    assert bridge._closed is True, "客户端断开后 bridge.cleanup 未执行"
    # 3. bridge 内 pending 已清空（cleanup 契约）
    assert len(bridge._pending) == 0
    # 4. ContextVar 已重置：event_generator 的 set/reset 必须在 generator 上下文
    #    成对（避免「Token was created in a different Context」）。bridge_var 在
    #    generator 的 copy_context 内 set/reset，主上下文 get() 应返回默认值
    #    （未 set 状态），不指向本 bridge
    sentinel = object()
    got = bridge_var.get(sentinel)
    assert got is sentinel or got is not bridge, "ContextVar 未在 generator finally 中 reset，主上下文仍可见本 bridge"


async def test_event_generator_cancels_agent_task_on_done(monkeypatch) -> None:
    """正常终止路径：done 帧后 finally 仍 cancel agent_task（对已完成 task 是 no-op）+ unregister

    与上一个断开测试互补：验证终态路径同样清理 session（防 session 泄漏的另一面）。
    用直接驱动 generator 方式而非 HTTP，聚焦 finally 清理副作用。
    """
    monkeypatch.setattr(sse_module, "_get_or_create_agent", _fake_factory_done)

    thread_id = "done-thread-1"
    bridge = sse_module.session_manager.register(thread_id)
    chat_req = sse_module.ChatRequest(
        content="添加文昌地面站",
        thread_id=thread_id,
        message_id="m-done",
        current_scene_name=None,
    )

    frames: list[str] = []
    async for frame in sse_module.event_generator(bridge, chat_req):
        frames.append(frame)

    # 终态后 session 必须注销（防泄漏）
    assert sse_module.session_manager.get_bridge(thread_id) is None
    assert bridge._closed is True
    # 至少产出 done 帧
    parsed = _parse_sse_frames("".join(frames))
    events = [ev for ev, _ in parsed]
    assert "done" in events


async def test_tool_call_timeout_surfaces_as_terminal_via_bridge() -> None:
    """工具超时 → send_tool_call 抛 asyncio.TimeoutError，run_agent except 把它转 error 终态帧

    桥接级验证（不经过 RetryMiddleware，避免重试干扰断言）：send_tool_call 用极短
    timeout 且永不 resolve，确认：
    1. 抛 asyncio.TimeoutError（run_agent 的 except 捕获此异常并 emit error 帧）
    2. tool_call_id 从 _pending 移除（不留 pending 泄漏给后续 cleanup）
    3. 队列只收到 tool_start / tool_args（result / end 因超时未到达不发）

    端到端链路说明（run_agent 依赖的契约，非本测试直接断言）：
        send_tool_call 抛 TimeoutError → run_agent except 捕获 → bridge._emit(ERROR)
        → event_generator 消费到 error 帧 → 在 TERMINAL_EVENTS 中 → break 关闭流
        → finally cleanup。RetryMiddleware 在生产环境会捕获并退避重试（Phase 1B），
        但超时最终仍以 error 终态帧呈现给前端。
    """
    from space_aiagent.bridge.stream_bridge import StreamBridge

    bridge = StreamBridge("timeout-thread-1")

    with pytest.raises(asyncio.TimeoutError):
        await bridge.send_tool_call(
            namespace="scene_tools",
            tool_func="createScenario",
            args={"sceneName": "x"},
            timeout=0.02,  # 极短超时，永不 resolve
        )

    # 超时后 pending 必须清理（不留泄漏给 cleanup）
    assert len(bridge._pending) == 0
    # 队列只有 tool_start / tool_args（无 result / end）
    items: list[dict] = []
    while not bridge._queue.empty():
        items.append(bridge._queue.get_nowait())
    assert [it["event"] for it in items] == ["tool_start", "tool_args"]


async def test_tool_call_timeout_run_agent_emits_error(monkeypatch) -> None:
    """端到端：fake agent 调用 bridge.send_tool_call 超时 → run_agent except emit error 帧 → SSE 终态

    用一个真的 bridge（注入到 bridge_var）+ 一个调用 send_tool_call 的 fake agent，
    不 monkeypatch run_agent 本身，验证 run_agent 的 except 分支把 TimeoutError
    转成 error 帧。走真实 event_generator 路径，断言 error 为终态帧 + session 注销。
    """
    from space_aiagent.bridge import bridge_var

    async def _timeout_factory(_thread_id: str) -> Any:
        """fake agent：astream_events 内调 bridge.send_tool_call（短 timeout），

        触发 TimeoutError，由 run_agent 的 except 捕获 emit error 帧。
        bridge_var 由 event_generator set（在 generator 的 copy context），
        asyncio.create_task 拷贝该 context → agent_task 内 send_tool_call 能拿到 bridge。
        """

        async def _astream(*a: Any, **kw: Any):
            br = bridge_var.get()
            # 极短 timeout 立即超时（永不 resolve，无 tool-result 注入）
            await br.send_tool_call(
                namespace="scene_tools",
                tool_func="createScenario",
                args={"sceneName": "x"},
                timeout=0.02,
            )
            yield  # 执行不到（send_tool_call 抛 TimeoutError）

        class _A:
            astream_events = _astream

        return _A()

    monkeypatch.setattr(sse_module, "_get_or_create_agent", _timeout_factory)

    thread_id = "timeout-e2e-thread-1"
    chat_req = sse_module.ChatRequest(
        content="创建场景",
        thread_id=thread_id,
        message_id="m-timeout",
        current_scene_name=None,
    )

    frames: list[str] = []
    async for frame in sse_module.event_generator(sse_module.session_manager.register(thread_id), chat_req):
        frames.append(frame)

    parsed = _parse_sse_frames("".join(frames))
    events = [ev for ev, _ in parsed]
    # tool_start / tool_args（send_tool_call emit）+ error（run_agent except emit）
    assert "error" in events, f"超时未转为 error 终态帧，实际事件: {events}"
    assert events[-1] == "error"
    # 终态后 session 注销 + bridge cleanup
    assert sse_module.session_manager.get_bridge(thread_id) is None
