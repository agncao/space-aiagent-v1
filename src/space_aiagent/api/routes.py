"""V2 确定性工作流 API。"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from space_aiagent.api.transport import format_sse_frame
from space_aiagent.bridge import StreamBridge, bridge_var, session_manager
from space_aiagent.infrastructure.config import get_settings
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.sse_schemas import (
    TERMINAL_EVENTS,
    ChatRequest,
    ResumeRequest,
    SSEEventType,
    ToolResultRequest,
)
from space_aiagent.models.workflow_schemas import RunStatus, SceneContext, WorkflowRun
from space_aiagent.workflow.engine import WorkflowEngine, get_engine
from space_aiagent.workflow.presentation import waiting_context_snapshot, workflow_run_snapshot
from space_aiagent.workflow.repository import get_run_repository

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v2/space", tags=["space-v2"])

_engine: WorkflowEngine | None = None


def _ensure_enabled() -> None:
    if not get_settings().workflow.enabled:
        raise HTTPException(status_code=503, detail="V2 工作流当前未启用")


def _streaming_response(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _finish_workflow_stream(bridge: StreamBridge, run: WorkflowRun) -> None:
    """工作流执行结束后，根据 run 的最终状态向 SSE 流发送收尾事件。

    两种收尾路径：
    - WAITING_USER：发送 INTERRUPT 事件（携带等待上下文）→ 发送 DONE(interrupted=True) → 流关闭
    - 其他终端状态：发送 DONE 事件（携带 final_result 摘要）→ 流关闭
    """
    # 将 run 信息回写到 bridge，供 finally 清理时使用
    bridge.set_workflow_run(run.run_id)
    bridge.set_workflow_revision(run.revision)

    if run.status == RunStatus.WAITING_USER and run.waiting_context:
        waiting = run.waiting_context
        # 生成 waiting_context 快照，解析关联的前序步骤结果
        waiting_payload = waiting_context_snapshot(run) or {}
        # 推送 INTERRUPT 事件，前端据此展示等待提示（如确认框、参数输入等）
        await bridge._emit(
            SSEEventType.INTERRUPT,
            {
                "is_custom": True,
                "interrupt_type": waiting.kind,
                "message": waiting.prompt,
                "data": {
                    **waiting.data,
                    "result_ref": waiting_payload.get("result_ref"),
                    "resolved_data": waiting_payload.get("resolved_data"),
                },
                "step_id": waiting.step_id,
            },
        )
        # DONE 事件标记 interrupted=True，前端以此区分"等待中"和"真正结束"
        await bridge._emit(SSEEventType.DONE, {"content": "", "interrupted": True})
        return

    # 终端状态（SUCCEEDED / FAILED / CANCELLED 等）：发送最终结果
    result = run.final_result.model_dump(mode="json") if run.final_result else None
    if run.status == RunStatus.CANCELLED:
        content = "任务已取消。"
    elif run.final_result:
        content = run.final_result.summary
    else:
        content = "任务已结束。"
    await bridge._emit(
        SSEEventType.DONE,
        {"content": content, "status": run.status.value, "result": result},
    )


async def _run_and_emit(
    bridge: StreamBridge,
    operation: Callable[[], Awaitable[WorkflowRun]],
) -> None:
    try:
        run = await operation()
        await _finish_workflow_stream(bridge, run)
    except Exception as exc:
        logger.exception("V2 工作流执行失败", thread_id=bridge._thread_id, run_id=bridge.run_id)
        with contextlib.suppress(Exception):
            await bridge._emit(SSEEventType.ERROR, {"message": str(exc)})


async def _stream_workflow_response(
    bridge: StreamBridge,
    operation: Callable[[], Awaitable[WorkflowRun]],
) -> AsyncIterator[str]:
    """将工作流执行结果以 SSE 帧流式输出。

    核心流程：
    1. 将 bridge 绑定到 contextvar，供下游异步调用访问
    2. 在后台 task 中执行工作流，结果通过 bridge._queue 传递
    3. 主循环从 bridge._queue 消费事件，逐帧 yield 给客户端
    4. finally 保证无论正常结束、客户端断开还是异常，都清理资源
    """
    thread_id = bridge._thread_id
    # 将 bridge 注入 contextvar，使 _run_and_emit 等下游函数能通过 bridge_var.get() 获取当前 bridge
    bridge_token = bridge_var.set(bridge)
    # 后台执行工作流，避免阻塞主循环对 queue 的消费
    workflow_task = asyncio.create_task(_run_and_emit(bridge, operation))
    try:
        while True:
            item = await bridge._queue.get()
            event_name = item["event"]
            yield format_sse_frame(event_name, item["data"])
            if event_name in TERMINAL_EVENTS:
                break
    finally:
        # 客户端断开或异常时，取消仍在运行的后台工作流
        workflow_task.cancel()
        # 等待 task 终止（CancelledError 是预期的，忽略即可）
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await workflow_task
        bridge.cleanup()
        session_manager.unregister(thread_id)
        bridge_var.reset(bridge_token)
        logger.info("V2 SSE 流已关闭", thread_id=thread_id, run_id=bridge.run_id)


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    _ensure_enabled()
    if session_manager.get_bridge(req.thread_id) is not None:
        raise HTTPException(status_code=409, detail="该会话已有活跃请求在处理")

    repository = await get_run_repository()
    engine = await get_engine()
    active = await repository.find_active_by_thread(req.thread_id)

    # ======== start:判断上一轮次如果没有结束(RunState为SUCCEEDED / FAILED)，则只等待用户输入状态，才可恢复执行
    # 1. 如果上一轮次没结束，且用户表示对当前回答不满意，想要"中断"正在进行的对话，用新的输入重新开始一轮
    if active and req.mode == "replace":
        await engine.cancel_run(active.run_id)
        active = None

    # 2. 阻止并发冲突，例如：
    # 用户发送: "分析卫星轨道"  →  Agent 进入 RUNNING 状态（正在计算）
    # 用户又发: "新建场景"      →  409 冲突，因为上一条还没跑完
    if active and active.status != RunStatus.WAITING_USER:
        raise HTTPException(status_code=409, detail={"message": "该会话存在未终结 Run", "run_id": active.run_id})

    # 3. 活跃 Run 处于 WAITING_USER 状态 → 恢复执行，注入用户输入，例如：
    # Agent 询问: "需要确认：是否删除卫星'高分一号'的轨道数据？"  →  Run 进入 WAITING_USER 状态
    # 用户回复: "确认删除"                                       →  恢复 Run，注入 "确认删除"
    if active:
        bridge = session_manager.register(req.thread_id, run_id=active.run_id)
        return _streaming_response(
            _stream_workflow_response(
                bridge,
                lambda: engine.resume_run(active.run_id, user_input=req.content),
            )
        )
    # ======== end:判断上一轮次如果没有结束(RunState为SUCCEEDED / FAILED)，则只等待用户输入状态，才可恢复执行

    # 4. 无活跃 Run → 创建新 Run 并启动执行
    bridge = session_manager.register(req.thread_id)
    scene_context = SceneContext.from_request(
        scene_name=req.current_scene_name,
        revision=req.scene_revision,
    )
    return _streaming_response(
        _stream_workflow_response(
            bridge,
            lambda: engine.create_run(
                thread_id=req.thread_id,
                intent=req.content,
                scene_context=scene_context,
            ),
        )
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "space-aiagent"}


@router.post("/tool-result")
async def tool_result(req: ToolResultRequest) -> dict[str, Any]:
    _ensure_enabled()
    repository = await get_run_repository()
    execution = await repository.get_tool_execution_by_call_id(req.tool_call_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="未知工具调用")

    expected = {
        "run_id": execution.run_id,
        "step_id": execution.step_id,
        "execution_id": execution.execution_id,
        "idempotency_key": execution.idempotency_key,
        "tool_func": execution.tool_func,
    }
    actual = {
        "run_id": req.run_id,
        "step_id": req.step_id,
        "execution_id": req.execution_id,
        "idempotency_key": req.idempotency_key,
        "tool_func": req.tool_func,
    }
    if actual != expected:
        raise HTTPException(status_code=409, detail={"message": "工具回告关联字段不匹配", "expected": expected})

    run = await repository.get_run(req.run_id)
    if run is None or run.thread_id != req.thread_id:
        raise HTTPException(status_code=409, detail="thread_id 与 WorkflowRun 不匹配")
    if req.args != execution.args:
        raise HTTPException(status_code=409, detail="工具回告 args 与原调用不匹配")

    data_scene_name = req.data.get("sceneName") if isinstance(req.data, dict) else None
    normalized_result: dict[str, Any] = {
        "success": req.success,
        "code": req.code,
        "message": req.message,
        "data": req.data,
        "current_scene_name": req.scene_name or data_scene_name,
        "scene_revision": req.scene_revision,
    }
    if execution.result is not None:
        if execution.result != normalized_result:
            raise HTTPException(status_code=409, detail="同一幂等键返回了不同结果")
        return {"ok": True, "deduplicated": True}

    await repository.complete_tool_execution(req.tool_call_id, normalized_result)
    bridge = session_manager.get_bridge(req.thread_id)
    resolved = False
    if bridge is not None and bridge.run_id == req.run_id:
        resolved = bridge.resolve_tool_result_dict(req.tool_call_id, normalized_result)
    logger.info(
        "V2 tool-result 已持久化",
        thread_id=req.thread_id,
        run_id=req.run_id,
        step_id=req.step_id,
        tool_call_id=req.tool_call_id,
        resolved=resolved,
    )
    return {"ok": True, "resolved": resolved}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    _ensure_enabled()
    repository = await get_run_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="WorkflowRun 不存在")
    return workflow_run_snapshot(run)


@router.post("/runs/{run_id}/resume")
async def resume(run_id: str, req: ResumeRequest) -> StreamingResponse:
    _ensure_enabled()
    repository = await get_run_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="WorkflowRun 不存在")
    if session_manager.get_bridge(run.thread_id) is not None:
        raise HTTPException(status_code=409, detail="该会话已有活跃请求在处理")
    if run.status != RunStatus.WAITING_USER:
        raise HTTPException(status_code=409, detail="WorkflowRun 当前不等待用户输入")
    engine = await get_engine()
    bridge = session_manager.register(run.thread_id, run_id=run.run_id)
    return _streaming_response(
        _stream_workflow_response(
            bridge,
            lambda: engine.resume_run(run.run_id, user_input=req.user_input, data=req.data),
        )
    )


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str) -> dict[str, Any]:
    _ensure_enabled()
    repository = await get_run_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="WorkflowRun 不存在")
    if session_manager.get_bridge(run.thread_id) is not None:
        raise HTTPException(status_code=409, detail="活跃执行中不能同步取消，请先中止 SSE 请求")
    engine = await get_engine()
    cancelled = await engine.cancel_run(run_id)
    return workflow_run_snapshot(cancelled)
