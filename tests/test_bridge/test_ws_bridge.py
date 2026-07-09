"""bridge send_tool_call 超时行为测试

Phase 1B：超时改抛 asyncio.TimeoutError（不再返回 {success:false} dict），
由 RetryMiddleware 捕获重试。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from space_aiagent.bridge.ws_bridge import WSBridge


async def test_send_tool_call_timeout_raises_timeout_error():
    """超时 → 抛 asyncio.TimeoutError，pending 已清理"""
    ws = AsyncMock()
    bridge = WSBridge(ws, "thread-1")

    with pytest.raises(asyncio.TimeoutError):
        await bridge.send_tool_call("createScenario", {}, timeout=0.01)

    # 超时后 pending 必须清理，避免 future 泄漏
    assert len(bridge._pending) == 0


async def test_send_tool_call_success_returns_result():
    """正常返回 → 返回前端结果 dict"""
    ws = AsyncMock()
    bridge = WSBridge(ws, "thread-1")

    # 在另一个协程里 resolve future
    async def _resolve():
        # 轮询等 pending future 出现（send_tool_call 内才注册，避免时序竞争）
        while not bridge._pending:
            await asyncio.sleep(0.001)
        for _fid, fut in list(bridge._pending.items()):
            fut.set_result({"success": True, "data": "ok"})

    task = asyncio.create_task(_resolve())
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    result = await bridge.send_tool_call("createScenario", {}, timeout=1.0)
    assert result["success"] is True
