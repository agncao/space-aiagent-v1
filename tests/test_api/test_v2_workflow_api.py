import json

from space_aiagent.api import routes
from space_aiagent.workflow.models import (
    ArtifactRef,
    PlanStep,
    ResultRef,
    RunResult,
    RunStatus,
    SceneContext,
    StepResult,
    StepStatus,
    ToolExecution,
    WaitingContext,
    WorkflowRun,
)
from space_aiagent.workflow.repository import SqliteRunRepository


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = json.loads(next(line.removeprefix("data: ") for line in lines if line.startswith("data: ")))
        events.append((event, data))
    return events


class FakeEngine:
    def __init__(self, run: WorkflowRun) -> None:
        self.run = run

    async def create_run(self, **kwargs):
        return self.run

    async def resume_run(self, run_id, **kwargs):
        return self.run

    async def cancel_run(self, run_id):
        self.run.status = RunStatus.CANCELLED
        return self.run


async def test_v2_chat_done_contains_workflow_correlation(client, monkeypatch, tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    run = WorkflowRun(
        run_id="run_api",
        thread_id="thread_api",
        original_intent="测试",
        status=RunStatus.SUCCEEDED,
        revision=3,
        final_result=RunResult(status=RunStatus.SUCCEEDED, summary="完成"),
    )

    async def fake_repository():
        return repository

    async def fake_engine():
        return FakeEngine(run)

    monkeypatch.setattr(routes, "get_run_repository", fake_repository)
    monkeypatch.setattr(routes, "_get_engine", fake_engine)
    response = await client.post(
        "/api/v2/space/chat",
        json={"content": "测试", "thread_id": "thread_api", "scene_revision": 0},
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    done = events[-1][1]
    assert done["run_id"] == "run_api"
    assert done["revision"] == 3
    assert done["seq"] == 1
    assert done["timestamp"]


async def test_v2_waiting_run_emits_interrupt_then_done(client, monkeypatch, tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    run = WorkflowRun(
        run_id="run_wait",
        thread_id="thread_wait",
        original_intent="添加实体",
        status=RunStatus.WAITING_USER,
        waiting_context=WaitingContext(
            kind="missing_precondition",
            step_id="step_1",
            prompt="请选择场景",
            data={"choices": ["open_scene", "create_scene"]},
        ),
    )

    async def fake_repository():
        return repository

    async def fake_engine():
        return FakeEngine(run)

    monkeypatch.setattr(routes, "get_run_repository", fake_repository)
    monkeypatch.setattr(routes, "_get_engine", fake_engine)
    response = await client.post(
        "/api/v2/space/chat",
        json={"content": "添加实体", "thread_id": "thread_wait"},
    )
    events = _sse_events(response.text)
    assert [item[0] for item in events] == ["interrupt", "done"]
    assert events[0][1]["interrupt_type"] == "missing_precondition"
    assert events[1][1]["interrupted"] is True


async def test_v2_snapshot_derives_waiting_data_and_serializes_artifacts(client, monkeypatch, tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    step = PlanStep(
        step_id="step_select",
        action="open_scene",
        title="选择场景",
        executor="scene-agent",
        status=StepStatus.WAITING_USER,
        result=StepResult(
            status="waiting_user",
            code="SCENE_QUERIED",
            summary="请选择",
            data=[{"scene_name": "火箭场景A"}, {"scene_name": "火箭场景B"}],
            artifacts=[
                ArtifactRef(
                    artifact_id="artifact-1",
                    kind="report",
                    name="候选报告",
                    uri="/artifacts/artifact-1",
                )
            ],
        ),
    )
    run = WorkflowRun(
        run_id="run_snapshot",
        thread_id="thread_snapshot",
        original_intent="打开场景",
        status=RunStatus.WAITING_USER,
        steps=[step],
        waiting_context=WaitingContext(
            kind="scene_selection",
            step_id=step.step_id,
            prompt="请选择",
            result_ref=ResultRef(source_step_id=step.step_id, pointer="/data"),
            data={"code": "SCENE_QUERIED"},
        ),
    )
    await repository.create_run(run)

    async def fake_repository():
        return repository

    monkeypatch.setattr(routes, "get_run_repository", fake_repository)
    response = await client.get(f"/api/v2/space/runs/{run.run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["waiting_context"]["resolved_data"] == step.result.data
    assert payload["waiting_context"]["data"] == {"code": "SCENE_QUERIED"}
    assert payload["steps"][0]["result"]["artifacts"][0]["uri"] == "/artifacts/artifact-1"

    persisted = await repository.get_run(run.run_id)
    assert persisted is not None
    assert "resolved_data" not in persisted.waiting_context.model_dump(mode="json")


async def test_v2_tool_result_is_persisted_before_late_ack(client, monkeypatch, tmp_path) -> None:
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    run = WorkflowRun(
        run_id="run_tool",
        thread_id="thread_tool",
        original_intent="添加",
        scene_context=SceneContext(status="opened", scene_name="场景A", revision=2),
    )
    await repository.create_run(run)
    execution = ToolExecution(
        execution_id="exec_1",
        run_id=run.run_id,
        step_id="step_1",
        tool_call_id="call_1",
        idempotency_key="idem_1",
        fingerprint="fp",
        tool_func="addPointEntity",
        args={"name": "文昌地面站"},
    )
    await repository.start_tool_execution(execution)

    async def fake_repository():
        return repository

    monkeypatch.setattr(routes, "get_run_repository", fake_repository)
    payload = {
        "thread_id": run.thread_id,
        "run_id": run.run_id,
        "step_id": "step_1",
        "execution_id": "exec_1",
        "tool_call_id": "call_1",
        "idempotency_key": "idem_1",
        "tool_func": "addPointEntity",
        "args": {"name": "文昌地面站"},
        "success": True,
        "code": "ENTITY_CREATED",
        "message": "ok",
        "data": {"entity_name": "文昌地面站"},
        "scene_name": "场景A",
        "scene_revision": 2,
    }
    response = await client.post("/api/v2/space/tool-result", json=payload)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "resolved": False}
    persisted = await repository.get_tool_execution_by_call_id("call_1")
    assert persisted is not None
    assert persisted.status == "succeeded"

    duplicate = await client.post("/api/v2/space/tool-result", json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json() == {"ok": True, "deduplicated": True}
