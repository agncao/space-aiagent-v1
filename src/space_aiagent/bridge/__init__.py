"""
远程工具桥接层

核心设计:
由于工具实际在前端 Cesium 中执行，后端需要一种机制：
1. 工具函数调用时，通过 bridge 发送指令到前端
2. 阻塞等待前端返回执行结果（由 POST /tool-result handler resolve）
3. 将结果返回给 Agent

实现原理: 使用 asyncio.Future
- 每次工具调用创建一个 Future，绑定到 tool_call_id
- POST /tool-result 收到前端 tool_result 时，根据 tool_call_id 找到对应 Future 并 resolve
- 工具函数 await Future 得到结果

两个 Bridge 实现：
- StreamBridge：SSE 时代的事件出口桥接（POST /chat handler 使用）
- WSBridge：WebSocket 时代的桥接（保留至 WS 路径完全清理，test_ws_bridge.py 仍引用）
"""

from contextvars import ContextVar

from .session import SessionManager
from .stream_bridge import StreamBridge
from .ws_bridge import WSBridge

__all__ = ["SessionManager", "StreamBridge", "WSBridge", "bridge_var"]

# 会话级别的 bridge 实例，由 SSE handler 在创建 Agent 之前通过 bridge_var.set() 注入；
# asyncio.create_task 拷贝当前 context，agent 任务与工具函数通过 bridge_var.get() 取得 bridge。
bridge_var: ContextVar[StreamBridge | WSBridge | None] = ContextVar(
    "bridge_var", default=None
)
