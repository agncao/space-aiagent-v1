import pytest

from space_aiagent.models.workflow_schemas import (
    DraftStep,
    PlanDraft,
    RunStatus,
    StepResult,
    StepStatus,
    WorkerTodoSource,
    WorkflowRun,
)
from space_aiagent.workflow.catalog import WorkerCatalog
from space_aiagent.workflow.scheduler import FinalizationGuard, Scheduler
from space_aiagent.workflow.validator import PlanValidationError, PlanValidator


def _validator() -> PlanValidator:
    return PlanValidator(WorkerCatalog.from_yaml())


def _todo(
    ref: str,
    worker: str,
    task: str,
    *,
    source: WorkerTodoSource = WorkerTodoSource.USER_INTENT,
    depends_on: list[str] | None = None,
) -> DraftStep:
    return DraftStep(
        ref=ref,
        worker=worker,
        task=task,
        source=source,
        depends_on=depends_on or [],
    )


def test_validator_preserves_worker_todo_dependency() -> None:
    draft = PlanDraft(
        goal="打开再添加",
        todos=[
            _todo("open", "scene-agent", "打开火箭场景"),
            _todo("add", "entity-agent", "添加文昌地面站", depends_on=["open"]),
        ],
    )

    steps = _validator().validate(draft, expected_source=WorkerTodoSource.USER_INTENT)

    assert [item.worker for item in steps] == ["scene-agent", "entity-agent"]
    assert steps[1].depends_on == [steps[0].step_id]
    assert all(item.source == WorkerTodoSource.USER_INTENT for item in steps)


def test_validator_marks_generated_requirement_todos() -> None:
    draft = PlanDraft(
        goal="满足场景前置条件",
        todos=[
            _todo(
                "scene",
                "scene-agent",
                "确保存在已打开场景",
                source=WorkerTodoSource.REQUIREMENT,
            )
        ],
    )

    steps = _validator().validate(
        draft,
        expected_source=WorkerTodoSource.REQUIREMENT,
        generated_for_step_id="step_parent",
        requirement_key="scene.opened",
        inherited_dependencies=["step_previous"],
        dependency_depth=1,
    )

    assert steps[0].generated_for_step_id == "step_parent"
    assert steps[0].requirement_key == "scene.opened"
    assert steps[0].depends_on == ["step_previous"]
    assert steps[0].dependency_depth == 1


@pytest.mark.parametrize(
    "draft",
    [
        PlanDraft(
            goal="未知 Worker",
            todos=[_todo("one", "unknown-agent", "执行任务")],
        ),
        PlanDraft(
            goal="前向依赖",
            todos=[
                _todo("one", "scene-agent", "任务一", depends_on=["two"]),
                _todo("two", "entity-agent", "任务二"),
            ],
        ),
        PlanDraft(
            goal="错误来源",
            todos=[
                _todo(
                    "one",
                    "scene-agent",
                    "任务一",
                    source=WorkerTodoSource.REQUIREMENT,
                )
            ],
        ),
    ],
)
def test_validator_rejects_invalid_worker_todo(draft: PlanDraft) -> None:
    with pytest.raises(PlanValidationError):
        _validator().validate(draft, expected_source=WorkerTodoSource.USER_INTENT)


def test_scheduler_reactivates_todo_after_dynamic_dependencies_succeed() -> None:
    dependency, blocked = _validator().validate(
        PlanDraft(
            goal="前置后统计",
            todos=[
                _todo("scene", "scene-agent", "打开场景"),
                _todo("count", "entity-agent", "统计实体", depends_on=["scene"]),
            ],
        ),
        expected_source=WorkerTodoSource.USER_INTENT,
    )
    dependency.status = StepStatus.SUCCEEDED
    dependency.result = StepResult(status="success", code="OK", summary="场景已打开")
    blocked.status = StepStatus.WAITING_DEPENDENCY
    run = WorkflowRun(
        run_id="run_1",
        thread_id="thread_1",
        original_intent="统计实体",
        status=RunStatus.RUNNING,
        steps=[dependency, blocked],
    )

    decision = Scheduler().decide(run)

    assert decision.outcome == "execute"
    assert decision.step_id == blocked.step_id
    assert blocked.status == StepStatus.READY


def test_scheduler_blocks_todo_after_dependency_failure() -> None:
    first, second = _validator().validate(
        PlanDraft(
            goal="复合",
            todos=[
                _todo("first", "scene-agent", "打开"),
                _todo("second", "entity-agent", "统计", depends_on=["first"]),
            ],
        ),
        expected_source=WorkerTodoSource.USER_INTENT,
    )
    first.status = StepStatus.FAILED
    first.result = StepResult(status="failed", code="FAILED", summary="失败")
    run = WorkflowRun(
        run_id="run_1",
        thread_id="thread_1",
        original_intent="复合",
        status=RunStatus.RUNNING,
        steps=[first, second],
    )

    decision = Scheduler().decide(run)

    assert decision.outcome == "finalize"
    assert second.status == StepStatus.BLOCKED


def test_finalizer_fallback_uses_worker_result_instead_of_generic_completion() -> None:
    step = _validator().validate(
        PlanDraft(goal="统计", todos=[_todo("count", "entity-agent", "统计实体数")]),
        expected_source=WorkerTodoSource.USER_INTENT,
    )[0]
    step.status = StepStatus.SUCCEEDED
    step.result = StepResult(
        status="success",
        code="ENTITIES_LIST",
        summary="当前场景共有 3 个实体",
        data={"count": 3},
    )
    run = WorkflowRun(
        run_id="run_1",
        thread_id="thread_1",
        original_intent="统计实体数",
        status=RunStatus.RUNNING,
        steps=[step],
    )

    FinalizationGuard().finalize(run)

    assert run.final_result is not None
    assert run.final_result.summary == "当前场景共有 3 个实体"
