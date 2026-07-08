"""
会话管理器

管理 WebSocket 连接与 thread_id 的映射关系。
每个前端连接对应一个 thread_id，Agent 处理时需要找到对应的 WS 连接发送指令。
"""

from fastapi import WebSocket

from space_aiagent.infrastructure.logging import get_logger

from .ws_bridge import WSBridge

logger = get_logger(__name__)


class SessionManager:
    """
    会话管理器

    管理以下映射关系:
    - thread_id -> WebSocket 连接
    - thread_id -> WSBridge 实例
    """

    def __init__(self) -> None:
        # thread_id -> WebSocket
        self._connections: dict[str, WebSocket] = {}
        # thread_id -> WSBridge
        self._bridges: dict[str, WSBridge] = {}

    def register(self, thread_id: str, websocket: WebSocket) -> WSBridge:
        """
        注册新的 WebSocket 连接

        创建对应的 WSBridge 实例并存储映射关系。
        如果 thread_id 已存在，先清理旧连接。

        Returns:
            新创建的 WSBridge 实例
        """
        # 清理旧连接（同一 thread_id 重新连接的情况）
        if thread_id in self._bridges:
            old_bridge = self._bridges[thread_id]
            old_bridge.cleanup()
            logger.info("旧连接被替换", thread_id=thread_id)

        bridge = WSBridge(websocket, thread_id)
        self._connections[thread_id] = websocket
        self._bridges[thread_id] = bridge
        logger.info("注册新连接", thread_id=thread_id)
        return bridge

    def unregister(self, thread_id: str) -> None:
        """
        注销连接（断开时调用）

        清理 bridge 的 pending futures，并从映射中移除。
        """
        bridge = self._bridges.pop(thread_id, None)
        if bridge:
            bridge.cleanup()

        self._connections.pop(thread_id, None)
        logger.info("注销连接", thread_id=thread_id)

    def get_websocket(self, thread_id: str) -> WebSocket | None:
        """根据 thread_id 获取 WebSocket 连接"""
        return self._connections.get(thread_id)

    def get_bridge(self, thread_id: str) -> WSBridge | None:
        """根据 thread_id 获取 WSBridge 实例"""
        return self._bridges.get(thread_id)
