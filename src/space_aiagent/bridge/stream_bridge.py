"""Stream 远程工具桥接（SSE 事件流）

StreamBridge 持 asyncio.Queue 作为事件出口（_emit → _queue.put），SSE handler
（POST /chat 的 event_generator）消费队列转 SSE 帧。Future / resolve / cleanup /
超时语义：每次工具调用建 Future 绑 tool_call_id，POST /tool-result 按 id resolve，
超时抛 asyncio.TimeoutError（由 RetryMiddleware 捕获重试）。

事件协议（事件类型常量在 models/sse_schemas.py:SSEEventType）：
- tool_start:  send_tool_call 入口
- tool_args:   紧随 tool_start
- tool_result: Future resolve 后
- tool_end:    send_tool_call 返回前

时序:
    Agent 调用工具 → send_tool_call() → emit tool_start/tool_args + await Future
                                                                    ↑
    Worker 得到结果 ← await future ← resolve_tool_result_dict() ← POST /tool-result handler
                              ↓
                          emit tool_result / tool_end
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.sse_schemas import SSEEventType
from space_aiagent.workflow.models import ToolExecution

logger = get_logger(__name__)


class StreamBridge:
    """SSE 事件流远程工具桥接

    持 asyncio.Queue 作为事件出口。工具函数（scene/entity/orbit）调
    ``bridge.send_tool_call(...)``（同名、同参、同返回），内部把 tool_* 事件 emit
    到 queue，由 SSE handler（event_generator）消费转 SSE 帧。
    """

    def __init__(self, thread_id: str, run_id: str | None = None) -> None:
        self._thread_id = thread_id
        self._run_id = run_id
        self._sequence = 0
        self._workflow_revision = 0
        self._workflow_execution: dict[str, Any] | None = None
        self._workflow_repository: Any = None
        # tool_call_id -> Future
        self._pending: dict[str, asyncio.Future] = {}
        # 默认超时时间（秒）
        self._timeout = 60
        # 新增：事件出口队列，SSE handler 消费
        self._queue: asyncio.Queue = asyncio.Queue()
        # cleanup 后置 True，_emit 不再入队
        self._closed = False

    @property
    def run_id(self) -> str | None:
        return self._run_id

    def set_workflow_run(self, run_id: str) -> None:
        self._run_id = run_id

    def set_workflow_repository(self, repository: Any) -> None:
        self._workflow_repository = repository

    def set_workflow_revision(self, revision: int) -> None:
        self._workflow_revision = revision

    def set_workflow_execution(
        self,
        *,
        run_id: str,
        step_id: str,
        execution_id: str,
        scene_revision: int,
        repository: Any,
    ) -> None:
        self._run_id = run_id
        self._workflow_repository = repository
        self._workflow_execution = {
            "step_id": step_id,
            "execution_id": execution_id,
            "scene_revision": scene_revision,
        }

    def clear_workflow_execution(self) -> None:
        self._workflow_execution = None

    def pending_workflow_context(self, tool_call_id: str) -> dict[str, Any] | None:
        value = getattr(self, "_pending_context", {}).get(tool_call_id)
        return dict(value) if value else None

    async def _emit(self, event: str, data: dict) -> None:
        """把事件入队（thread_id 自动注入到 data）。

        cleanup 后静默丢弃（已关闭的流不再 emit）。
        """
        if self._closed:
            return
        payload = {**data, "thread_id": self._thread_id}
        if self._run_id:
            if self._workflow_repository is not None:
                self._sequence = await self._workflow_repository.next_sequence(self._run_id)
            else:
                self._sequence += 1
            payload.update(
                {
                    "run_id": self._run_id,
                    "seq": self._sequence,
                    "revision": self._workflow_revision,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            if self._workflow_execution:
                payload.update(
                    {
                        "step_id": self._workflow_execution["step_id"],
                        "execution_id": self._workflow_execution["execution_id"],
                    }
                )
        await self._queue.put({"event": event, "data": payload})

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
        # 工作流调用使用稳定 fingerprint/idempotency_key/tool_call_id，
        # 使超时重发和前端 ACK 丢失不会重复执行副作用；非工作流调用仅供测试工具使用。
        correlation: dict[str, Any] = {}
        if self._workflow_execution and self._run_id and self._workflow_repository is not None:
            canonical_args = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            fingerprint_source = (
                f"{self._workflow_execution['step_id']}|{tool_func}|{canonical_args}|"
                f"{self._workflow_execution['scene_revision']}"
            )
            fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
            idempotency_key = hashlib.sha256(f"{self._run_id}|{fingerprint}".encode()).hexdigest()
            tool_call_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
            correlation = {
                "step_id": self._workflow_execution["step_id"],
                "execution_id": self._workflow_execution["execution_id"],
                "idempotency_key": idempotency_key,
            }
            existing = await self._workflow_repository.get_tool_execution_by_idempotency(idempotency_key)
            logger.debug(
                "查询幂等工具执行",
                idempotency_key=idempotency_key,
                tool_func=tool_func,
                hit=existing is not None,
                status=existing.status if existing else None,
                thread_id=self._thread_id,
            )
            if existing is not None and existing.status in {"succeeded", "failed"} and existing.result is not None:
                await self._emit(
                    SSEEventType.TOOL_START,
                    {
                        "tool_func": tool_func,
                        "namespace": namespace,
                        "tool_call_id": tool_call_id,
                        "idempotency_key": idempotency_key,
                        "deduplicated": True,
                    },
                )
                await self._emit(
                    SSEEventType.TOOL_ARGS,
                    {
                        "tool_func": tool_func,
                        "tool_call_id": tool_call_id,
                        "idempotency_key": idempotency_key,
                        "args": args,
                        "deduplicated": True,
                    },
                )
                await self._emit(
                    SSEEventType.TOOL_RESULT,
                    {
                        "tool_func": tool_func,
                        "tool_call_id": tool_call_id,
                        "idempotency_key": idempotency_key,
                        "result": existing.result,
                        "deduplicated": True,
                    },
                )
                await self._emit(
                    SSEEventType.TOOL_END,
                    {
                        "tool_func": tool_func,
                        "tool_call_id": tool_call_id,
                        "idempotency_key": idempotency_key,
                        "deduplicated": True,
                    },
                )
                return existing.result
            await self._workflow_repository.start_tool_execution(
                ToolExecution(
                    execution_id=self._workflow_execution["execution_id"],
                    run_id=self._run_id,
                    step_id=self._workflow_execution["step_id"],
                    tool_call_id=tool_call_id,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    tool_func=tool_func,
                    args=args,
                )
            )
        else:
            tool_call_id = str(uuid.uuid4())
            logger.debug(
                "非工作流工具调用",
                tool_func=tool_func,
                tool_call_id=tool_call_id,
                thread_id=self._thread_id,
            )
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[tool_call_id] = future
        if not hasattr(self, "_pending_context"):
            self._pending_context: dict[str, dict[str, Any]] = {}
        if correlation:
            self._pending_context[tool_call_id] = {
                **correlation,
                "run_id": self._run_id,
                "tool_func": tool_func,
                "args": args,
            }

        # emit 工具调用启动 + 参数
        await self._emit(
            SSEEventType.TOOL_START,
            {
                "tool_func": tool_func,
                "namespace": namespace,
                "tool_call_id": tool_call_id,
                **({"idempotency_key": correlation["idempotency_key"]} if correlation else {}),
            },
        )
        await self._emit(
            SSEEventType.TOOL_ARGS,
            {
                "tool_func": tool_func,
                "tool_call_id": tool_call_id,
                "args": args,
                **({"idempotency_key": correlation["idempotency_key"]} if correlation else {}),
            },
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
            SSEEventType.TOOL_RESULT,
            {
                "tool_func": tool_func,
                "tool_call_id": tool_call_id,
                "result": result,
                **({"idempotency_key": correlation["idempotency_key"]} if correlation else {}),
            },
        )
        await self._emit(
            SSEEventType.TOOL_END,
            {
                "tool_func": tool_func,
                "tool_call_id": tool_call_id,
                **({"idempotency_key": correlation["idempotency_key"]} if correlation else {}),
            },
        )
        logger.debug(
            "emit tool_result",
            tool_call_id=tool_call_id,
            thread_id=self._thread_id,
            success=result.get("success"),
        )
        return result

    def resolve_tool_result_dict(self, tool_call_id: str, result: dict[str, Any]) -> bool:
        """工具回告持久化后，用规范化结果唤醒对应 Future。"""
        future = self._pending.pop(tool_call_id, None)
        if future and not future.done():
            future.set_result(result)
            return True
        logger.warning("收到无 pending Future 的 tool_result", tool_call_id=tool_call_id)
        return False

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
        if hasattr(self, "_pending_context"):
            self._pending_context.clear()
        self._closed = True
        logger.debug("stream bridge cleanup", cleaned_count=count, thread_id=self._thread_id)
