"""POST /chat SSE 端点测试

通过 httpx.ASGITransport 直连 FastAPI app，mock 掉 _get_or_create_agent
注入 fake agent，验证：
- test_chat_streams_done：fake agent 流空 + aget_state 返 structured_response，
  断言 SSE 帧含 event: done 且流正确终止
- test_chat_concurrent_409：同 thread_id 已有活跃 session → 409
- test_chat_emits_token_frame：fake agent 产出 messages token chunk，
  断言 event: token 帧出现在 done 之前
- test_chat_interrupt_*：fake agent 产出 values+interrupts chunk → interrupt + done(interrupted)
- test_chat_then_resume_hitl：/chat 触发 interrupt → /resume 用 Command 续跑 → token + done
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.types import Command, Interrupt

from space_aiagent.api import sse as sse_module
from space_aiagent.models.response_schema.agent_struct_response import (
    AgentResponse,
    ResponseCode,
)

# ── fake 基础设施（astream v2 chunk 形状 + aget_state）──────────────────────


class _FakeChunk:
    """模拟 langchain 消息 chunk：content 可能是 str 或 list[block]。

    作为 messages 流 chunk 的 ``data[0]``（消息本体）。
    """

    def __init__(self, content: Any) -> None:
        self.content = content


class _ToolCallChunk(_FakeChunk):
    """模拟结构化输出的 tool_call 流式碎片：content 空，带 tool_call_chunks。"""

    def __init__(self) -> None:
        super().__init__("")
        self.tool_call_chunks = [{"name": "create_scenario", "args": '{"scene_name":', "id": "", "index": 0}]


class _ToolMessageChunk:
    """模拟 ToolMessage：type=='tool'，content 是工具结果 JSON（应被跳过不发 token）。"""

    type = "tool"

    def __init__(self, content: str = '{"success": true}') -> None:
        self.content = content


class _StateSnapshot:
    """模拟 langgraph StateSnapshot：run_agent 用 .values / .next。"""

    def __init__(self, values: dict | None = None, next_: tuple = ()) -> None:
        self.values = values or {}
        self.next = next_


class _FakeAgent:
    """伪 Agent：按预设 v2 chunk 序列 yield astream，aget_state 返固定快照。

    astream 按 input 类型分流（chat=dict / resume=Command），用于 interrupt→resume
    端到端测试。``last_input`` 捕获最近一次 astream 入参，供断言 resume 收到 Command。
    """

    def __init__(self, chat_chunks: list[dict] | None = None, state: _StateSnapshot | None = None) -> None:
        self._chat_chunks = chat_chunks or []
        self._resume_chunks: list[dict] = []
        self._state = state or _StateSnapshot()
        self.last_input: Any = None

    def set_resume_chunks(self, chunks: list[dict]) -> _FakeAgent:
        self._resume_chunks = chunks
        return self

    async def astream(self, input_: Any, config: Any = None, **kwargs: Any):
        self.last_input = input_
        # resume（Command）与 chat（dict）走不同 chunk 序列
        chunks = self._resume_chunks if isinstance(input_, Command) else self._chat_chunks
        for c in chunks:
            yield c

    async def aget_state(self, config: Any = None) -> _StateSnapshot:
        return self._state


def _msg_chunk(content: Any, node: str = "model") -> dict:
    """构造 messages 流 v2 chunk：data=(msg, metadata)。"""
    return {"type": "messages", "ns": (), "data": (_FakeChunk(content), {"langgraph_node": node})}


def _interrupt_chunk(action_requests: list[dict], review_configs: list[dict] | None = None) -> dict:
    """构造 values 流 v2 chunk：携带 interrupts（声明式 interrupt_on 形状）。"""
    return {
        "type": "values",
        "ns": (),
        "interrupts": [
            Interrupt(
                value={
                    "action_requests": action_requests,
                    "review_configs": review_configs or [],
                }
            )
        ],
    }


def _agent_response() -> AgentResponse:
    return AgentResponse(
        status="success",
        code=ResponseCode.ENTITY_CREATED,
        summary="已添加文昌地面站",
        suggestions=[],
    )


async def _factory_done(_thread_id: str) -> Any:
    """流空 + aget_state 返 structured_response 的 fake agent。"""
    return _FakeAgent(state=_StateSnapshot({"structured_response": _agent_response()}))


async def _factory_token(_thread_id: str) -> Any:
    """先吐两个 token chunk、再 aget_state 返 structured_response 的 fake agent。"""
    return _FakeAgent(
        chat_chunks=[_msg_chunk("正在"), _msg_chunk("处理")],
        state=_StateSnapshot({"structured_response": _agent_response()}),
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
    """每个测试前清空 session_manager 与 agent 缓存，避免互相干扰"""
    sse_module.session_manager._bridges.clear()
    sse_module._agent_cache.clear()
    yield
    sse_module.session_manager._bridges.clear()
    sse_module._agent_cache.clear()


# ── POST /chat 基础路径 ────────────────────────────────────────────────────


async def test_chat_streams_done(monkeypatch, chat_request_body: dict) -> None:
    """fake agent aget_state 返 structured_response → SSE 流以 done 帧终止"""
    monkeypatch.setattr(sse_module, "_get_or_create_agent", _factory_done)

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
    monkeypatch.setattr(sse_module, "_get_or_create_agent", _factory_done)

    from space_aiagent.main import app

    # 预先注册一个活跃 session（模拟 agent 正在跑）
    sse_module.session_manager.register(chat_request_body["thread_id"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/chat", json=chat_request_body)

    assert resp.status_code == 409


async def test_chat_emits_token_frame(monkeypatch, chat_request_body: dict) -> None:
    """fake agent 输出 messages token chunk → SSE 流含 event: token 帧"""
    monkeypatch.setattr(sse_module, "_get_or_create_agent", _factory_token)

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


async def test_chat_token_filters_tool_call_and_tool_messages(monkeypatch, chat_request_body: dict) -> None:
    """spike 结论 + ToolMessage 跳过验证：
    - 结构化输出的 tool_call 碎片（content 空）不发 token
    - ToolMessage（type=='tool'，content 是工具结果 JSON）不发 token（切 astream 后必须显式跳过）
    只发真正的自由文本 content token，且 source 取 metadata.langgraph_node。
    """
    agent_response = AgentResponse(
        status="success",
        code=ResponseCode.ENTITY_CREATED,
        summary="已创建",
        suggestions=[],
    )
    chunks = [
        _msg_chunk("我将"),
        {"type": "messages", "ns": (), "data": (_ToolCallChunk(), {"langgraph_node": "model"})},
        {"type": "messages", "ns": (), "data": (_ToolMessageChunk('{"success":true}'), {"langgraph_node": "model"})},
        _msg_chunk("为您创建"),
    ]

    async def _factory(_thread_id: str) -> Any:
        return _FakeAgent(chat_chunks=chunks, state=_StateSnapshot({"structured_response": agent_response}))

    monkeypatch.setattr(sse_module, "_get_or_create_agent", _factory)
    from space_aiagent.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/chat", json=chat_request_body)

    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.text)
    token_frames = [d for ev, d in frames if ev == "token"]
    # tool_call 碎片 + ToolMessage 被过滤，只余两个 content token
    assert len(token_frames) == 2, f"碎片/ToolMessage 未被过滤，token 帧数: {len(token_frames)}"
    assert "".join(d["content"] for d in token_frames) == "我将为您创建"
    # source 从 metadata.langgraph_node 解析（spike 实测统一为 "model"）
    assert all(d["source"] == "model" for d in token_frames), "source 应取 langgraph_node='model'"


async def test_chat_emits_error_on_exception(monkeypatch, chat_request_body: dict) -> None:
    """fake agent astream 抛异常 → SSE 流以 error 帧终止"""

    async def _failing_factory(_thread_id: str) -> Any:
        async def _astream(self, *args: Any, **kwargs: Any):
            raise RuntimeError("boom")
            yield  # 占位：使函数成为 async generator（执行不到）

        class _A:
            astream = _astream

            async def aget_state(self, config: Any = None) -> Any:  # 执行不到
                ...

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


# ── interrupt + resume（HITL 主链路）────────────────────────────────────────


async def test_chat_interrupt_emits_interrupt_and_done(monkeypatch, chat_request_body: dict) -> None:
    """fake agent 产出 values+interrupts chunk → SSE 含 interrupt(hitl_approval) + done(interrupted)

    验证声明式 interrupt_on（HumanInTheLoopMiddleware）触发的中断：
    - interrupt 帧 interrupt_type=hitl_approval，携带 action_requests / review_configs
    - 随后 done 帧 interrupted=True，且为终态帧
    - 流关闭、session 注销
    """
    action_requests = [{"name": "delete_scene", "args": {}, "description": "即将删除当前场景，请确认。"}]
    review_configs = [{"action_name": "delete_scene", "allowed_decisions": ["approve", "reject"]}]

    async def _factory(_thread_id: str) -> Any:
        return _FakeAgent(chat_chunks=[_interrupt_chunk(action_requests, review_configs)])

    monkeypatch.setattr(sse_module, "_get_or_create_agent", _factory)
    from space_aiagent.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/space/chat", json=chat_request_body)

    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.text)
    events = [ev for ev, _ in frames]

    assert "interrupt" in events, f"未在帧中找到 interrupt，实际事件: {events}"
    intr_idx = events.index("interrupt")
    intr_data = frames[intr_idx][1]
    assert intr_data["interrupt_type"] == "hitl_approval"
    assert intr_data["action_requests"] == action_requests
    assert intr_data["review_configs"] == review_configs
    # done 紧随其后，interrupted=True，且为终态
    assert events[intr_idx + 1] == "done"
    assert frames[intr_idx + 1][1]["interrupted"] is True
    assert events[-1] == "done"
    # 流关闭、session 注销
    assert sse_module.session_manager.get_bridge(chat_request_body["thread_id"]) is None


async def test_chat_then_resume_hitl(monkeypatch, chat_request_body: dict) -> None:
    """端到端 HITL：/chat 触发 interrupt → /resume 用 Command 续跑 → token + done

    同一 fake agent（按 input 类型分流）：
    - /chat（dict）：吐 interrupt chunk → interrupt + done(interrupted)
    - /resume（Command）：吐 token chunk → aget_state 返 structured_response → done
    断言：resume 流含 token + done，且 fake 的 astream 收到了 Command(resume=decisions)。
    """
    action_requests = [
        {"name": "rename_scenario", "args": {"scene_name": "新名"}, "description": "即将重命名，请确认。"}
    ]
    review_configs = [{"action_name": "rename_scenario", "allowed_decisions": ["approve", "reject"]}]

    shared = _FakeAgent(
        chat_chunks=[_interrupt_chunk(action_requests, review_configs)],
        state=_StateSnapshot({"structured_response": _agent_response()}),
    ).set_resume_chunks([_msg_chunk("已重命名")])

    async def _factory(_thread_id: str) -> Any:
        return shared  # /chat 与 /resume 共享同一实例（_get_or_create_agent 被 monkeypatch）

    monkeypatch.setattr(sse_module, "_get_or_create_agent", _factory)
    from space_aiagent.main import app

    transport = ASGITransport(app=app)
    decisions = {"decisions": [{"type": "approve"}]}

    # 1) /chat 触发 interrupt
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        chat_resp = await ac.post("/api/v1/space/chat", json=chat_request_body)
    chat_frames = _parse_sse_frames(chat_resp.text)
    chat_events = [ev for ev, _ in chat_frames]
    assert "interrupt" in chat_events
    assert chat_events[-1] == "done"
    assert chat_frames[-1][1]["interrupted"] is True

    # 2) /resume 用 Command 续跑
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resume_resp = await ac.post("/api/v1/space/chat/test-thread-1/resume", json={"resume": decisions})
    assert resume_resp.status_code == 200
    resume_frames = _parse_sse_frames(resume_resp.text)
    resume_events = [ev for ev, _ in resume_frames]

    # resume 流含 token + 终态 done（非 interrupted）
    assert "token" in resume_events, f"resume 流未含 token，实际事件: {resume_events}"
    assert resume_events[-1] == "done"
    assert resume_frames[-1][1].get("interrupted") is not True
    # run_agent 把 resume 数据以 Command(resume=...) 形式喂给 agent.astream
    assert isinstance(shared.last_input, Command)
    assert shared.last_input.resume == decisions
    # resume 流结束、session 注销
    assert sse_module.session_manager.get_bridge(chat_request_body["thread_id"]) is None


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


# ── 客户端断开取消 + 工具超时（T8）─────────────────────────────────────────


async def test_stream_chat_response_cleans_up_on_consumer_cancel(monkeypatch) -> None:
    """客户端断开（消费方取消）→ stream_chat_response finally 兜底清理

    验证契约（T3 deferred / T8 补齐）：当 SSE 消费方在流中途断开（Starlette 在
    下一次 yield 抛 CancelledError）时，stream_chat_response 的 finally 必须执行：
    - agent_task 被 cancel（不挂死）
    - bridge.cleanup() 执行（_closed=True）
    - session_manager.unregister 执行（get_bridge(thread_id) is None）
    - ContextVar bridge_var 被重置（bridge_var.get() is NOT 指向该 bridge）

    方案：直接把 stream_chat_response 当 async generator 驱动（避开 httpx 中途断流
    的 fiddly 细节）。monkeypatch _get_or_create_agent 为「永远 sleeping 的 fake
    agent」（astream 不 yield 任何事件），使 run_agent 在 agent_task 内阻塞、
    stream_chat_response 阻塞在 bridge._queue.get()。然后取消消费方 task，
    断言 finally 清理副作用。
    """

    async def _sleeping_factory(_thread_id: str) -> Any:
        """永不产出事件的 fake agent：astream 进入即 sleep，run_agent
        因此阻塞在 async for，bridge._queue 永远没有事件 → stream_chat_response
        阻塞在 await bridge._queue.get()，给消费方取消以可观察窗口。"""

        async def _astream(self, *args: Any, **kwargs: Any):
            await asyncio.sleep(3600)  # 远超测试时长，确保取消窗口稳定
            yield  # 占位：使函数成为 async generator（执行不到）

        class _A:
            astream = _astream

            async def aget_state(self, config: Any = None) -> Any:  # 执行不到
                ...

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

    gen = sse_module.stream_chat_response(bridge, chat_req)

    async def _consume() -> list[str]:
        """拉取 generator 帧直到被取消"""
        frames: list[str] = []
        async for frame in gen:
            frames.append(frame)
        return frames

    consumer = asyncio.create_task(_consume())
    # 让 consumer 进入 generator 内部：ContextVar 已 set、agent_task 已 create、
    # 并阻塞在 await bridge._queue.get()（run_agent 内 fake agent 在 sleep）
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
    # 4. ContextVar 已重置：stream_chat_response 的 set/reset 必须在 generator 上下文
    #    成对（避免「Token was created in a different Context」）。bridge_var 在
    #    generator 的 copy_context 内 set/reset，主上下文 get() 应返回默认值
    #    （未 set 状态），不指向本 bridge
    sentinel = object()
    got = bridge_var.get(sentinel)
    assert got is sentinel or got is not bridge, "ContextVar 未在 generator finally 中 reset，主上下文仍可见本 bridge"


async def test_stream_chat_response_cancels_agent_task_on_done(monkeypatch) -> None:
    """正常终止路径：done 帧后 finally 仍 cancel agent_task（对已完成 task 是 no-op）+ unregister

    与上一个断开测试互补：验证终态路径同样清理 session（防 session 泄漏的另一面）。
    用直接驱动 generator 方式而非 HTTP，聚焦 finally 清理副作用。
    """
    monkeypatch.setattr(sse_module, "_get_or_create_agent", _factory_done)

    thread_id = "done-thread-1"
    bridge = sse_module.session_manager.register(thread_id)
    chat_req = sse_module.ChatRequest(
        content="添加文昌地面站",
        thread_id=thread_id,
        message_id="m-done",
        current_scene_name=None,
    )

    frames: list[str] = []
    async for frame in sse_module.stream_chat_response(bridge, chat_req):
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
        → stream_chat_response 消费到 error 帧 → 在 TERMINAL_EVENTS 中 → break 关闭流
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
    转成 error 帧。走真实 stream_chat_response 路径，断言 error 为终态帧 + session 注销。
    """
    from space_aiagent.bridge import bridge_var

    async def _timeout_factory(_thread_id: str) -> Any:
        """fake agent：astream 内调 bridge.send_tool_call（短 timeout），

        触发 TimeoutError，由 run_agent 的 except 捕获 emit error 帧。
        bridge_var 由 stream_chat_response set（在 generator 的 copy context），
        asyncio.create_task 拷贝该 context → agent_task 内 send_tool_call 能拿到 bridge。
        """

        async def _astream(self, *args: Any, **kwargs: Any):
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
            astream = _astream

            async def aget_state(self, config: Any = None) -> Any:  # 执行不到
                ...

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
    async for frame in sse_module.stream_chat_response(sse_module.session_manager.register(thread_id), chat_req):
        frames.append(frame)

    parsed = _parse_sse_frames("".join(frames))
    events = [ev for ev, _ in parsed]
    # tool_start / tool_args（send_tool_call emit）+ error（run_agent except emit）
    assert "error" in events, f"超时未转为 error 终态帧，实际事件: {events}"
    assert events[-1] == "error"
    # 终态后 session 注销 + bridge cleanup
    assert sse_module.session_manager.get_bridge(thread_id) is None
