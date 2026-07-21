"""Stream 远程工具桥接（SSE 迁移 T1）

与 WSBridge 的关系：StreamBridge 把 WSBridge 的 WebSocket 出口（self._ws.send_json）
替换为 asyncio.Queue 事件出口（_emit → _queue.put）。Future / resolve / cleanup /
超时语义原样保留（与 ws_bridge.py 等价）。

事件协议（T1 用字符串字面量，T2 抽到独立 SSE 事件类型模块）：
- tool_start:  send_tool_call 入口
- tool_args:   紧随 tool_start
- tool_result: Future resolve 后
- tool_end:    send_tool_call 返回前

时序（与 WSBridge 一致）:
    Agent 调用工具 → send_tool_call() → emit tool_start/tool_args + await Future
                                                                    ↑
    Agent 得到结果 ← await future ← resolve_tool_result() ← POST /tool-result handler
                              ↓
                          emit tool_result / tool_end
"""

import asyncio
import uuid

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.messages import ToolResultMessage

logger = get_logger(__name__)


class StreamBridge:
    """SSE 事件流远程工具桥接

    持 asyncio.Queue 作为事件出口，替代 WSBridge 的 WebSocket。
    工具函数（scene/entity/orbit）调 ``bridge.send_tool_call(...)`` 的契约与
    WSBridge 完全一致（同名、同参、同返回），只是内部把 tool_* 事件 emit 到 queue，
    由 SSE handler 消费。
    """

    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id
        # tool_call_id -> Future（与 WSBridge 一致）
        self._pending: dict[str, asyncio.Future] = {}
        # 默认超时时间（秒，与 WSBridge 一致）
        self._timeout = 60
        # 新增：事件出口队列，SSE handler 消费
        self._queue: asyncio.Queue = asyncio.Queue()
        # cleanup 后置 True，_emit 不再入队
        self._closed = False

    async def _emit(self, event: str, data: dict) -> None:
        """把事件入队（thread_id 自动注入到 data）。

        cleanup 后静默丢弃（已关闭的流不再 emit）。
        """
        if self._closed:
            return
        await self._queue.put({"event": event, "data": {**data, "thread_id": self._thread_id}})

    async def send_tool_call(
        self,
        namespace: str,
        tool_func: str,
        args: dict,
        timeout: float = 60,
    ) -> dict:
        """发送工具调用指令到前端（经 SSE emit），等待执行结果

        Args:
            namespace: 前端工具函数所在的命名空间, 例如:
                scene_tools.createScenario，scene_tools 为命名空间
            tool_func: 前端工具函数名（如 "createScenario"）
            args: 工具参数
            timeout: 等待超时时间（秒）

        Returns:
            前端执行结果 dict，格式: {"success": bool, "message": str, "data": ...}

        Raises:
            asyncio.TimeoutError: 等待结果超时（Phase 1B 行为，由 RetryMiddleware 捕获重试）
        """
        # 生成唯一调用 ID，绑定到 Future
        tool_call_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[tool_call_id] = future

        # emit 工具调用启动 + 参数（替代 WSBridge 的 ws.send_json(ToolCallMessage)）
        await self._emit(
            "tool_start",
            {"tool_func": tool_func, "namespace": namespace, "tool_call_id": tool_call_id},
        )
        await self._emit(
            "tool_args",
            {"tool_func": tool_func, "tool_call_id": tool_call_id, "args": args},
        )
        logger.debug(
            "emit tool_call",
            tool_func=tool_func,
            args=args,
            tool_call_id=tool_call_id,
            thread_id=self._thread_id,
        )

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            # Phase 1B：超时抛 asyncio.TimeoutError（不吞成 {success:false} dict），
            # 由 RetryMiddleware.awrap_tool_call 捕获并退避重试
            self._pending.pop(tool_call_id, None)
            logger.warning(
                "tool_call 超时",
                tool_func=tool_func,
                tool_call_id=tool_call_id,
                thread_id=self._thread_id,
            )
            raise

        # 结果到达，emit tool_result + tool_end
        await self._emit(
            "tool_result",
            {"tool_func": tool_func, "tool_call_id": tool_call_id, "result": result},
        )
        await self._emit(
            "tool_end",
            {"tool_func": tool_func, "tool_call_id": tool_call_id},
        )
        logger.debug(
            "emit tool_result",
            tool_call_id=tool_call_id,
            thread_id=self._thread_id,
            success=result.get("success"),
        )
        return result

    def resolve_tool_result(self, result: ToolResultMessage) -> None:
        """解析前端返回的工具执行结果

        由 POST /tool-result handler 在收到 tool_result 时调用，
        根据 tool_call_id 找到对应 Future 并 resolve。

        与 WSBridge.resolve_tool_result 等价：排除 type / thread_id / tool_call_id /
        tool_func 字段后设置 future 结果；未知 id 仅 warning（不抛异常）。
        """
        tool_call_id = result.tool_call_id
        future = self._pending.pop(tool_call_id, None)
        if future and not future.done():
            future.set_result(result.model_dump(exclude={"type", "thread_id", "tool_call_id", "tool_func"}))
        else:
            logger.warning("收到未知 tool_result", tool_call_id=tool_call_id)

    def cleanup(self) -> None:
        """清理所有 pending futures 并关闭事件出口

        在 SSE 流终止时调用（done/error 后或客户端断开），对所有未完成的 Future
        设置 ConnectionError；置 _closed=True 使后续 _emit 静默丢弃。
        """
        for _, future in self._pending.items():
            if not future.done():
                future.set_exception(ConnectionError("Stream 已关闭"))
        count = len(self._pending)
        self._pending.clear()
        self._closed = True
        logger.debug("stream bridge cleanup", cleaned_count=count, thread_id=self._thread_id)
