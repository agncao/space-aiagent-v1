import pytest

from space_aiagent.models.workflow_schemas import (
    PlanStep,
    ResultRef,
    StepResult,
    StepStatus,
    WorkerTodoSource,
    WorkflowRun,
)
from space_aiagent.workflow.result_resolver import ResultReferenceError, resolve_result_reference


def _run() -> WorkflowRun:
    step = PlanStep(
        step_id="step_query",
        worker="scene-agent",
        task="查询场景",
        source=WorkerTodoSource.USER_INTENT,
        status=StepStatus.SUCCEEDED,
        result=StepResult(
            status="success",
            code="SCENE_QUERIED",
            summary="查询完成",
            data=[{"scene_name": "火箭场景"}],
        ),
    )
    return WorkflowRun(
        run_id="run_1",
        thread_id="thread_1",
        original_intent="查询场景",
        steps=[step],
    )


def test_waiting_result_reference_resolves_json_pointer() -> None:
    value = resolve_result_reference(
        _run(),
        ResultRef(source_step_id="step_query", pointer="/data/0/scene_name"),
        require_source_success=True,
    )

    assert value == "火箭场景"


def test_waiting_result_reference_rejects_missing_path() -> None:
    with pytest.raises(ResultReferenceError, match="结果路径不存在"):
        resolve_result_reference(
            _run(),
            ResultRef(source_step_id="step_query", pointer="/data/1"),
            require_source_success=True,
        )
