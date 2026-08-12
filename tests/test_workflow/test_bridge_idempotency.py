import asyncio

import pytest

from space_aiagent.bridge.stream_bridge import StreamBridge
from space_aiagent.workflow.models import WorkflowRun
from space_aiagent.workflow.repository import SqliteRunRepository


async def test_v2_bridge_reuses_persisted_side_effect_result(tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    await repository.create_run(WorkflowRun(run_id="run_1", thread_id="thread_1", original_intent="测试"))
    bridge = StreamBridge("thread_1", run_id="run_1")
    bridge.set_workflow_execution(
        run_id="run_1",
        step_id="step_1",
        execution_id="exec_1",
        scene_revision=1,
        repository=repository,
    )
    args = {"name": "文昌地面站"}
    first = asyncio.create_task(bridge.send_tool_call("entity_tools", "addPointEntity", args, timeout=1))
    start = await bridge._queue.get()
    await bridge._queue.get()
    tool_call_id = start["data"]["tool_call_id"]
    idempotency_key = start["data"]["idempotency_key"]
    persisted = {"success": True, "code": "ENTITY_CREATED", "message": "ok"}
    await repository.complete_tool_execution(tool_call_id, persisted)
    assert bridge.resolve_tool_result_dict(tool_call_id, persisted) is True
    assert await first == persisted
    await bridge._queue.get()
    await bridge._queue.get()

    second = await bridge.send_tool_call("entity_tools", "addPointEntity", args, timeout=1)
    assert second == persisted
    repeated_start = await bridge._queue.get()
    assert repeated_start["data"]["tool_call_id"] == tool_call_id
    assert repeated_start["data"]["idempotency_key"] == idempotency_key
    assert repeated_start["data"]["deduplicated"] is True


async def test_v2_bridge_lost_ack_retry_uses_persisted_result(tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    await repository.create_run(WorkflowRun(run_id="run_1", thread_id="thread_1", original_intent="测试"))
    bridge = StreamBridge("thread_1", run_id="run_1")
    bridge.set_workflow_execution(
        run_id="run_1",
        step_id="step_1",
        execution_id="exec_1",
        scene_revision=1,
        repository=repository,
    )
    args = {"name": "文昌地面站"}
    with pytest.raises(asyncio.TimeoutError):
        await bridge.send_tool_call("entity_tools", "addPointEntity", args, timeout=0.01)
    start = await bridge._queue.get()
    await bridge._queue.get()
    tool_call_id = start["data"]["tool_call_id"]

    late_result = {"success": True, "code": "ENTITY_CREATED", "message": "late ack"}
    await repository.complete_tool_execution(tool_call_id, late_result)
    retried = await bridge.send_tool_call("entity_tools", "addPointEntity", args, timeout=0.01)
    assert retried == late_result
    retry_start = await bridge._queue.get()
    assert retry_start["data"]["tool_call_id"] == tool_call_id
    assert retry_start["data"]["deduplicated"] is True
