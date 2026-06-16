"""
远程工具桥接层

核心设计:
由于工具实际在前端 Cesium 中执行，后端需要一种机制：
1. 工具函数调用时，通过 WebSocket 发送指令到前端
2. 阻塞等待前端返回执行结果
3. 将结果返回给 Agent

实现原理: 使用 asyncio.Future
- 每次工具调用创建一个 Future，绑定到 tool_call_id
- WebSocket 收到前端的 tool_result 时，根据 tool_call_id 找到对应 Future 并 resolve
- 工具函数 await Future 得到结果
"""

from contextvars import ContextVar

from .session import SessionManager
from .ws_bridge import WSBridge

__all__ = ["SessionManager", "WSBridge", "bridge_var", "current_scene_name_var"]

# 会话级别的 bridge 实例，由 websocket handler 在创建 Agent 前通过 bridge_var.set() 注入
# 工具函数通过 bridge_var.get() 获取当前会话的 bridge，实现远程工具调用
bridge_var: ContextVar[WSBridge | None] = ContextVar("bridge_var", default=None)

# 会话级别的当前场景名，由 websocket handler 从 UserInputMessage.current_scene_name 注入
# 工具前置校验中间件通过 current_scene_name_var.get() 判断是否有场景上下文
current_scene_name_var: ContextVar[str | None] = ContextVar("current_scene_name_var", default=None)
