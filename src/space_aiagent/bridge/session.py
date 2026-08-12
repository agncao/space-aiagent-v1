"""
V2 SSE 会话管理器

管理 StreamBridge 实例与 thread_id 的映射。每个活跃 SSE 会话对应一个 thread_id
与一个 StreamBridge；POST /chat 在 register 时创建，event_generator 终止时
unregister。

并发护栏：register 之前调用 get_bridge 检查是否已有活跃 session（活跃则返 409）。
WS 时代单连接串行处理多轮，SSE+POST 时代每个 POST 独立，需要 SessionManager 守住
「同 thread_id 同时只允许一个 agent 运行」的不变式，避免 checkpointer 冲突。
"""

from space_aiagent.infrastructure.logging import get_logger

from .stream_bridge import StreamBridge

logger = get_logger(__name__)


class SessionManager:
    """会话管理器

    管理映射：thread_id -> StreamBridge。
    """

    def __init__(self) -> None:
        # thread_id -> StreamBridge
        self._bridges: dict[str, StreamBridge] = {}

    def register(self, thread_id: str, *, run_id: str | None = None) -> StreamBridge:
        """注册新会话并创建对应的 StreamBridge 实例

        调用方必须先调 ``get_bridge`` 做并发检查（活跃则 409），再调本方法。
        返回新建的 StreamBridge（事件出口由本 SSE 流消费）。

        Returns:
            新创建的 StreamBridge 实例
        """
        bridge = StreamBridge(thread_id, run_id=run_id)
        self._bridges[thread_id] = bridge
        logger.info("注册新连接", thread_id=thread_id)
        return bridge

    def unregister(self, thread_id: str) -> None:
        """注销会话（SSE 流终止时调用）

        清理 bridge 的 pending futures，并从映射中移除。
        """
        bridge = self._bridges.pop(thread_id, None)
        if bridge:
            bridge.cleanup()
        logger.info("注销连接", thread_id=thread_id)

    def get_bridge(self, thread_id: str) -> StreamBridge | None:
        """根据 thread_id 获取 StreamBridge 实例"""
        return self._bridges.get(thread_id)


session_manager = SessionManager()
