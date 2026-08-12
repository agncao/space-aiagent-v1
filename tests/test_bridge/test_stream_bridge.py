"""StreamBridge 单测

V2 SSE 远程工具桥接：
- 用 asyncio.Queue 作为事件出口
- Future / resolve / cleanup / 超时语义
- send_tool_call 内部依次 emit tool_start → tool_args → tool_result → tool_end
"""

import asyncio

import pytest

from space_aiagent.bridge.stream_bridge import StreamBridge


def _drain_queue(queue: asyncio.Queue) -> list[dict]:
    """同步抽干 queue 中所有已入队事件（不阻塞）"""
    items: list[dict] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def test_send_tool_call_emits_full_lifecycle():
    """正常路径：tool_start → tool_args → tool_result → tool_end 顺序 emit，且 send_tool_call 返回 resolve 结果"""
    bridge = StreamBridge("thread-1")

    # 在另一协程里轮询 pending，模拟 POST /tool-result 持久化后的唤醒路径。
    async def _resolve():
        while not bridge._pending:
            await asyncio.sleep(0.001)
        tool_call_id = next(iter(bridge._pending))
        bridge.resolve_tool_result_dict(
            tool_call_id,
            {
                "args": {"name": "测试场景"},
                "success": True,
                "message": "ok",
                "data": {"id": 1},
                "code": "",
            },
        )

    task = asyncio.create_task(_resolve())
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    result = await bridge.send_tool_call(
        namespace="scene_tools",
        tool_func="createScenario",
        args={"name": "测试场景"},
        timeout=1.0,
    )

    # 返回值即 Future resolve 的规范化结果。
    assert result == {
        "args": {"name": "测试场景"},
        "success": True,
        "message": "ok",
        "data": {"id": 1},
        "code": "",
    }

    # 队列按顺序收到 4 个事件
    events = _drain_queue(bridge._queue)
    assert [e["event"] for e in events] == ["tool_start", "tool_args", "tool_result", "tool_end"]

    # tool_start：含 tool_func / namespace / tool_call_id / thread_id
    start_data = events[0]["data"]
    assert start_data["tool_func"] == "createScenario"
    assert start_data["namespace"] == "scene_tools"
    assert start_data["tool_call_id"]
    assert start_data["thread_id"] == "thread-1"

    # tool_args：含 args
    args_data = events[1]["data"]
    assert args_data["args"] == {"name": "测试场景"}
    assert args_data["tool_call_id"] == start_data["tool_call_id"]

    # tool_result：含 Future resolve 的规范化结果。
    result_data = events[2]["data"]
    assert result_data["result"] == {
        "args": {"name": "测试场景"},
        "success": True,
        "message": "ok",
        "data": {"id": 1},
        "code": "",
    }

    # tool_end：仅 tool_func + tool_call_id + thread_id
    end_data = events[3]["data"]
    assert end_data["tool_func"] == "createScenario"
    assert end_data["tool_call_id"] == start_data["tool_call_id"]

    # pending 已清理
    assert len(bridge._pending) == 0


async def test_resolve_tool_result():
    """注册的 Future 被规范化结果唤醒。"""
    bridge = StreamBridge("thread-1")

    # 构造一个 pending future
    future = asyncio.get_running_loop().create_future()
    bridge._pending["call-123"] = future

    result = {
        "args": {"name": "x"},
        "success": True,
        "message": "created",
        "data": {"id": 7},
        "code": "SCENE_CREATED",
    }
    bridge.resolve_tool_result_dict("call-123", result)

    assert future.done()
    assert future.result() == result
    # resolve 后 pending 中已 pop
    assert "call-123" not in bridge._pending


async def test_resolve_unknown_id():
    """resolve 未注册的 tool_call_id：不抛异常（仅 warning 日志）"""
    bridge = StreamBridge("thread-1")

    # 不应抛异常
    bridge.resolve_tool_result_dict("unknown-id", {"success": True})

    # pending 仍为空
    assert len(bridge._pending) == 0


async def test_cleanup_closes_emit():
    """cleanup 后：_emit 不再入队；pending futures 被设置 ConnectionError；_closed 标志为 True"""
    bridge = StreamBridge("thread-1")

    # 注册一个 pending future
    future = asyncio.get_running_loop().create_future()
    bridge._pending["call-abc"] = future

    bridge.cleanup()

    # 标志置位
    assert bridge._closed is True

    # pending future 被设置 ConnectionError
    assert future.done()
    with pytest.raises(ConnectionError):
        future.result()

    # pending 已清空
    assert len(bridge._pending) == 0

    # cleanup 后 _emit 不再入队
    await bridge._emit("tool_start", {"tool_func": "x", "tool_call_id": "y"})
    assert bridge._queue.empty()


async def test_timeout_raises():
    """超时：pending future 永不 resolve → send_tool_call 抛 asyncio.TimeoutError，且 tool_call_id 从 _pending 移除"""
    bridge = StreamBridge("thread-1")

    with pytest.raises(asyncio.TimeoutError):
        await bridge.send_tool_call(
            namespace="scene_tools",
            tool_func="createScenario",
            args={},
            timeout=0.01,
        )

    # 超时后 pending 必须清理
    assert len(bridge._pending) == 0

    # 队列里只应有 tool_start / tool_args（result / end 因超时未到达而不 emit）
    events = _drain_queue(bridge._queue)
    assert [e["event"] for e in events] == ["tool_start", "tool_args"]


async def test_cleanup_during_inflight_send_raises_connection_error():
    """cleanup 在 send_tool_call await pending Future 期间触发：

    T3 的 SSE event_generator finally 块依赖此契约——客户端断流时 cleanup() 必须让
    正在 await 的 send_tool_call 立即抛 ConnectionError（既不 hang 也不吞异常），
    且 _pending 最终为空。
    """
    bridge = StreamBridge("thread-1")

    async def call():
        with pytest.raises(ConnectionError):
            await bridge.send_tool_call(
                namespace="scene_tools",
                tool_func="createScenario",
                args={},
                timeout=5.0,
            )

    task = asyncio.create_task(call())
    # 让 call 协程跑到 await wait_for(future) 处，Future 已注册到 _pending
    await asyncio.sleep(0.05)
    bridge.cleanup()
    await task

    assert len(bridge._pending) == 0
