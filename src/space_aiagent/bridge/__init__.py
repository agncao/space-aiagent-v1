"""
远程工具桥接层

核心设计:
由于工具实际在前端 Cesium 中执行，后端需要一种机制：
1. 工具函数调用时，通过 bridge 发送指令到前端
2. 阻塞等待前端返回执行结果（由 POST /tool-result handler resolve）
3. 将结果返回给当前 Worker

实现原理: 使用 asyncio.Future
- 每次工具调用创建一个 Future，绑定到 tool_call_id
- POST /tool-result 收到前端 tool_result 时，根据 tool_call_id 找到对应 Future 并 resolve
- 工具函数 await Future 得到结果

StreamBridge：SSE 事件出口桥接（POST /chat handler 使用），把 tool_* 事件 emit 到
asyncio.Queue 供 SSE handler 消费。
"""

from contextvars import ContextVar

from .session import SessionManager, session_manager
from .stream_bridge import StreamBridge

__all__ = ["SessionManager", "StreamBridge", "bridge_var", "session_manager"]

# Run 级 bridge 由 SSE handler 在启动工作流前通过 bridge_var.set() 注入；
# asyncio.create_task 拷贝当前 context，Worker 与工具函数通过 bridge_var.get() 取得 bridge。
bridge_var: ContextVar[StreamBridge | None] = ContextVar("bridge_var", default=None)
