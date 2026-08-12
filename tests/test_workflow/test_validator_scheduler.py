import pytest

from space_aiagent.workflow.catalog import ActionCatalog, ActionDefinition
from space_aiagent.workflow.models import (
    DraftResultRef,
    DraftStep,
    PlanDraft,
    RunStatus,
    SceneContext,
    StepResult,
    StepStatus,
    WorkflowRun,
)
from space_aiagent.workflow.scheduler import FinalizationGuard, Scheduler
from space_aiagent.workflow.validator import PlanValidationError, PlanValidator


def _catalog() -> ActionCatalog:
    return ActionCatalog.from_yaml()


def test_action_catalog_rejects_completion_tool_outside_allowlist() -> None:
    with pytest.raises(ValueError):
        ActionDefinition(
            name="bad",
            description="bad",
            executor="worker",
            allowed_tools=["query"],
            completion_tools=["delete"],
        )


def test_validator_preserves_explicit_scene_then_entity_dependency() -> None:
    draft = PlanDraft(
        goal="打开再添加",
        steps=[
            DraftStep(ref="open", action="open_scene", title="打开火箭场景", args={"scene_name": "火箭"}),
            DraftStep(ref="add", action="add_entity", title="添加文昌地面站", depends_on=["open"]),
        ],
    )
    steps = PlanValidator(_catalog()).validate(draft, SceneContext(status="none"))
    assert [item.action for item in steps] == ["open_scene", "add_entity"]
    assert steps[1].depends_on == [steps[0].step_id]


def test_validator_inserts_scene_precondition_for_entity_only() -> None:
    draft = PlanDraft(
        goal="添加实体",
        steps=[DraftStep(ref="add", action="add_entity", title="添加文昌地面站")],
    )
    steps = PlanValidator(_catalog()).validate(draft, SceneContext(status="none"))
    assert [item.action for item in steps] == ["ensure_scene_context", "add_entity"]
    assert steps[1].depends_on == [steps[0].step_id]


def test_validator_rejects_forward_dependency() -> None:
    draft = PlanDraft(
        goal="非法计划",
        steps=[
            DraftStep(ref="one", action="query_scene", title="一", depends_on=["two"]),
            DraftStep(ref="two", action="query_scene", title="二"),
        ],
    )
    with pytest.raises(PlanValidationError):
        PlanValidator(_catalog()).validate(draft, SceneContext())


def test_validator_keeps_batch_entities_serialized() -> None:
    draft = PlanDraft(
        goal="打开场景并添加两个地面站",
        steps=[
            DraftStep(ref="open", action="open_scene", title="打开场景"),
            DraftStep(ref="wenchang", action="add_entity", title="添加文昌", depends_on=["open"]),
            DraftStep(ref="urumqi", action="add_entity", title="添加乌鲁木齐", depends_on=["wenchang"]),
        ],
    )
    steps = PlanValidator(_catalog()).validate(draft, SceneContext(status="none"))
    assert [item.action for item in steps] == ["open_scene", "add_entity", "add_entity"]
    assert steps[1].depends_on == [steps[0].step_id]
    assert steps[2].depends_on == [steps[1].step_id]


def test_validator_converts_result_binding_and_adds_direct_dependency() -> None:
    draft = PlanDraft(
        goal="查询后复用名称",
        steps=[
            DraftStep(ref="query", action="query_scene", title="查询"),
            DraftStep(
                ref="open",
                action="open_scene",
                title="打开",
                input_bindings={"scene_name": DraftResultRef(source_ref="query", pointer="/data/0/scene_name")},
            ),
        ],
    )

    steps = PlanValidator(_catalog()).validate(draft, SceneContext(status="none"))

    assert steps[1].depends_on == [steps[0].step_id]
    assert steps[1].input_bindings["scene_name"].source_step_id == steps[0].step_id


@pytest.mark.parametrize(
    ("draft", "message"),
    [
        (
            PlanDraft(
                goal="参数冲突",
                steps=[
                    DraftStep(ref="query", action="query_scene", title="查询"),
                    DraftStep(
                        ref="open",
                        action="open_scene",
                        title="打开",
                        args={"scene_name": "A"},
                        input_bindings={"scene_name": DraftResultRef(source_ref="query")},
                    ),
                ],
            ),
            "同时存在于 args 和 input_bindings",
        ),
        (
            PlanDraft(
                goal="非法路径",
                steps=[
                    DraftStep(ref="query", action="query_scene", title="查询"),
                    DraftStep(
                        ref="open",
                        action="open_scene",
                        title="打开",
                        input_bindings={"scene_name": DraftResultRef(source_ref="query", pointer="data/scene_name")},
                    ),
                ],
            ),
            "非法 JSON Pointer",
        ),
        (
            PlanDraft(
                goal="必需引用非必需步骤",
                steps=[
                    DraftStep(ref="query", action="query_scene", title="查询", required=False),
                    DraftStep(
                        ref="open",
                        action="open_scene",
                        title="打开",
                        input_bindings={"scene_name": DraftResultRef(source_ref="query")},
                    ),
                ],
            ),
            "required binding",
        ),
    ],
)
def test_validator_rejects_invalid_result_bindings(draft: PlanDraft, message: str) -> None:
    with pytest.raises(PlanValidationError, match=message):
        PlanValidator(_catalog()).validate(draft, SceneContext(status="none"))


def test_scheduler_waits_for_missing_scene_and_finalizer_rejects_early_end() -> None:
    draft = PlanDraft(goal="添加", steps=[DraftStep(ref="add", action="add_entity", title="添加")])
    run = WorkflowRun(
        run_id="run_1",
        thread_id="thread_1",
        original_intent="添加",
        scene_context=SceneContext(status="none"),
        steps=PlanValidator(_catalog()).validate(draft, SceneContext(status="none")),
        status=RunStatus.RUNNING,
    )
    decision = Scheduler().decide(run)
    assert decision.outcome == "wait"
    assert run.status == RunStatus.WAITING_USER
    assert run.waiting_context is not None
    assert run.waiting_context.kind == "missing_precondition"
    with pytest.raises(RuntimeError):
        FinalizationGuard().finalize(run)


def test_scheduler_blocks_dependent_step_after_failure() -> None:
    draft = PlanDraft(
        goal="复合",
        steps=[
            DraftStep(ref="open", action="open_scene", title="打开"),
            DraftStep(ref="add", action="add_entity", title="添加", depends_on=["open"]),
        ],
    )
    run = WorkflowRun(
        run_id="run_1",
        thread_id="thread_1",
        original_intent="复合",
        steps=PlanValidator(_catalog()).validate(draft, SceneContext(status="none")),
        status=RunStatus.RUNNING,
    )
    run.steps[0].status = StepStatus.FAILED
    run.steps[0].result = StepResult(status="failed", code="FAILED", summary="失败")
    decision = Scheduler().decide(run)
    assert decision.outcome == "finalize"
    assert run.steps[1].status == StepStatus.BLOCKED
