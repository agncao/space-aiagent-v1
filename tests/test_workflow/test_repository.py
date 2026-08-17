import pytest

from space_aiagent.models.workflow_schemas import ToolExecution, WorkflowRun
from space_aiagent.workflow.repository import ConcurrentRunUpdateError, SqliteRunRepository


async def test_repository_persists_run_and_enforces_revision(tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    run = WorkflowRun(run_id="run_1", thread_id="thread_1", original_intent="测试")
    await repository.create_run(run)

    loaded = await repository.get_run("run_1")
    assert loaded is not None
    assert loaded.revision == 0
    loaded.original_intent = "更新"
    await repository.save_run(loaded, expected_revision=0)
    assert loaded.revision == 1

    with pytest.raises(ConcurrentRunUpdateError):
        await repository.save_run(loaded, expected_revision=0)


async def test_execution_ledger_returns_same_idempotent_result(tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    execution = ToolExecution(
        execution_id="exec_1",
        run_id="run_1",
        step_id="step_1",
        tool_call_id="call_1",
        idempotency_key="idem_1",
        fingerprint="fingerprint",
        tool_func="addPointEntity",
        args={"name": "文昌地面站"},
    )
    await repository.start_tool_execution(execution)
    completed = await repository.complete_tool_execution("call_1", {"success": True, "code": "ENTITY_CREATED"})
    assert completed is not None
    assert completed.status == "succeeded"

    duplicate = await repository.start_tool_execution(execution)
    assert duplicate.status == "succeeded"
    assert duplicate.result == {"success": True, "code": "ENTITY_CREATED"}


async def test_event_sequence_is_monotonic_across_streams(tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    assert await repository.next_sequence("run_1") == 1
    assert await repository.next_sequence("run_1") == 2
    assert await repository.next_sequence("run_2") == 1
